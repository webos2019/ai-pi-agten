"""Sub-Agent — 子代理系统

将 agent_loop 包装为可委托的子代理，让父 Agent 通过 tool_call 委托子任务。

设计理念:
  - 借鉴 Pi (pi.dev) extension sub-agents: 通过扩展实现子代理
  - 借鉴 LangGraph subgraph: 子图嵌套组合
  - 每个 Sub-Agent 有独立的 system_prompt + 工具子集 + agent_loop
  - 父 Agent 通过 delegate_sub_agent 工具委托子任务
  - 子 Agent 独立运行，结果作为 tool_result 返回给父 Agent
  - 递归深度限制 (MAX_SUB_AGENT_DEPTH) 防止无限嵌套

架构:
  Parent Agent Loop
    ├─ tool_call: delegate_sub_agent(task="...", agent_type="research")
    │    └─ run_sub_agent()
    │         ├─ 创建子 AgentContext (独立 system_prompt + tools)
    │         ├─ 运行子 agent_loop (LLM ↔ 工具循环)
    │         ├─ 发射 sub_agent_start / sub_agent_end 流式事件
    │         └─ 返回最终 assistant 消息作为 tool_result
    └─ 父 Agent 收到 tool_result，继续推理
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from agent_loop import (
    AgentContext,
    AgentMessage,
    AgentLoopConfig,
    ToolDefinition,
    AgentEvent,
    run_loop,
)
from deepseek import chat_completion
from stream import (
    create_id,
    create_text_chunk,
    create_tool_call_chunk,
    create_tool_result_chunk,
    create_sub_agent_start_chunk,
    create_sub_agent_end_chunk,
)
from tool_registry import tool_registry


# ─── 常量 ──────────────────────────────────────────────

MAX_SUB_AGENT_DEPTH = 2  # 最大嵌套深度 (0=父代理, 1=一级子代理, 2=二级子代理)
SUB_AGENT_MAX_TURNS = 8  # 子代理最大轮次
SUB_AGENT_TOOL_TIMEOUT = 60.0  # 子代理工具超时 (秒)


# ─── SubAgentType ─────────────────────────────────────

@dataclass
class SubAgentType:
    """子代理类型定义 — 每种类型有独立的 system_prompt 和工具子集"""
    name: str
    description: str
    system_prompt: str
    tool_names: list[str]
    model: str = "deepseek-chat"
    max_turns: int = SUB_AGENT_MAX_TURNS


# ─── 预定义子代理类型 ─────────────────────────────────

SUB_AGENT_TYPES: dict[str, SubAgentType] = {
    "research": SubAgentType(
        name="research",
        description="信息检索专家 — 擅长搜索网页、读取文件、获取天气和位置信息，并汇总为结构化报告",
        system_prompt=(
            "你是一个信息检索子代理。你的任务是高效地收集和整理信息。\n\n"
            "工作流程:\n"
            "1. 分析任务，确定需要哪些信息\n"
            "2. 使用可用工具获取信息\n"
            "3. 整理信息，输出结构化摘要\n\n"
            "要求:\n"
            "- 简洁高效，不做不必要的工具调用\n"
            "- 每条信息标注来源\n"
            "- 如果信息不足，明确说明缺失项\n"
        ),
        tool_names=["web_browse", "local-text-read", "list_files", "get_weather", "get_location"],
    ),
    "analysis": SubAgentType(
        name="analysis",
        description="数据分析专家 — 擅长数学计算、单位换算和文本处理",
        system_prompt=(
            "你是一个数据分析子代理。你的任务是进行精确的计算和数据分析。\n\n"
            "工作流程:\n"
            "1. 理解分析需求\n"
            "2. 使用计算器、单位换算等工具进行精确计算\n"
            "3. 给出分析结论\n\n"
            "要求:\n"
            "- 计算过程透明，展示中间步骤\n"
            "- 使用工具确保计算准确性\n"
            "- 结论清晰明确\n"
        ),
        tool_names=["calculator", "unit_convert", "text_transform"],
    ),
    "writer": SubAgentType(
        name="writer",
        description="内容创作专家 — 擅长撰写、改写和润色文本内容",
        system_prompt=(
            "你是一个内容创作子代理。你的任务是生成高质量的文本内容。\n\n"
            "要求:\n"
            "- 内容结构清晰\n"
            "- 语言流畅自然\n"
            "- 符合用户指定的风格和格式要求\n"
        ),
        tool_names=[],  # 纯 LLM，无工具
    ),
}


def get_sub_agent_type(name: str) -> SubAgentType | None:
    """获取子代理类型定义"""
    return SUB_AGENT_TYPES.get(name)


def list_sub_agent_types() -> list[SubAgentType]:
    """列出所有可用子代理类型"""
    return list(SUB_AGENT_TYPES.values())


# ─── 工具定义转换 ─────────────────────────────────────

def _build_tool_defs(
    tool_names: list[str],
    context: dict[str, Any],
) -> list[ToolDefinition]:
    """将 tool_registry 中的 ChatToolDefinition 桥接为 agent_loop 的 ToolDefinition

    与 chat_orchestrator._build_tool_definitions 相同的模式，
    但用于子代理的工具集。
    """
    defs: list[ToolDefinition] = []
    for name in tool_names:
        tool_def = tool_registry.get(name)
        if not tool_def:
            continue

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


# ─── 消息格式转换 ─────────────────────────────────────

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


def _map_stop_reason(finish_reason: str | None) -> str:
    """将 OpenAI finish_reason 映射为 agent_loop 的 stop_reason"""
    mapping = {
        "stop": "stop",
        "tool_calls": "tool_use",
        "length": "length",
        "content_filter": "error",
    }
    return mapping.get(finish_reason or "", "stop")


# ─── 子代理 stream_fn ─────────────────────────────────

async def _sub_agent_stream_fn(
    context: AgentContext,
    config: AgentLoopConfig,
) -> AgentMessage:
    """子代理的 stream_fn — 调用 LLM 获取 assistant 响应

    与 chat_orchestrator._default_stream_fn 相同的模式，
    但用于子代理的独立 agent_loop。
    """
    # 1. 构建消息列表
    llm_messages: list[dict[str, Any]] = [
        {"role": "system", "content": context.system_prompt}
    ]
    for msg in context.messages:
        llm_messages.append(_agent_message_to_openai(msg))

    # 2. 构建工具规格
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


# ─── 子代理事件转发 ───────────────────────────────────

def _emit_sub_agent_event(
    event: AgentEvent,
    lifecycle: Any,
    run_id: str,
    agent_type: str,
    depth: int,
) -> None:
    """将子代理的 AgentEvent 转为流式 chunk 并写入父流

    策略:
    - message_start (assistant + 有内容): 发射 text chunk (带前缀标记)
    - message_start (assistant + 有 tool_calls): 发射 tool_call chunks (source=sub-agent)
    - turn_end (有 tool_results): 发射 tool_result chunks (source=sub-agent)
    - 其他事件: 忽略 (避免噪音)
    """
    if not lifecycle:
        return

    prefix = "  " * (depth + 1)  # 缩进表示子代理层级

    if event.type == "message_start":
        msg = event.message
        if msg and msg.role == "assistant":
            if msg.content:
                lifecycle.write_chunk(create_text_chunk(
                    f"{prefix}🔍 [{agent_type}] {msg.content}"
                ))
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", create_id())
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("arguments", {})
                    lifecycle.write_chunk(create_tool_call_chunk(
                        tc_id, tc_name, tc_args,
                        source=f"sub-agent:{agent_type}",
                    ))

    elif event.type == "turn_end":
        if event.tool_results:
            for tr in event.tool_results:
                lifecycle.write_chunk(create_tool_result_chunk(
                    tr.tool_call_id,
                    tr.tool_name,
                    tr.result,
                    is_valid=not tr.is_error,
                    source=f"sub-agent:{agent_type}",
                ))


# ─── 结果提取 ─────────────────────────────────────────

def _extract_final_result(messages: list[AgentMessage]) -> str:
    """从子代理的消息列表中提取最终结果

    找到最后一条 assistant 消息的 content 作为结果。
    """
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.content:
            return msg.content
    return ""


# ─── 核心: 运行子代理 ─────────────────────────────────

async def run_sub_agent(
    agent_type: str,
    task: str,
    context: dict[str, Any],
    depth: int = 0,
) -> str:
    """运行子代理 — 创建独立的 agent_loop 并返回最终结果

    参数:
        agent_type: 子代理类型名称 (research / analysis / writer)
        task: 子任务描述
        context: 父代理的上下文 (含 _lifecycle, _sub_agent_depth 等)
        depth: 当前递归深度

    返回:
        子代理的最终 assistant 消息内容 (字符串)
    """
    # 深度检查
    if depth >= MAX_SUB_AGENT_DEPTH:
        return f"错误: 子代理嵌套深度超过上限 ({MAX_SUB_AGENT_DEPTH})，拒绝执行"

    # 获取子代理类型
    sub_type = get_sub_agent_type(agent_type)
    if not sub_type:
        available = ", ".join(SUB_AGENT_TYPES.keys())
        return f"错误: 未知子代理类型 '{agent_type}'。可用类型: {available}"

    # 获取 lifecycle (用于发射流式事件)
    lifecycle = context.get("_lifecycle")

    # 获取父级 run_id (用于关联事件)
    parent_run_id = context.get("_run_id", "")

    # 生成子代理 run_id
    sub_run_id = create_id()

    # 发射 sub_agent_start 事件
    if lifecycle:
        lifecycle.write_chunk(create_sub_agent_start_chunk(
            run_id=sub_run_id,
            agent_type=agent_type,
            task=task,
            depth=depth,
            parent_run_id=parent_run_id,
        ))

    t0 = time.time()

    try:
        # 构建子代理上下文 (深度 +1，传递 lifecycle)
        child_context = {**context, "_sub_agent_depth": depth + 1}

        # 构建工具定义
        tool_defs = _build_tool_defs(sub_type.tool_names, child_context)

        # 构建 AgentContext
        agent_context = AgentContext(
            system_prompt=sub_type.system_prompt,
            messages=[AgentMessage(role="user", content=task)],
            tools=tool_defs,
            model=sub_type.model,
        )

        # 构建 AgentLoopConfig
        config = AgentLoopConfig(
            stream_fn=_sub_agent_stream_fn,
            tool_timeout=SUB_AGENT_TOOL_TIMEOUT,
            max_turns=sub_type.max_turns,
        )

        # 构建 emit 回调 — 将子代理事件转发到父流
        async def emit(event: AgentEvent) -> None:
            _emit_sub_agent_event(event, lifecycle, sub_run_id, agent_type, depth)

        # 运行 agent_loop
        new_messages: list[AgentMessage] = []
        await run_loop(agent_context, new_messages, config, None, emit)

        # 提取最终 assistant 消息
        result = _extract_final_result(new_messages)

        duration_ms = int((time.time() - t0) * 1000)

        # 发射 sub_agent_end 事件
        if lifecycle:
            lifecycle.write_chunk(create_sub_agent_end_chunk(
                run_id=sub_run_id,
                agent_type=agent_type,
                status="success",
                result_summary=result[:200] if result else "(空)",
                duration_ms=duration_ms,
                depth=depth,
            ))

        return result or "(子代理未产生输出)"

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)

        # 发射 sub_agent_end 事件 (error)
        if lifecycle:
            lifecycle.write_chunk(create_sub_agent_end_chunk(
                run_id=sub_run_id,
                agent_type=agent_type,
                status="error",
                result_summary=str(e)[:200],
                duration_ms=duration_ms,
                depth=depth,
            ))

        return f"子代理执行出错: {e}"
