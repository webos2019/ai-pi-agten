"""聊天编排器 — 基于 agent_loop 的核心对话流程

阶段一迁移: 将原来的单层 while 循环替换为 pi.dev 风格的 agent_loop 双层循环。

收益:
- 并发工具执行 (asyncio.gather 替代串行 for)
- 截断容错 (fail_tool_calls_from_truncated_message)
- 模型热切换能力 (prepare_next_turn 钩子，阶段二启用)
- 上下文转换钩子 (transform_context，阶段二启用)
- 双层循环骨架 (follow-up 外层 + tool call 内层)
"""

import json
import asyncio
import os
import re
from typing import Any

from chat_session import ChatSession
from tool_registry import tool_registry
from skill_registry import skill_registry
from stream import (
    StreamLifecycle,
    StreamWriter,
    create_id,
    create_text_chunk,
    create_tool_call_chunk,
    create_tool_result_chunk,
    create_recovering_chunk,
    create_recovery_fallback_chunk,
)
from agent_runtime import (
    AgentState,
    run_tasklist_agent,
    resolve_version_plan_uri,
    list_available_version_plans,
    VERSION_PLAN_URI_PATTERN,
    VERSION_PLAN_URI_SEARCH_PATTERN,
)
from agent_loop import (
    run_loop,
    AgentContext,
    AgentMessage,
    AgentLoopConfig,
    ToolDefinition,
    ToolCallResult,
    AgentEvent,
)
from deepseek import chat_completion

MAX_TOOL_CALLS = 5
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 2.0


# ─── Agent 入口检测 ─────────────────────────────────────

async def _try_agent_entry(
    session: ChatSession,
    writer: StreamWriter,
    lifecycle: StreamLifecycle,
    context: dict[str, Any],
) -> bool:
    """
    Agent 受控入口检测。
    命中条件: /tasklist 命令 + @docs://versions/*.md 引用
    返回 True 表示命中 Agent 路径并已执行, False 表示不命中。
    """
    structured = context.get("structured")
    if not structured:
        return False

    chips = structured.get("chips", [])
    segments = structured.get("segments", [])

    # 检测 /tasklist 命令
    has_tasklist_command = any(
        seg.get("type") == "chip" and seg.get("chipType") == "skill"
        and "tasklist" in seg.get("label", "").lower()
        for seg in segments
    ) or any(
        chip.get("type") == "skill" and "tasklist" in chip.get("label", "").lower()
        for chip in chips
    )

    raw_text = structured.get("rawText", "")
    if not has_tasklist_command and "/tasklist" not in raw_text.lower():
        return False

    # 检测 @docs://versions/*.md 引用
    version_plan_uri: str | None = None
    for seg in segments:
        if seg.get("type") == "chip":
            label = seg.get("label", "")
            if VERSION_PLAN_URI_PATTERN.match(label):
                version_plan_uri = label
                break
            data = seg.get("data", {})
            if isinstance(data, dict) and data.get("uri"):
                if VERSION_PLAN_URI_PATTERN.match(data["uri"]):
                    version_plan_uri = data["uri"]
                    break
    for chip in chips:
        if not version_plan_uri:
            label = chip.get("label", "")
            if VERSION_PLAN_URI_PATTERN.match(label):
                version_plan_uri = label
                break
            data = chip.get("data", {})
            if isinstance(data, dict) and data.get("uri"):
                if VERSION_PLAN_URI_PATTERN.match(data["uri"]):
                    version_plan_uri = data["uri"]
                    break

    # 也检查 rawText 中是否有 docs://versions/ 引用（支持嵌入文本中的 URI）
    if not version_plan_uri:
        uri_match = VERSION_PLAN_URI_SEARCH_PATTERN.search(raw_text)
        if uri_match:
            version_plan_uri = f"docs://versions/{uri_match.group(1)}"

    # 借鉴 Pi (pi.dev) 的 AGENTS.md 机制：缺少显式引用时，自动发现默认版本方案
    if not version_plan_uri:
        auto_uri = _discover_default_version_plan()
        if auto_uri:
            version_plan_uri = auto_uri
            lifecycle.write_chunk(create_text_chunk(
                f"📌 自动发现默认版本方案: `{auto_uri}`（来自 AGENTS.md）\n\n"
            ))

    if not version_plan_uri:
        # 命中 /tasklist 但缺少版本方案引用 → 明确提示
        lifecycle.write_chunk(create_text_chunk(
            "⚠️ 请先通过 @ 引用一个 `docs://versions/*.md` 版本方案，再生成 tasklist 草稿。\n\n"
            "本版不支持只根据目标直接生成 tasklist。\n\n"
            "可用版本方案:\n"
            + "\n".join(f"  - `{p['uri']}`" for p in _list_version_plans())
        ))
        lifecycle.emit_done_once()
        return True  # 命中但缺少引用，仍然短路

    # ── 进入 Agent 路径 ──
    state = AgentState(
        run_id=create_id(),
        version_plan_uri=version_plan_uri,
    )

    # 从 context 获取 steer 队列（流式插话）
    steer_queue = context.get("steer_queue")

    await run_tasklist_agent(state, writer, lifecycle, steer_queue)
    lifecycle.emit_done_once()
    return True


def _list_version_plans() -> list[dict[str, str]]:
    return list_available_version_plans()


def _discover_default_version_plan() -> str | None:
    """
    从项目根 AGENTS.md 自动发现默认版本方案 URI。

    借鉴 Pi (pi.dev) 的 AGENTS.md 项目指令加载机制：
    Pi 从 ~/.pi/agent/、父目录链、当前目录发现 AGENTS.md 并注入上下文。
    本服务为 Web 后端，cwd 固定为项目根，故直接从项目根读取 AGENTS.md。

    AGENTS.md 中通过 `default_version_plan: docs://versions/xxx.md` 声明默认方案。
    返回确实存在的 URI 字符串；未声明或文件不存在则返回 None。
    """
    agents_md = os.path.join(os.path.dirname(__file__), "AGENTS.md")
    if not os.path.isfile(agents_md):
        return None
    try:
        with open(agents_md, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    match = re.search(
        r"^default_version_plan:\s*(docs://versions/[^\s]+\.md)\s*$",
        content, re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        return None
    uri = match.group(1).strip()
    # 校验方案文件确实存在
    _filename, filepath = resolve_version_plan_uri(uri)
    return uri if filepath else None


async def orchestrate_chat(
    session: ChatSession,
    writer: StreamWriter,
    context: dict[str, Any],
) -> None:
    """主编排函数 - 带错误恢复

    steer 集成: 从 context 读取 steer_queue_id，写入 start chunk，
    前端据此发起流式插话 (/api/chat/steer)。
    """
    lifecycle = StreamLifecycle(writer)
    message_id = create_id()
    steer_queue_id = context.get("steer_queue_id")
    lifecycle.emit_start_once(message_id, steer_queue_id)

    recovery_attempts = 0

    while recovery_attempts <= MAX_RETRY_ATTEMPTS:
        try:
            # ── Agent 受控分支 (最优先) ──
            if await _try_agent_entry(session, writer, lifecycle, context):
                lifecycle.close()
                return

            await _do_orchestrate(session, writer, context, lifecycle)
            lifecycle.close()
            return
        except Exception as err:
            error_msg = str(err) if str(err) else "未知错误"

            if recovery_attempts < MAX_RETRY_ATTEMPTS:
                recovery_attempts += 1
                lifecycle.write_chunk(create_recovering_chunk(
                    f"服务遇到问题，正在尝试恢复... ({recovery_attempts}/{MAX_RETRY_ATTEMPTS})",
                    recovery_attempts,
                    MAX_RETRY_ATTEMPTS,
                ))
                await asyncio.sleep(RETRY_DELAY_S * recovery_attempts)
            else:
                lifecycle.write_chunk(create_recovery_fallback_chunk(
                    "多次尝试恢复失败，将尝试直接回答",
                    "direct-answer",
                ))
                try:
                    await _fallback_to_direct_answer(session, lifecycle)
                except Exception as fallback_err:
                    lifecycle.emit_error_once(
                        str(fallback_err) if str(fallback_err) else "服务不可用"
                    )
                lifecycle.close()
                return


# ════════════════════════════════════════════════════════
#  基于 agent_loop 的普通聊天编排 (阶段一迁移)
# ════════════════════════════════════════════════════════

async def _do_orchestrate(
    session: ChatSession,
    writer: StreamWriter,
    context: dict[str, Any],
    lifecycle: StreamLifecycle,
) -> None:
    """执行编排逻辑：通过 agent_loop 实现完整的 LLM ↔ 工具循环

    替代原来的单层 while 循环 + 串行工具执行 + 手写总结生成。
    agent_loop 自动处理:
    - LLM 调用 → 工具执行 → 下一轮 LLM（看到工具结果后自然生成总结）
    - 并发工具执行 (asyncio.gather)
    - 截断容错 (stop_reason=length 时自动标记 tool_call 为错误)
    - 双层循环骨架 (follow-up 外层 + tool call 内层)
    """
    current_messages = list(session.get_messages())
    skill = skill_registry.get(session.get_skill_id())
    result_policy = skill.result_policy if skill else "auto"
    tool_names = skill.tool_names if skill else []

    # 1. 构建 AgentMessage 列表
    agent_messages = _convert_to_agent_messages(current_messages)

    # 2. 构建工具定义 (桥接 tool_registry → ToolDefinition)
    tool_defs = _build_tool_definitions(tool_names, context)

    # 3. 构建 AgentContext
    agent_context = AgentContext(
        system_prompt=session.get_system_prompt(),
        messages=agent_messages,
        tools=tool_defs,
        model="deepseek-chat",
    )

    # 4. 构建配置 — 所有策略通过钩子注入
    tool_call_tracker = {"count": 0}

    async def stream_fn(ctx: AgentContext, cfg: AgentLoopConfig) -> AgentMessage:
        return await _default_stream_fn(ctx, cfg)

    async def should_stop(
        message: AgentMessage,
        tool_results: list[ToolCallResult],
        ctx: AgentContext,
    ) -> bool:
        return _check_should_stop(
            message, tool_results, ctx,
            result_policy, lifecycle, tool_call_tracker,
        )

    config = AgentLoopConfig(
        stream_fn=stream_fn,
        should_stop_after_turn=should_stop,
        tool_timeout=30.0,
    )

    # 5. 构建 emit 回调 — 实时把 AgentEvent 转为 NDJSON chunk
    async def emit(event: AgentEvent) -> None:
        _emit_event_to_stream(event, lifecycle)

    # 6. 运行 agent_loop (直接调用 run_loop，实现实时事件推送)
    new_messages: list[AgentMessage] = []
    await run_loop(agent_context, new_messages, config, None, emit)

    lifecycle.emit_done_once()


# ─── 消息格式转换 ──────────────────────────────────────

def _convert_to_agent_messages(messages: list[dict[str, Any]]) -> list[AgentMessage]:
    """将 OpenAI 格式的消息列表转为 AgentMessage 列表"""
    result: list[AgentMessage] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "assistant":
            tool_calls = None
            if msg.get("tool_calls"):
                tool_calls = []
                for tc in msg["tool_calls"]:
                    args_str = tc.get("function", {}).get("arguments", "")
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": args,
                    })
            result.append(AgentMessage(
                role="assistant",
                content=content,
                tool_calls=tool_calls,
            ))
        elif role == "tool":
            result.append(AgentMessage(
                role="tool_result",
                content=content,
                tool_call_id=msg.get("tool_call_id"),
            ))
        else:
            result.append(AgentMessage(
                role="user",
                content=content,
            ))
    return result


def _agent_message_to_openai(msg: AgentMessage) -> dict[str, Any]:
    """将 AgentMessage 转为 OpenAI API 格式"""
    if msg.role == "tool_result":
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id or "",
            "content": content,
        }

    content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
    entry: dict[str, Any] = {"role": msg.role, "content": content}

    if msg.tool_calls:
        entry["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": (
                        json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                        if isinstance(tc.get("arguments"), dict)
                        else str(tc.get("arguments", ""))
                    ),
                },
            }
            for tc in msg.tool_calls
        ]

    return entry


# ─── stream_fn: 包装 deepseek.chat_completion ──────────

async def _default_stream_fn(
    context: AgentContext,
    config: AgentLoopConfig,
) -> AgentMessage:
    """默认的 stream_fn — 调用 DeepSeek API 获取 assistant 响应

    1. 构建 OpenAI 格式消息 (system + context.messages)
    2. 构建 tool specs (从 context.tools)
    3. 调用 chat_completion
    4. 映射 stop_reason + 转换 tool_calls → AgentMessage
    """
    # 1. 构建消息列表
    llm_messages: list[dict[str, Any]] = [
        {"role": "system", "content": context.system_prompt}
    ]
    for msg in context.messages:
        llm_messages.append(_agent_message_to_openai(msg))

    # 2. 构建工具规格 (从 context.tools，支持 prepare_next_turn 热切换)
    tool_specs: list[dict[str, Any]] | None = None
    if context.tools:
        tool_specs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in context.tools
        ]

    # 3. 调用 LLM
    response = await chat_completion(
        messages=llm_messages,
        tools=tool_specs,
    )
    choice = response.choices[0]
    message = choice.message

    # 4. 映射 stop_reason
    stop_reason = _map_stop_reason(choice.finish_reason)

    # 5. 转换 tool_calls
    tool_calls: list[dict[str, Any]] = []
    if message.tool_calls:
        for tc in message.tool_calls:
            args_str = tc.function.arguments
            try:
                args = json.loads(args_str) if args_str else {}
            except (json.JSONDecodeError, ValueError):
                # JSON 截断 — 保留原始字符串，让 agent_loop 的截断容错处理
                args = {"_raw_truncated": args_str}
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            })

    return AgentMessage(
        role="assistant",
        content=message.content or "",
        tool_calls=tool_calls if tool_calls else None,
        stop_reason=stop_reason,
    )


def _map_stop_reason(finish_reason: str | None) -> str:
    """将 OpenAI finish_reason 映射为 agent_loop 的 stop_reason"""
    mapping = {
        "stop": "stop",
        "tool_calls": "tool_use",
        "length": "length",
        "content_filter": "error",
    }
    return mapping.get(finish_reason or "", "stop")


# ─── 工具定义桥接 ──────────────────────────────────────

def _build_tool_definitions(
    tool_names: list[str],
    context: dict[str, Any],
) -> list[ToolDefinition]:
    """将 tool_registry 中的工具桥接为 agent_loop 的 ToolDefinition

    每个工具的 handler 通过闭包捕获 context，调用 tool_registry.execute。
    """
    defs: list[ToolDefinition] = []
    for name in tool_names:
        tool_def = tool_registry.get(name)
        if not tool_def:
            continue

        # 闭包捕获 name 和 context
        async def handler(
            args: dict[str, Any],
            _name: str = name,
            _ctx: dict[str, Any] = context,
        ) -> str:
            return await tool_registry.execute(_name, args, _ctx)

        defs.append(ToolDefinition(
            name=tool_def.name,
            description=tool_def.description,
            parameters=tool_def.parameters,
            handler=handler,
        ))
    return defs


# ─── 事件 → chunk 转换 ─────────────────────────────────

def _emit_event_to_stream(event: AgentEvent, lifecycle: StreamLifecycle) -> None:
    """将 AgentEvent 实时转为 NDJSON chunk 并写入流

    事件类型 → chunk 映射:
    - agent_start  → 忽略 (start chunk 已由 orchestrate_chat 发射)
    - turn_start   → 忽略
    - message_start → text chunk (content) + tool_call chunks
    - message_end   → 忽略
    - turn_end      → tool_result chunks
    - agent_end     → 忽略 (done chunk 由 _do_orchestrate 最后发射)
    """
    if event.type == "message_start":
        msg = event.message
        if msg and msg.role == "assistant":
            # 发射文本内容
            if msg.content:
                lifecycle.write_chunk(create_text_chunk(msg.content))
            # 发射 tool_call chunks
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", create_id())
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("arguments", {})
                    lifecycle.write_chunk(create_tool_call_chunk(tc_id, tc_name, tc_args))

    elif event.type == "turn_end":
        if event.tool_results:
            for tr in event.tool_results:
                tool_def = tool_registry.get(tr.tool_name)
                is_authoritative = tool_def.result_is_authoritative if tool_def else False

                # 工具不在技能范围内时给友好提示
                if tr.is_error and "not found" in tr.result.lower():
                    lifecycle.write_chunk(create_text_chunk(
                        f"⚠️ 工具 {tr.tool_name} 不在当前技能的能力范围内"
                    ))

                lifecycle.write_chunk(create_tool_result_chunk(
                    tr.tool_call_id,
                    tr.tool_name,
                    tr.result,
                    is_valid=not tr.is_error,
                    is_authoritative=is_authoritative,
                ))


# ─── 停止策略 ──────────────────────────────────────────

def _check_should_stop(
    message: AgentMessage,
    tool_results: list[ToolCallResult],
    ctx: AgentContext,
    result_policy: str,
    lifecycle: StreamLifecycle,
    tool_call_tracker: dict[str, int],
) -> bool:
    """should_stop_after_turn 钩子实现

    停止条件:
    1. 工具调用总数超过 MAX_TOOL_CALLS
    2. tool-first policy: 有 authoritative result 时直接输出并停止
    """
    if not tool_results:
        return False

    # 累计工具调用次数
    tool_call_tracker["count"] += len(tool_results)

    # 超过最大工具调用次数 → 停止
    if tool_call_tracker["count"] >= MAX_TOOL_CALLS:
        return True

    # tool-first policy: 有 authoritative result → 直接输出工具结果并停止
    if result_policy == "tool-first":
        for tr in tool_results:
            tool_def = tool_registry.get(tr.tool_name)
            if tool_def and tool_def.result_is_authoritative:
                formatted = _format_tool_result_for_text(tr.result, tr.tool_name)
                lifecycle.write_chunk(create_text_chunk(formatted))
                return True

    return False


# ─── 错误恢复回退 ──────────────────────────────────────

async def _fallback_to_direct_answer(
    session: ChatSession,
    lifecycle: StreamLifecycle,
) -> None:
    """回退到直接回答（无工具）"""
    messages = session.get_messages()
    response = await session.invoke_model(messages=messages, tools=[])
    choice = response.choices[0]
    content = choice.message.content or ""
    if content:
        lifecycle.write_chunk(create_text_chunk(content))
    lifecycle.emit_done_once()


# ─── 工具结果格式化 ────────────────────────────────────

def _format_tool_result_for_text(tool_result: str, tool_name: str) -> str:
    """格式化工具结果为文本"""
    try:
        parsed = json.loads(tool_result)
        if parsed.get("message"):
            return parsed["message"]
        if parsed.get("result") is not None:
            if parsed.get("fromName") and parsed.get("toName"):
                return f"{parsed['value']} {parsed['fromName']} = {parsed['result']} {parsed['toName']}"
            return str(parsed["result"])
        if parsed.get("expression") is not None:
            return f"{parsed['expression']} = {parsed['result']}"
        if parsed.get("currentTime"):
            return f"当前时间：{parsed['currentTime']}"
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return tool_result
