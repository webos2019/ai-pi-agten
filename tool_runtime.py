"""Tool Runtime — 能力驱动的工具编排引擎

基于能力的工具编排：Intent 检测 -> 工具解析 -> 执行规划 -> 上下文增强。
用于将结构化请求（chips）与用户意图映射到具体工具。
"""

from __future__ import annotations
import re
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition
from skill_registry import skill_registry, SkillMeta


class Capability:
    """工具能力描述 — 关键词模式 + 提供的输出类型"""

    def __init__(self, name: str, description: str, patterns: list[str],
                 requires_context: list[str] | None = None,
                 provides: list[str] | None = None,
                 priority: int = 0):
        self.name = name
        self.description = description
        self.patterns = patterns
        self.requires_context = requires_context or []
        self.provides = provides or []
        self.priority = priority


class ToolRuntime:
    """基于能力的工具运行时"""

    def __init__(self):
        self.capabilities: dict[str, Capability] = {}
        self.intent_patterns: list[tuple[str, str, float]] = []  # (pattern, capability, weight)
        self._register_default_capabilities()

    def _register_default_capabilities(self):
        """注册默认工具能力"""
        capabilities = [
            Capability(
                name="math_computation",
                description="执行数学计算、表达式求值、公式推导",
                patterns=[r"计算", r"等于", r"\d+[\+\-\*\/]\d+", r"数学", r"公式", r"求值", r"sum", r"total", r"calculator"],
                provides=["numerical_result"],
                priority=10,
            ),
            Capability(
                name="datetime_query",
                description="获取当前时间、日期查询、时间换算",
                patterns=[r"时间", r"日期", r"几点", r"星期", r"现在", r"today", r"time", r"date", r"datetime"],
                provides=["temporal_info"],
                priority=8,
            ),
            Capability(
                name="text_transformation",
                description="文本格式转换、Markdown处理、JSON美化、链接提取",
                patterns=[r"markdown", r"转换", r"提取链接", r"json.*美化", r"文本", r"格式化", r"transform", r"text"],
                provides=["transformed_text", "structured_data"],
                priority=5,
            ),
            Capability(
                name="unit_conversion",
                description="单位换算：长度、重量、温度等",
                patterns=[r"换算", r"转换.*单位", r"公里.*米", r"千克.*磅", r"celsius", r"fahrenheit", r"unit", r"convert"],
                provides=["converted_value"],
                priority=7,
            ),
            Capability(
                name="location_lookup",
                description="通过IP获取地理位置信息",
                patterns=[r"位置", r"城市", r"ip.*地址", r"location", r"在哪", r"地区", r"地理", r"定位"],
                provides=["location_info"],
                priority=6,
            ),
            Capability(
                name="weather_query",
                description="查询城市实时天气信息",
                patterns=[r"天气", r"温度", r"下雨", r"weather", r"forecast", r"气候", r"预报"],
                provides=["weather_info"],
                priority=9,
            ),
            Capability(
                name="web_browsing",
                description="浏览网页内容、获取在线信息",
                patterns=[r"网页", r"网站", r"url", r"http", r"browse", r"web", r"在线"],
                provides=["web_content"],
                priority=4,
            ),
            Capability(
                name="file_reading",
                description="读取本地文件内容",
                patterns=[r"读取文件", r"文件内容", r"打开文件", r"read.*file", r"查看.*文件"],
                provides=["file_content"],
                priority=6,
            ),
            Capability(
                name="file_listing",
                description="列出项目目录结构",
                patterns=[r"目录", r"文件列表", r"项目结构", r"list.*files", r"files", r"ls"],
                provides=["file_list"],
                priority=3,
            ),
        ]

        for cap in capabilities:
            self.capabilities[cap.name] = cap
            for pattern in cap.patterns:
                self.intent_patterns.append((pattern, cap.name, cap.priority / 10.0))

    def detect_intent(self, user_input: str) -> list[tuple[str, float]]:
        """检测用户意图，返回匹配的能力及置信度（降序）"""
        input_lower = user_input.lower()
        scores: dict[str, float] = {}

        for pattern, cap_name, weight in self.intent_patterns:
            if re.search(pattern, input_lower, re.IGNORECASE):
                scores[cap_name] = scores.get(cap_name, 0) + weight

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def resolve_tools(self, skill_id: str, user_input: str, structured: dict[str, Any] | None = None) -> list[str]:
        """根据技能和用户输入解析需要使用的工具列表"""
        skill = skill_registry.get(skill_id)
        if not skill:
            return []

        available_tools = skill.tool_names
        intent = self.detect_intent(user_input)

        # 从结构化请求中提取 chip 指定的工具
        chip_tools: list[str] = []
        if structured and structured.get("chips"):
            for chip in structured["chips"]:
                if chip.get("chipType") == "tool" or chip.get("type") == "tool":
                    tool_label = chip.get("label", "")
                    for t in available_tools:
                        if tool_label.lower() in t.lower() or t.lower() in tool_label.lower():
                            chip_tools.append(t)
                            break

        # 从意图中推荐工具
        recommended: list[tuple[str, float]] = []
        for cap_name, confidence in intent:
            tool_name = self._capability_to_tool(cap_name)
            if tool_name and tool_name in available_tools:
                recommended.append((tool_name, confidence))

        # 合并 chip 工具和推荐工具（去重）
        resolved: list[str] = []
        seen: set[str] = set()
        for t in chip_tools:
            if t not in seen:
                resolved.append(t)
                seen.add(t)
        for t, _ in recommended:
            if t not in seen and t in available_tools:
                resolved.append(t)
                seen.add(t)

        return resolved

    def _capability_to_tool(self, cap_name: str) -> str | None:
        mapping = {
            "math_computation": "calculator",
            "datetime_query": "datetime",
            "text_transformation": "text_transform",
            "unit_conversion": "unit_convert",
            "location_lookup": "get_location",
            "weather_query": "get_weather",
            "web_browsing": "web_browse",
            "file_reading": "local-text-read",
            "file_listing": "list_files",
        }
        return mapping.get(cap_name)

    def plan_execution(self, user_input: str, resolved_tools: list[str], context: dict[str, Any]) -> list[dict[str, Any]]:
        """规划工具执行顺序（按优先级排序）"""
        plan = []
        for tool_name in resolved_tools:
            tool_def = tool_registry.get(tool_name)
            if not tool_def:
                continue
            tool_args = self._infer_tool_args(tool_name, user_input, context)
            plan.append({
                "tool": tool_name,
                "args": tool_args,
                "capability": self._tool_to_capability(tool_name),
                "priority": tool_def.decision_weight,
            })
        plan.sort(key=lambda x: x["priority"], reverse=True)
        return plan

    def _tool_to_capability(self, tool_name: str) -> str | None:
        mapping = {
            "calculator": "math_computation",
            "datetime": "datetime_query",
            "text_transform": "text_transformation",
            "unit_convert": "unit_conversion",
            "get_location": "location_lookup",
            "get_weather": "weather_query",
            "web_browse": "web_browsing",
            "local-text-read": "file_reading",
            "list_files": "file_listing",
        }
        return mapping.get(tool_name)

    def _infer_tool_args(self, tool_name: str, user_input: str, context: dict[str, Any]) -> dict[str, Any]:
        """根据用户输入推断工具参数"""
        args: dict[str, Any] = {}

        if tool_name == "calculator":
            expr_match = re.search(r"[\d\+\-\*\/\(\)\.\s]+", user_input)
            if expr_match:
                args["expression"] = expr_match.group().strip()

        elif tool_name == "get_weather":
            city_match = re.search(
                r"(?:查询|查看|天气|weather)\s*[:：]?\s*([^\s,，。！？]+)",
                user_input, re.IGNORECASE,
            )
            args["city"] = city_match.group(1) if city_match else "北京"

        elif tool_name == "local-text-read":
            if context.get("filename"):
                args["filename"] = context["filename"]

        return args

    def enhance_context(self, user_input: str, skill_id: str, structured: dict[str, Any] | None = None) -> str:
        """增强上下文：将结构化请求中的 chip 引用信息注入到用户消息中"""
        enhanced = user_input

        if structured and structured.get("chips"):
            chip_contexts = []
            for chip in structured["chips"]:
                chip_type = chip.get("chipType") or chip.get("type") or ""
                label = chip.get("label", "")
                data = chip.get("data", {})

                if chip_type == "tool" and isinstance(data, dict) and data.get("toolName"):
                    chip_contexts.append(f"[引用工具: {data['toolName']}]")
                elif chip_type == "file" and isinstance(data, dict) and data.get("file"):
                    content = data.get("content", "")
                    if content:
                        preview = content[:200] + "..." if len(content) > 200 else content
                        chip_contexts.append(f"[引用文件: {data['file']}]\n```\n{preview}\n```")
                    else:
                        chip_contexts.append(f"[引用文件: {data['file']}]")
                elif chip_type == "context":
                    chip_contexts.append(f"[引用上下文: {label}]")

            if chip_contexts:
                enhanced = user_input + "\n\n---\n" + "\n".join(chip_contexts)

        return enhanced


# 全局 Tool Runtime 实例
tool_runtime = ToolRuntime()
