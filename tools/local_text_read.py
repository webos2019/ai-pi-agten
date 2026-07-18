"""本地文本文件读取工具 — 仅允许读取项目根目录下的直接文件"""

import os
import json
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".md", ".json", ".yaml", ".yml", ".css", ".scss", ".sql",
    ".sh", ".bash", ".toml", ".xml", ".html", ".vue", ".svelte",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
    ".dart", ".txt",
}
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB


def _assert_safe_filename(filename: str) -> str:
    """路径安全检查：禁止子目录、路径遍历、隐藏文件、绝对路径"""
    if "/" in filename or "\\" in filename:
        raise ValueError("访问被拒绝：仅支持读取项目根目录下的直接文件，不支持子目录路径")
    if ".." in filename:
        raise ValueError("访问被拒绝：不允许路径遍历")
    if filename.startswith("."):
        raise ValueError("访问被拒绝：不允许访问隐藏文件")
    if os.path.isabs(filename):
        raise ValueError("访问被拒绝：不允许绝对路径")
    return filename


def _assert_allowed_extension(filename: str) -> None:
    ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'不支持的文件类型 "{ext}"')


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    filename = args.get("filename", "")
    if not filename:
        return {"error": "文件名不能为空"}

    safe_filename = _assert_safe_filename(filename)
    _assert_allowed_extension(safe_filename)

    base_dir = os.getcwd()
    full_path = os.path.join(base_dir, safe_filename)

    if not os.path.isfile(full_path):
        return {"filename": safe_filename, "error": "文件不存在"}

    file_size = os.path.getsize(full_path)
    if file_size > MAX_FILE_SIZE:
        return {"filename": safe_filename, "error": "文件超出大小限制（最大 1MB）"}

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"filename": safe_filename, "content": content}


def register():
    tool_registry.register(ChatToolDefinition(
        name="local-text-read",
        description="读取本地文本文件内容",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要读取的文件名",
                }
            },
            "required": ["filename"],
        },
        execute=execute,
        format_input=lambda args: f"读取文件: {args.get('filename', '')}",
        result_is_authoritative=True,
        planning_category="information",
        decision_weight=0.7,
        keywords=["文件", "读取", "本地", "内容"],
    ))
