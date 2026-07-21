"""子代理工具 — 将 agent_loop 包装为可委托的工具

父 Agent 通过 delegate_sub_agent 工具委托子任务给专用子代理。
子代理拥有独立的 system_prompt + 工具集 + agent_loop，
运行完成后将结果作为 tool_result 返回给父 Agent。
"""

from typing import Any

from tool_registry import tool_registry, ChatToolDefinition
from sub_agent import run_sub_agent, list_sub_agent_types, MAX_SUB_AGENT_DEPTH


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """执行子代理委托

    args:
        task: 子任务描述 (必需)
        agent_type: 子代理类型 (research / analysis / writer, 默认 research)

    context (由 chat_orchestrator 注入):
        _lifecycle: StreamLifecycle — 用于发射流式事件
        _sub_agent_depth: int — 当前递归深度
        _run_id: str — 父代理的 run_id
    """
    task = args.get("task", "").strip()
    agent_type = args.get("agent_type", "research").strip()

    if not task:
        return {"error": "task 参数不能为空"}

    # 获取当前深度 (从 context 中)
    depth = context.get("_sub_agent_depth", 0)

    # 深度检查
    if depth >= MAX_SUB_AGENT_DEPTH:
        return {
            "error": f"子代理嵌套深度超过上限 ({MAX_SUB_AGENT_DEPTH})",
            "current_depth": depth,
        }

    # 运行子代理
    result = await run_sub_agent(agent_type, task, context, depth)

    return {
        "agent_type": agent_type,
        "task": task,
        "result": result,
    }


def register():
    """注册子代理工具"""
    # 动态生成工具描述（包含可用子代理类型）
    type_desc = "\n".join(
        f"    - {t.name}: {t.description}"
        for t in list_sub_agent_types()
    )

    tool_registry.register(ChatToolDefinition(
        name="delegate_sub_agent",
        description=(
            "委托子任务给专用子代理处理。子代理拥有独立的 system prompt 和工具集，"
            "可以独立运行多轮 LLM ↔ 工具循环，完成后将结果返回给父 Agent。\n\n"
            "适用场景:\n"
            "- 需要多步信息检索后汇总\n"
            "- 需要专业分析后给出结论\n"
            "- 需要生成特定格式的内容\n\n"
            "可用子代理类型:\n" + type_desc
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "委托给子代理的任务描述，应清晰明确",
                },
                "agent_type": {
                    "type": "string",
                    "description": "子代理类型",
                    "enum": [t.name for t in list_sub_agent_types()],
                    "default": "research",
                },
            },
            "required": ["task"],
        },
        execute=execute,
        format_input=lambda args: (
            f"委托子代理({args.get('agent_type', 'research')}): "
            f"{args.get('task', '')}"
        ),
        format_output=lambda result: (
            result.get("result", "") if isinstance(result, dict) else str(result)
        ),
        result_is_authoritative=False,
        planning_category="action",
        decision_weight=0.8,
        keywords=["委托", "子代理", "sub-agent", "delegate", "研究", "分析", "创作"],
    ))
