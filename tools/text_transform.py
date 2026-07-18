"""文本转换工具 — markdown 转纯文本 / 提取链接 / 提取代码块 / JSON 美化"""

import re
import json
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    content = args.get("content", "")
    action = args.get("action", "")

    if action == "markdown_to_text":
        text = re.sub(r"[#*`>\[\]]", "", content)
        text = re.sub(r"\n{2,}", "\n", text).strip()
        return {"action": action, "result": text}

    elif action == "extract_links":
        links = []
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
            links.append({"text": match.group(1), "url": match.group(2)})
        return {"action": action, "links": links, "count": len(links)}

    elif action == "extract_code_blocks":
        blocks = []
        for match in re.finditer(r"```(\w+)?\n([\s\S]*?)```", content):
            blocks.append({
                "language": match.group(1) or "text",
                "code": match.group(2).strip(),
            })
        return {"action": action, "blocks": blocks, "count": len(blocks)}

    elif action == "json_pretty":
        try:
            parsed = json.loads(content)
            return {"action": action, "result": json.dumps(parsed, indent=2, ensure_ascii=False)}
        except Exception:
            return {"action": action, "error": "无效的 JSON 格式"}

    return {"action": action, "error": "未知操作"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="text_transform",
        description="文本转换：markdown转文本、提取链接、提取代码块、JSON美化",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要转换的文本内容",
                },
                "action": {
                    "type": "string",
                    "enum": ["markdown_to_text", "extract_links", "extract_code_blocks", "json_pretty"],
                    "description": "转换操作",
                },
            },
            "required": ["content", "action"],
        },
        execute=execute,
        planning_category="utility",
        decision_weight=0.5,
        keywords=["markdown", "链接", "代码", "json", "格式化"],
    ))
