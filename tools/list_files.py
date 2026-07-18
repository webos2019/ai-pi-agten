"""列出项目根目录文件工具"""

import os
import json
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    base_dir = os.getcwd()
    try:
        entries = []
        for name in os.listdir(base_dir):
            if name.startswith("."):
                continue
            full_path = os.path.join(base_dir, name)
            if os.path.isfile(full_path):
                entries.append(name)
        return {"files": entries}
    except Exception as e:
        return {"error": f"读取目录失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="list_files",
        description="列出项目根目录下的文件",
        parameters={
            "type": "object",
            "properties": {},
        },
        execute=execute,
        format_input=lambda args: "列出文件",
        planning_category="information",
        decision_weight=0.6,
        keywords=["文件", "列表", "目录", "项目"],
    ))
