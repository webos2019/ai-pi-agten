"""Steer 控制器 — 统一的流式插话应用逻辑

解决「同一概念两套实现」问题:
  之前 agent_runtime.py 手动调用 _check_steer()（7+ 处），
  agent_loop.py 有 get_steering_messages 钩子但未接线。
  两处各写了一遍 drain → mark → emit chunk → record history。

本模块提供单一入口 SteerController:
  - check_and_apply(): 唯一的 steer 消费 + 应用逻辑
  - format_for_prompt(): 唯一的 prompt 注入逻辑
  - steer_aware_step(): 上下文管理器，防止新增步骤时忘记检查

两种调用模式统一到同一个 controller:
  1. 状态机模式 (agent_runtime): async with steer_aware_step(ctrl, step, action)
  2. 循环模式 (agent_loop): get_steering_messages 钩子内部调 ctrl.check_and_apply()

未来扩展 (权限校验 / 日志 / 限流等) 只需改 SteerController 一处。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from stream import (
    StreamLifecycle,
    create_steer_applied_chunk,
    create_text_chunk,
)
from steer_queue import SteerQueue, SteerEntry


class SteerController:
    """统一的 Steer 控制器 — 封装所有 steer 应用逻辑

    职责:
    1. drain: 从 SteerQueue 消费排队的 steer
    2. mark_applied: 标记已应用 (含 step_index / action_type)
    3. emit_chunk: 发送 steer_applied chunk 通知前端
    4. record_history: 记录到内部 _history 列表
    5. emit_text: 发送可见文本提示用户 steer 已接收
    6. format_for_prompt: 格式化 history 为 prompt 注入文本

    所有行为集中在此类，修改 steer 逻辑只需改一处。
    """

    def __init__(
        self,
        steer_queue: SteerQueue | None,
        lifecycle: StreamLifecycle,
    ) -> None:
        self._queue = steer_queue
        self._lifecycle = lifecycle
        self._history: list[str] = []

    @property
    def history(self) -> list[str]:
        """已应用的 steer 文本列表 (按时间顺序)"""
        return self._history

    @property
    def has_history(self) -> bool:
        """是否有已应用的 steer"""
        return len(self._history) > 0

    async def check_and_apply(
        self,
        step_index: int,
        action_type: str,
    ) -> list[SteerEntry]:
        """检查并应用所有排队的 steer — 唯一入口

        在步骤边界 / 循环边界调用，消费 SteerQueue 中所有排队的 steer:
        1. drain 队列
        2. 对每条 steer:
           a. mark_applied (标记 step_index + action_type)
           b. 发送 steer_applied chunk (通知前端)
           c. 追加到 _history (供 prompt 注入)
           d. 发送可见文本提示

        返回: 已消费的 SteerEntry 列表 (空列表 = 无 steer 或队列不存在)
        """
        if not self._queue:
            return []

        steers = await self._queue.drain()
        if not steers:
            return []

        for s in steers:
            self._queue.mark_applied(s, step_index, action_type)
            self._lifecycle.write_chunk(create_steer_applied_chunk(
                steer_id=s.id,
                steer_text=s.text,
                applied_at_step=step_index,
                action_type=action_type,
            ))
            self._history.append(s.text)
            self._lifecycle.write_chunk(create_text_chunk(
                f"\n> 🔄 **已接收转向指令**: {s.text}\n\n"
            ))

        return steers

    def format_for_prompt(self) -> str:
        """格式化 steer_history 为模型 prompt 注入文本

        返回空字符串表示无 steer 需注入。
        调用方直接拼接到 user_content 即可。
        """
        if not self._history:
            return ""
        steer_lines = "\n".join(f"  - {s}" for s in self._history)
        return (
            f"\n\n⚠️ 用户中途插话的转向指令（请据此调整草稿内容和侧重点）:\n{steer_lines}\n"
            "请在草稿中体现这些调整方向，不必在输出中重复指令本身。"
        )

    def to_history_list(self) -> list[str]:
        """返回 history 的副本 (兼容 AgentState.steer_history)"""
        return list(self._history)


@asynccontextmanager
async def steer_aware_step(
    controller: SteerController,
    step_index: int,
    action_type: str,
) -> AsyncIterator[None]:
    """上下文管理器 — 自动在步骤边界检查 steer

    防止「新增步骤时忘记加 _check_steer()」:
    每个步骤用 async with 包裹，steer 检查自动执行。

    用法:
        async with steer_aware_step(controller, step, "draft_tasklist"):
            # 步骤逻辑
            ...

    等价于在步骤开始前调用 controller.check_and_apply(step, action_type)。
    """
    await controller.check_and_apply(step_index, action_type)
    yield
