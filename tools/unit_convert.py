"""单位换算工具 — 长度 / 重量 / 温度"""

from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


def convert_unit(value: float, from_name: str, to_name: str) -> float | str:
    conversions: dict[str, dict[str, Any]] = {
        "meter": {"kilometer": lambda v: v / 1000, "centimeter": lambda v: v * 100, "mile": lambda v: v * 0.000621371},
        "kilometer": {"meter": lambda v: v * 1000, "mile": lambda v: v * 0.621371},
        "kilogram": {"gram": lambda v: v * 1000, "pound": lambda v: v * 2.20462},
        "pound": {"kilogram": lambda v: v * 0.453592},
        "celsius": {"fahrenheit": lambda v: (v * 9) / 5 + 32, "kelvin": lambda v: v + 273.15},
        "fahrenheit": {"celsius": lambda v: ((v - 32) * 5) / 9},
    }
    if from_name not in conversions or to_name not in conversions[from_name]:
        return "不支持的单位转换"
    return conversions[from_name][to_name](value)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    value = float(args.get("value", 0))
    from_name = args.get("fromName", "")
    to_name = args.get("toName", "")
    result = convert_unit(value, from_name, to_name)
    return {"value": value, "fromName": from_name, "toName": to_name, "result": result}


def register():
    tool_registry.register(ChatToolDefinition(
        name="unit_convert",
        description="单位换算，支持长度、重量、温度等",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "要转换的值"},
                "fromName": {"type": "string", "description": "源单位，如: meter, kilogram, celsius"},
                "toName": {"type": "string", "description": "目标单位，如: kilometer, pound, fahrenheit"},
            },
            "required": ["value", "fromName", "toName"],
        },
        execute=execute,
        format_input=lambda args: f"{args.get('value', 0)} {args.get('fromName', '')} -> {args.get('toName', '')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.85,
        keywords=["换算", "单位", "转换", "公里", "米", "千克", "磅", "度"],
    ))
