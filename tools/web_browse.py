"""网页浏览工具 — 抓取网页文本内容"""

import re
import json
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "")
    max_chars = int(args.get("maxChars", 2000))

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return {"url": url, "error": f"HTTP {resp.status_code}"}

            text = resp.text
            # 移除脚本、样式与 HTML 标签
            text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip()

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return {
                "url": url,
                "content": text,
                "truncated": truncated,
            }
    except Exception as e:
        return {"url": url, "error": f"网页浏览失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="web_browse",
        description="浏览网页内容，提取正文文本",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要浏览的网页URL"},
                "maxChars": {"type": "number", "description": "最大返回字符数，默认2000"},
            },
            "required": ["url"],
        },
        execute=execute,
        format_input=lambda args: f"浏览网页: {args.get('url', '')}",
        planning_category="information",
        decision_weight=0.75,
        keywords=["网页", "网站", "URL", "浏览", "内容"],
    ))
