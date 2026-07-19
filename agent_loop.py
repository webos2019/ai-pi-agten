"""Agent Loop — pi.dev 风格的通用 Agent 循环

借鉴 pi.dev (packages/agent/src/agent-loop.ts ~550行) 的核心设计，
实现一个钩子化的 Agent 循环，补齐我们项目缺失的 5 个能力:

1. transformContext 钩子    — 上下文转换（RAG/过滤/注入）
2. prepareNextTurn 钩子     — 模型热切换 / thinking level 调整
3. shouldStopAfterTurn 钩子 — 自定义停止策略
4. failToolCallsFromTruncatedMessage — LLM 输出截断时的容错
5. 双层循环                 — follow-up 外层循环 + tool call 内层循环

设计哲学（与 pi.dev 一致）:
  runLoop() 核心循环只管"流式 → 工具调用 → 下一轮"骨架，
  所有策略通过 config 钩子注入，核心不绑定具体业务逻辑。

与 pi.dev 的差异:
  - pi.dev 用 TypeScript AsyncIterator EventStream，我们用 asyncio + list
  - pi.dev 的 convertToLlm 在核心循环内调用，我们把它作为 streamFn 的职责
  - 我们的 SteerQueue / FollowUpQueue 复用已有的 steer_queue.py 基础设施
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from stream import create_id


# ─── 消息与工具类型 ────────────────────────────────────

@dataclass
class AgentMessage:
    """Agent 消息 — 对应 pi.dev 的 AgentMessage"""
    role: Literal["user", "assistant", "tool_result"]
    content: Any  # str 或 list[dict]（多模态）
    tool_calls: list[dict[str, Any]] | None = None  # assistant 消息的 tool_call 列表
    tool_call_id: str | None = None  # tool_result 消息关联的 tool_call_id
    stop_reason: str | None = None  # assistant: "stop" | "tool_use" | "length" | "error" | "aborted"


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], Awaitable[str]]  # 异步执行函数


@dataclass
class ToolCallResult:
    """单个工具调用结果"""
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    result: str
    is_error: bool = False


@dataclass
class ExecutedToolBatch:
    """一批工具调用的执行结果 — 对应 pi.dev 的 ExecutedToolBatch"""
    messages: list[AgentMessage]  # tool_result 消息
    results: list[ToolCallResult]
    terminate: bool = False  # 是否应终止循环


# ─── AgentContext ──────────────────────────────────────

@dataclass
class AgentContext:
    """Agent 上下文 — 对应 pi.dev 的 AgentContext

    在循环过程中被逐步修改（messages 追加）。
    """
    system_prompt: str
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    model: str = "deepseek-chat"
    thinking_level: str = "medium"  # "off" | "low" | "medium" | "high"


# ─── NextTurnSnapshot ──────────────────────────────────

@dataclass
class NextTurnSnapshot:
    """prepareNextTurn 钩子的返回值 — 可在下一轮切换 model/thinking_level

    对应 pi.dev 的 prepareNextTurn 返回值。
    """
    context: AgentContext | None = None
    model: str | None = None
    thinking_level: str | None = None


# ─── AgentLoopConfig ───────────────────────────────────

@dataclass
class AgentLoopConfig:
    """Agent 循环配置 — 所有策略通过钩子注入

    对应 pi.dev 的 AgentLoopConfig，核心循环通过调用这些钩子来获取策略。
    每个钩子都是可选的（None 时使用默认行为）。
    """

    # LLM 流式调用函数 (必需)
    # 接收 context + config，返回 (assistant_message, stop_reason)
    stream_fn: Callable[[AgentContext, "AgentLoopConfig"], Awaitable[AgentMessage]]

    # ── 上下文转换钩子 ──
    # 在每次 LLM 调用前转换消息列表（RAG 注入 / 历史过滤 / 图片过滤等）
    # 对应 pi.dev 的 transformContext
    transform_context: Callable[[list[AgentMessage]], Awaitable[list[AgentMessage]]] | None = None

    # ── 消息格式转换钩子 ──
    # 将 AgentMessage[] 转为 LLM API 格式（对应 pi.dev 的 convertToLlm）
    # 如果为 None，使用默认的 _default_convert_to_llm
    convert_to_llm: Callable[[list[AgentMessage]], list[dict[str, Any]]] | None = None

    # ── Steer 队列钩子 ──
    # 返回待处理的 steering 消息（流式插话），在下一轮 assistant 响应前注入
    get_steering_messages: Callable[[], Awaitable[list[AgentMessage]]] | None = None

    # ── Follow-up 队列钩子 ──
    # 返回流后追加消息（agent 本轮结束后继续），驱动外层循环
    get_follow_up_messages: Callable[[], Awaitable[list[AgentMessage]]] | None = None

    # ── 下一轮准备钩子 ──
    # 在每次 tool call 批次执行后调用，可切换 model / thinking_level / 修改 context
    prepare_next_turn: Callable[[AgentContext, AgentMessage, list[ToolCallResult]], Awaitable[NextTurnSnapshot | None]] | None = None

    # ── 停止策略钩子 ──
    # 在每轮结束后调用，返回 True 则停止循环（即使还有 tool call 能力）
    should_stop_after_turn: Callable[[AgentMessage, list[ToolCallResult], AgentContext], Awaitable[bool]] | None = None

    # ── API Key 解析钩子 ──
    get_api_key: Callable[[str], Awaitable[str | None]] | None = None

    # 工具执行超时（秒）
    tool_timeout: float = 30.0


# ─── 事件类型 ──────────────────────────────────────────

@dataclass
class AgentEvent:
    """Agent 事件 — 对应 pi.dev 的 AgentEvent"""
    type: str  # "agent_start" | "turn_start" | "message_start" | "message_update" | "message_end" | "turn_end" | "agent_end"
    message: AgentMessage | None = None
    tool_results: list[ToolCallResult] | None = None
    messages: list[AgentMessage] | None = None  # agent_end 时携带全部消息


# ─── 核心循环 ──────────────────────────────────────────

async def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None = None,
) -> tuple[list[AgentEvent], list[AgentMessage]]:
    """启动 Agent 循环 — 对应 pi.dev 的 agentLoop()

    返回: (events, messages)
    """
    events: list[AgentEvent] = []
    new_messages: list[AgentMessage] = list(prompts)

    current_context: AgentContext = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
        model=context.model,
        thinking_level=context.thinking_level,
    )

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    await run_loop(current_context, new_messages, config, signal, emit)
    return events, new_messages


async def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None = None,
) -> tuple[list[AgentEvent], list[AgentMessage]]:
    """从当前上下文继续 — 对应 pi.dev 的 agentLoopContinue()

    不添加新消息，用于重试。
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if context.messages[-1].role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    events: list[AgentEvent] = []
    new_messages: list[AgentMessage] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    await run_loop(context, new_messages, config, signal, emit)
    return events, new_messages


async def run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> None:
    """主循环逻辑 — 对应 pi.dev 的 runLoop()

    双层循环:
      外层 (while True): follow-up 驱动
        内层 (while has_more_tool_calls or pending_steers): tool call 驱动
          1. 注入 steering 消息
          2. stream_assistant_response() — 调 LLM
          3. 检查 stop_reason
          4. 提取 tool_calls → execute_tool_calls()
          5. prepare_next_turn() — 切模型/thinking
          6. should_stop_after_turn() — 检查是否该停
          7. 获取新的 steering 消息
        检查 follow-up 消息 → 有则继续，无则退出
    """
    current_context = initial_context
    current_config = config
    first_turn = True

    # 启动时检查 steering 消息
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages() or []

    await emit(AgentEvent(type="agent_start"))

    # 外层循环: follow-up 驱动
    while True:
        has_more_tool_calls = True

        # 内层循环: tool call 驱动
        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await emit(AgentEvent(type="turn_start"))
            else:
                first_turn = False

            # 1. 注入 pending 消息（steering 或 follow-up）
            if pending_messages:
                for msg in pending_messages:
                    await emit(AgentEvent(type="message_start", message=msg))
                    await emit(AgentEvent(type="message_end", message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # 2. 流式获取 assistant 响应
            message = await stream_assistant_response(
                current_context, current_config, signal, emit,
            )
            new_messages.append(message)

            # 3. 检查停止原因
            if message.stop_reason in ("error", "aborted"):
                await emit(AgentEvent(type="turn_end", message=message, tool_results=[]))
                await emit(AgentEvent(type="agent_end", messages=new_messages))
                return

            # 4. 提取 tool_calls
            tool_calls = message.tool_calls or []
            tool_results: list[ToolCallResult] = []

            has_more_tool_calls = False
            if tool_calls:
                # 检查是否被截断
                if message.stop_reason == "length":
                    # 截断容错: 将不完整的 tool_calls 标记为错误
                    executed = await fail_tool_calls_from_truncated_message(
                        tool_calls, emit,
                    )
                    tool_results.extend(executed.results)
                    for tr_msg in executed.messages:
                        current_context.messages.append(tr_msg)
                        new_messages.append(tr_msg)
                    has_more_tool_calls = not executed.terminate
                else:
                    # 正常执行工具
                    executed = await execute_tool_calls(
                        current_context, message, current_config, signal, emit,
                    )
                    tool_results.extend(executed.results)
                    for tr_msg in executed.messages:
                        current_context.messages.append(tr_msg)
                        new_messages.append(tr_msg)
                    has_more_tool_calls = not executed.terminate

            await emit(AgentEvent(type="turn_end", message=message, tool_results=tool_results))

            # 5. prepareNextTurn — 切换 model / thinking_level
            if current_config.prepare_next_turn and tool_results:
                snapshot = await current_config.prepare_next_turn(
                    current_context, message, tool_results,
                )
                if snapshot:
                    if snapshot.context:
                        current_context = snapshot.context
                    if snapshot.model or snapshot.thinking_level:
                        # 创建新 config（保持钩子不变，更新 model/thinking）
                        current_context.model = snapshot.model or current_context.model
                        current_context.thinking_level = (
                            snapshot.thinking_level or current_context.thinking_level
                        )

            # 6. shouldStopAfterTurn — 自定义停止策略
            if current_config.should_stop_after_turn:
                should_stop = await current_config.should_stop_after_turn(
                    message, tool_results, current_context,
                )
                if should_stop:
                    await emit(AgentEvent(type="agent_end", messages=new_messages))
                    return

            # 7. 获取新的 steering 消息
            if current_config.get_steering_messages:
                pending_messages = await current_config.get_steering_messages() or []
            else:
                pending_messages = []

        # 外层循环: 检查 follow-up 消息
        follow_ups: list[AgentMessage] = []
        if current_config.get_follow_up_messages:
            follow_ups = await current_config.get_follow_up_messages() or []

        if follow_ups:
            pending_messages = follow_ups
            continue

        # 无 follow-up，退出
        break

    await emit(AgentEvent(type="agent_end", messages=new_messages))


# ─── 流式 Assistant 响应 ───────────────────────────────

async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> AgentMessage:
    """调用 LLM 获取 assistant 响应 — 对应 pi.dev 的 streamAssistantResponse()

    1. 应用 transform_context 钩子（如果配置了）
    2. 调用 stream_fn 获取响应
    3. 发射 message_start / message_end 事件
    """
    # 1. 应用上下文转换
    messages = context.messages
    if config.transform_context:
        messages = await config.transform_context(messages)

    # 2. 构建转换后的 context（不修改原 context）
    llm_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=messages,
        tools=context.tools,
        model=context.model,
        thinking_level=context.thinking_level,
    )

    # 3. 调用 stream_fn
    if signal and signal.is_set():
        return AgentMessage(
            role="assistant",
            content="",
            stop_reason="aborted",
        )

    message = await config.stream_fn(llm_context, config)

    # 4. 发射事件
    await emit(AgentEvent(type="message_start", message=message))
    await emit(AgentEvent(type="message_end", message=message))

    # 追加到 context
    context.messages.append(message)

    return message


# ─── 工具批量执行 ──────────────────────────────────────

async def execute_tool_calls(
    context: AgentContext,
    assistant_message: AgentMessage,
    config: AgentLoopConfig,
    signal: asyncio.Event | None,
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> ExecutedToolBatch:
    """批量执行工具调用 — 对应 pi.dev 的 executeToolCalls()

    并发执行所有 tool_call，返回结果消息列表。
    """
    tool_calls = assistant_message.tool_calls or []
    if not tool_calls:
        return ExecutedToolBatch(messages=[], results=[], terminate=True)

    # 构建工具名 → 定义映射
    tool_map: dict[str, ToolDefinition] = {t.name: t for t in context.tools}

    async def _run_single(tc: dict[str, Any]) -> ToolCallResult:
        tc_id = tc.get("id", create_id())
        tc_name = tc.get("name", "")
        tc_args = tc.get("arguments", {})

        if tc_name not in tool_map:
            return ToolCallResult(
                tool_call_id=tc_id,
                tool_name=tc_name,
                args=tc_args,
                result=f"Error: tool '{tc_name}' not found",
                is_error=True,
            )

        tool = tool_map[tc_name]
        try:
            if signal and signal.is_set():
                return ToolCallResult(
                    tool_call_id=tc_id,
                    tool_name=tc_name,
                    args=tc_args,
                    result="Aborted by signal",
                    is_error=True,
                )

            result = await asyncio.wait_for(
                tool.handler(tc_args),
                timeout=config.tool_timeout,
            )
            return ToolCallResult(
                tool_call_id=tc_id,
                tool_name=tc_name,
                args=tc_args,
                result=result,
            )
        except asyncio.TimeoutError:
            return ToolCallResult(
                tool_call_id=tc_id,
                tool_name=tc_name,
                args=tc_args,
                result=f"Error: tool '{tc_name}' timed out after {config.tool_timeout}s",
                is_error=True,
            )
        except Exception as e:
            return ToolCallResult(
                tool_call_id=tc_id,
                tool_name=tc_name,
                args=tc_args,
                result=f"Error: {e}",
                is_error=True,
            )

    # 并发执行
    results = await asyncio.gather(*[_run_single(tc) for tc in tool_calls])

    # 构建 tool_result 消息
    messages: list[AgentMessage] = []
    for r in results:
        messages.append(AgentMessage(
            role="tool_result",
            content=r.result,
            tool_call_id=r.tool_call_id,
        ))

    return ExecutedToolBatch(messages=messages, results=list(results), terminate=False)


# ─── 截断容错 ──────────────────────────────────────────

async def fail_tool_calls_from_truncated_message(
    tool_calls: list[dict[str, Any]],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> ExecutedToolBatch:
    """将截断消息中的 tool_calls 标记为错误 — 对应 pi.dev 的 failToolCallsFromTruncatedMessage()

    当 LLM 输出被 token limit 截断时 (stop_reason == "length")，
    tool_call 的 arguments 可能不完整（JSON 截断）。
    将每个 tool_call 标记为错误，让模型在下一轮重新生成。
    """
    results: list[ToolCallResult] = []
    messages: list[AgentMessage] = []

    for tc in tool_calls:
        tc_id = tc.get("id", create_id())
        tc_name = tc.get("name", "unknown")
        tc_args = tc.get("arguments", {})

        # 尝试检测参数是否完整（简单的 JSON 完整性检查）
        args_str = json.dumps(tc_args, ensure_ascii=False) if tc_args else ""
        is_incomplete = _is_tool_call_truncated(tc)

        error_msg = (
            f"Error: tool call was truncated by token limit "
            f"(stop_reason=length). Argument JSON may be incomplete. "
            f"Please re-issue this tool call."
        )

        result = ToolCallResult(
            tool_call_id=tc_id,
            tool_name=tc_name,
            args=tc_args,
            result=error_msg,
            is_error=True,
        )
        results.append(result)
        messages.append(AgentMessage(
            role="tool_result",
            content=error_msg,
            tool_call_id=tc_id,
        ))

    return ExecutedToolBatch(messages=messages, results=results, terminate=False)


def _is_tool_call_truncated(tc: dict[str, Any]) -> bool:
    """检测 tool_call 是否被截断

    启发式检查:
    - arguments 为空字符串
    - arguments 是不完整的 JSON 片段
    """
    args = tc.get("arguments")
    if args is None:
        return True
    if isinstance(args, str):
        args_stripped = args.strip()
        if not args_stripped:
            return True
        # 尝试 JSON 解析
        try:
            json.loads(args_stripped)
            return False
        except (json.JSONDecodeError, ValueError):
            return True
    if isinstance(args, dict):
        return False
    return False


# ─── 默认 convertToLlm ─────────────────────────────────

def _default_convert_to_llm(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """默认的消息格式转换 — 将 AgentMessage[] 转为 LLM API 格式

    对应 pi.dev 的 convertToLlm，可在 config.convert_to_llm 中覆盖。
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        result.append(entry)
    return result


# ─── FollowUpQueue ─────────────────────────────────────

class FollowUpQueue:
    """Follow-up 队列 — 流后追加消息

    对应 pi.dev 的 follow_up 机制:
    - Agent 本轮结束后，检查是否有 follow-up 消息
    - 有则继续下一轮（作为新的 user 消息注入）
    - 无则真正结束

    与 SteerQueue 的区别:
    - SteerQueue: 流式中途插话，在工具执行边界注入
    - FollowUpQueue: 流后追加，在本轮 Agent 完全结束后注入
    """

    def __init__(self):
        self._queue: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._entries: list[AgentMessage] = []
        self._ended = False

    def enqueue(self, text: str) -> bool:
        """入队一条 follow-up 消息"""
        if self._ended:
            return False
        if not text or not text.strip():
            return False
        msg = AgentMessage(role="user", content=text.strip())
        self._entries.append(msg)
        self._queue.put_nowait(msg)
        return True

    async def drain(self) -> list[AgentMessage]:
        """消费所有排队的 follow-up"""
        if self._ended:
            return []
        drained: list[AgentMessage] = []
        while not self._queue.empty():
            drained.append(self._queue.get_nowait())
        return drained

    def reject_pending(self, reason: str = "流已结束") -> int:
        """拒绝所有未处理的 follow-up"""
        self._ended = True
        count = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            count += 1
        return count

    def is_ended(self) -> bool:
        return self._ended

    def get_all_entries(self) -> list[AgentMessage]:
        return list(self._entries)
