"""工具注册表 — 工具定义 + OpenAI function calling 规格生成"""

import json
from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field


@dataclass
class ChatToolDefinition:
    """单个工具的定义"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    execute: Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]
    format_input: Callable[[dict[str, Any]], str] | None = None
    format_output: Callable[[Any], str] | None = None
    result_is_authoritative: bool = False
    keywords: list[str] = field(default_factory=list)
    planning_category: str = "utility"
    decision_weight: float = 0.5

    def get_openai_tool_spec(self) -> dict[str, Any]:
        """生成 OpenAI function calling 格式的工具定义"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表 — 全局单例"""

    def __init__(self):
        self._tools: dict[str, ChatToolDefinition] = {}

    def register(self, tool_def: ChatToolDefinition) -> "ToolRegistry":
        if tool_def.name in self._tools:
            raise ValueError(f'工具 "{tool_def.name}" 已注册')
        self._tools[tool_def.name] = tool_def
        return self

    def get(self, name: str) -> ChatToolDefinition | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[ChatToolDefinition]:
        return list(self._tools.values())

    def get_openai_tool_specs(self, tool_names: list[str]) -> list[dict[str, Any]]:
        """获取指定工具的 OpenAI 格式定义"""
        specs = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool:
                specs.append(tool.get_openai_tool_spec())
        return specs

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        """执行工具，返回 JSON 字符串结果"""
        tool = self._tools.get(name)
        if not tool:
            return f'{{"error": "未知工具: {name}"}}'
        try:
            ctx = context or {}
            result = await tool.execute(args, ctx)
            if tool.format_output:
                return tool.format_output(result)
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def recommend_tool(self, user_input: str) -> ChatToolDefinition | None:
        """根据关键词命中度推荐工具"""
        input_lower = user_input.lower()
        scored = []
        for tool in self._tools.values():
            score = sum(1 for k in tool.keywords if k.lower() in input_lower)
            if score > 0:
                score += tool.decision_weight * 0.5
            scored.append((tool, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        if scored and scored[0][1] > 0:
            return scored[0][0]
        return None


# 全局工具注册表实例
tool_registry = ToolRegistry()
