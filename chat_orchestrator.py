"""聊天编排器 — 核心对话流程 (Agent 分支 + 工具循环 + 错误恢复)"""

import json
import asyncio
from typing import Any

from chat_session import ChatSession
from tool_registry import tool_registry
from skill_registry import skill_registry
from tool_runtime import tool_runtime
from stream import (
    StreamLifecycle,
    StreamWriter,
    create_id,
    create_text_chunk,
    create_tool_call_chunk,
    create_tool_result_chunk,
    create_recovering_chunk,
    create_recovery_fallback_chunk,
    create_agent_step_start_chunk,
    create_agent_step_end_chunk,
)
from agent_runtime import (
    AgentState,
    run_tasklist_agent,
    resolve_version_plan_uri,
    list_available_version_plans,
    VERSION_PLAN_URI_PATTERN,
    VERSION_PLAN_URI_SEARCH_PATTERN,
)

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

    await run_tasklist_agent(state, writer, lifecycle)
    lifecycle.emit_done_once()
    return True


def _list_version_plans() -> list[dict[str, str]]:
    return list_available_version_plans()


async def orchestrate_chat(
    session: ChatSession,
    writer: StreamWriter,
    context: dict[str, Any],
) -> None:
    """主编排函数 - 带错误恢复"""
    lifecycle = StreamLifecycle(writer)
    message_id = create_id()
    lifecycle.emit_start_once(message_id)

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


async def _do_orchestrate(
    session: ChatSession,
    writer: StreamWriter,
    context: dict[str, Any],
    lifecycle: StreamLifecycle,
) -> None:
    """执行编排逻辑：模型调用 → 工具执行 → 结果汇总"""
    current_messages = list(session.get_messages())
    skill = skill_registry.get(session.get_skill_id())
    result_policy = skill.result_policy if skill else "auto"
    output_policy = skill.output_policy if skill else "concise-utility"
    tool_names = skill.tool_names if skill else []

    tool_call_count = 0
    has_tool_calls = False
    has_authoritative_result = False
    tool_results: list[dict[str, Any]] = []

    while tool_call_count < MAX_TOOL_CALLS:
        response = await session.invoke_model(messages=current_messages)
        choice = response.choices[0]
        message = choice.message

        tool_calls = message.tool_calls or []

        if not tool_calls:
            if not has_tool_calls:
                content = message.content or ""
                if content:
                    lifecycle.write_chunk(create_text_chunk(content))
            break

        has_tool_calls = True

        # 将 AI 消息加入上下文
        ai_msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        ai_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
        current_messages.append(ai_msg)

        # 执行每个工具调用
        for tc in tool_calls:
            tool_name = tc.function.name
            tool_args_str = tc.function.arguments

            try:
                tool_args = json.loads(tool_args_str) if tool_args_str else {}
            except json.JSONDecodeError:
                tool_args = {}

            # 检查工具是否在技能范围内
            if tool_name not in tool_names:
                lifecycle.write_chunk(create_text_chunk(
                    f"⚠️ 工具 {tool_name} 不在当前技能的能力范围内"
                ))
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": f"工具 {tool_name} 不在技能范围内"}),
                })
                continue

            # 发送工具调用 chunk
            lifecycle.write_chunk(create_tool_call_chunk(tc.id, tool_name, tool_args))

            # 执行工具（带重试）
            tool_result_str = await _execute_tool_with_retry(
                tc.id, tool_name, tool_args, context, lifecycle
            )

            tool_def = tool_registry.get(tool_name)
            is_authoritative = tool_def.result_is_authoritative if tool_def else False

            if is_authoritative:
                has_authoritative_result = True

            tool_results.append({
                "toolName": tool_name,
                "result": tool_result_str,
                "isAuthoritative": is_authoritative,
            })

            lifecycle.write_chunk(create_tool_result_chunk(
                tc.id, tool_name, tool_result_str,
                is_valid=True,
                is_authoritative=is_authoritative,
            ))

            current_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result_str,
            })

            tool_call_count += 1

        if tool_call_count >= MAX_TOOL_CALLS:
            break

    # 后处理
    if has_tool_calls:
        if tool_results:
            if result_policy == "tool-first" and has_authoritative_result:
                authoritative_results = [r for r in tool_results if r["isAuthoritative"]]
                for tr in authoritative_results:
                    formatted = _format_tool_result_for_text(tr["result"], tr["toolName"])
                    lifecycle.write_chunk(create_text_chunk(formatted))
            else:
                await _generate_summary_answer(session, current_messages, lifecycle, output_policy)
        else:
            lifecycle.write_chunk(create_text_chunk("抱歉，工具调用失败，请稍后重试。"))

    lifecycle.emit_done_once()


async def _execute_tool_with_retry(
    tool_call_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    context: dict[str, Any],
    lifecycle: StreamLifecycle,
) -> str:
    """执行工具（带重试）"""
    attempts = 0
    max_attempts = 2

    while attempts <= max_attempts:
        try:
            result = await tool_registry.execute(tool_name, tool_args, context)
            return result
        except Exception as e:
            attempts += 1
            if attempts < max_attempts:
                lifecycle.write_chunk(create_recovering_chunk(
                    f"工具 {tool_name} 调用失败，正在重试...",
                    attempts, max_attempts,
                ))
                await asyncio.sleep(1.0)
            else:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps({"error": "未知错误"}, ensure_ascii=False)


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


async def _generate_summary_answer(
    session: ChatSession,
    current_messages: list[dict[str, Any]],
    lifecycle: StreamLifecycle,
    output_policy: str = "concise-utility",
) -> None:
    """生成总结回答"""
    output_instruction = ""
    if output_policy == "detailed-explanation":
        output_instruction = "\n\n请提供详细的解释，包括分析过程和步骤。"

    tool_messages = [m for m in current_messages if m.get("role") == "tool"]
    tool_result_text = "\n\n".join(m.get("content", "") for m in tool_messages)

    user_messages = [m for m in current_messages if m.get("role") == "user"]
    last_user_content = user_messages[-1]["content"] if user_messages else ""

    summary_messages = [
        {"role": "system", "content": session.get_system_prompt()},
        {
            "role": "user",
            "content": (
                f"用户问：{last_user_content}\n\n"
                f"工具调用结果：\n{tool_result_text}\n\n"
                f"请根据工具结果用自然语言总结回答用户。{output_instruction}"
            ),
        },
    ]

    response = await session.invoke_model(messages=summary_messages, tools=[])
    choice = response.choices[0]
    content = choice.message.content or ""
    if content:
        lifecycle.write_chunk(create_text_chunk(content))


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
