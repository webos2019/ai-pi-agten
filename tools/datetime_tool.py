"""日期时间工具"""

from datetime import datetime
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    fmt = args.get("format", "YYYY-MM-DD HH:mm:ss")
    now = datetime.now()
    weekdays = ["日", "一", "二", "三", "四", "五", "六"]
    format_map = {
        "YYYY-MM-DD": now.strftime("%Y-%m-%d"),
        "HH:mm:ss": now.strftime("%H:%M:%S"),
        "YYYY-MM-DD HH:mm:ss": now.strftime("%Y-%m-%d %H:%M:%S"),
        "YYYY年MM月DD日": f"{now.year}年{now.month:02d}月{now.day:02d}日",
        "星期": weekdays[now.weekday()],
    }
    return {
        "currentTime": format_map.get(fmt, now.isoformat()),
        "weekday": weekdays[now.weekday()],
    }


def register():
    tool_registry.register(ChatToolDefinition(
        name="datetime",
        description="获取当前日期和时间",
        parameters={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "description": "输出格式，如: YYYY-MM-DD HH:mm:ss",
                }
            },
        },
        execute=execute,
        format_input=lambda args: f"获取时间，格式: {args.get('format', '')}" if args.get('format') else "获取时间",
        result_is_authoritative=True,
        planning_category="information",
        decision_weight=0.8,
        keywords=["时间", "日期", "现在", "星期", "几点"],
    ))
