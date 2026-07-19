"""Steer 队列 — 流式插话机制

借鉴 pi.dev 的 steer 命令设计，实现 Agent 流式输出期间的中途插话能力:

工作流程:
1. Agent 流开始时，chat_service 注册一个 SteerQueue 到 ActiveStreamRegistry
2. start chunk 携带 steerQueueId，前端读取后可用于发起 steer
3. 用户中途发送 steer → POST /api/chat/steer → active_streams.enqueue()
4. Agent 在每个步骤边界调用 steer_queue.drain() 消费排队的 steer
5. steer 文本注入到后续步骤的模型 prompt（state.steer_history）
6. 流结束后 unregister，拒绝后续 steer 请求

与 pi.dev 的差异:
- pi.dev 的 steer 在"当前轮工具执行完后"插入（更细粒度）
- 我们的实现是在"Agent 步骤边界"检查（适配受控状态机的 7-8 步结构）
- pi.dev 还有 follow_up（流结束后追加），本版暂未实现
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from stream import create_id


@dataclass
class SteerEntry:
    """单条 steer 指令"""
    id: str
    text: str
    created_at: float
    applied: bool = False
    applied_at_step: int = -1
    applied_at_action: str = ""
    rejected: bool = False
    reject_reason: str = ""

    def to_dto(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "createdAt": self.created_at,
            "applied": self.applied,
            "appliedAtStep": self.applied_at_step,
            "appliedAtAction": self.applied_at_action,
            "rejected": self.rejected,
            "rejectReason": self.reject_reason,
        }


class SteerQueue:
    """单个活跃流的 steer 队列

    线程安全: 仅在 asyncio 事件循环中使用，put_nowait / get_nowait 都是协程安全的。
    """

    def __init__(self, steer_queue_id: str):
        self._id = steer_queue_id
        self._queue: asyncio.Queue[SteerEntry] = asyncio.Queue()
        self._entries: list[SteerEntry] = []  # 全部记录（含已处理）
        self._ended = False

    @property
    def id(self) -> str:
        return self._id

    def is_ended(self) -> bool:
        return self._ended

    def pending_count(self) -> int:
        """未消费的 steer 数量"""
        return self._queue.qsize()

    def enqueue(self, text: str) -> tuple[bool, str, SteerEntry | None]:
        """入队一条 steer 指令。

        返回: (success, message, entry)
        - 流已结束 → 拒绝
        - 文本为空 → 拒绝
        - 成功 → entry 入队
        """
        if self._ended:
            return False, "流已结束，无法 steer", None
        if not text or not text.strip():
            return False, "steer 文本不能为空", None

        entry = SteerEntry(
            id=create_id(),
            text=text.strip(),
            created_at=time.time(),
        )
        self._entries.append(entry)
        self._queue.put_nowait(entry)
        return True, "已入队", entry

    async def drain(self) -> list[SteerEntry]:
        """消费所有排队的 steer（在 Agent 步骤边界调用）

        返回: 已消费的 steer 列表（按入队顺序）
        流已结束时返回空列表。
        """
        if self._ended:
            return []

        drained: list[SteerEntry] = []
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            entry.applied = True
            drained.append(entry)
        return drained

    def mark_applied(
        self,
        entry: SteerEntry,
        step_index: int,
        action_type: str,
    ) -> None:
        """标记一条 steer 已在特定步骤应用"""
        entry.applied = True
        entry.applied_at_step = step_index
        entry.applied_at_action = action_type

    def reject_pending(self, reason: str = "流已结束") -> list[SteerEntry]:
        """拒绝所有未处理的 steer（流结束时调用）

        返回: 被拒绝的 steer 列表
        """
        self._ended = True
        rejected: list[SteerEntry] = []
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            entry.rejected = True
            entry.reject_reason = reason
            rejected.append(entry)
        return rejected

    def get_all_entries(self) -> list[SteerEntry]:
        """获取所有 steer 记录（含已处理/已拒绝）"""
        return list(self._entries)

    def get_pending_texts(self) -> list[str]:
        """获取所有已入队但未消费的 steer 文本（不消费）"""
        return [e.text for e in self._entries if e.applied is False and e.rejected is False]

    def to_dto(self) -> dict[str, Any]:
        return {
            "steerQueueId": self._id,
            "ended": self._ended,
            "totalCount": len(self._entries),
            "pendingCount": self.pending_count(),
            "entries": [e.to_dto() for e in self._entries],
        }


class ActiveStreamRegistry:
    """活跃流注册表 — 管理 steer 队列

    key: steer_queue_id（由 chat_service 在流开始时生成）
    生命周期: register(流开始) → enqueue(中途插话) → unregister(流结束)

    设计要点:
    - 全局单例，跨请求共享（多个并发流各自有独立 SteerQueue）
    - unregister 时自动拒绝所有未处理的 steer
    - enqueue 是非阻塞的（put_nowait），不影响 Agent 执行
    """

    def __init__(self):
        self._streams: dict[str, SteerQueue] = {}

    def register(self, steer_queue_id: str) -> SteerQueue:
        """注册一个新的活跃流"""
        queue = SteerQueue(steer_queue_id)
        self._streams[steer_queue_id] = queue
        return queue

    def get(self, steer_queue_id: str) -> SteerQueue | None:
        return self._streams.get(steer_queue_id)

    def is_active(self, steer_queue_id: str) -> bool:
        queue = self._streams.get(steer_queue_id)
        return queue is not None and not queue.is_ended()

    def unregister(self, steer_queue_id: str) -> list[SteerEntry]:
        """注销活跃流，拒绝所有未处理的 steer

        返回: 被拒绝的 steer 列表（供调用方发送 steer_rejected chunk）
        """
        queue = self._streams.pop(steer_queue_id, None)
        if not queue:
            return []
        return queue.reject_pending("流已结束")

    def enqueue(
        self,
        steer_queue_id: str,
        text: str,
    ) -> tuple[bool, str, SteerEntry | None]:
        """向活跃流入队 steer

        返回: (success, message, entry)
        - 流不存在 → 失败
        - 流已结束 → 失败
        - 成功 → entry 入队
        """
        queue = self._streams.get(steer_queue_id)
        if not queue:
            return False, "未找到活跃流（可能已结束或 steerQueueId 无效）", None
        return queue.enqueue(text)

    def active_count(self) -> int:
        return len(self._streams)


# 全局单例
active_streams = ActiveStreamRegistry()
