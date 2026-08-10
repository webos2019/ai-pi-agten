"""日志检索工具 — 按关键词/级别/时间范围搜索日志文件"""

import os
import re
import glob
from datetime import datetime, timedelta
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


def _search_log_file(
    filepath: str,
    keyword: str | None = None,
    level: str | None = None,
    lines_after: int = 3,
    lines_before: int = 1,
    max_matches: int = 50,
) -> list[dict]:
    """搜索单个日志文件"""
    results = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # 构建搜索 pattern
        patterns = []
        if keyword:
            patterns.append(re.compile(re.escape(keyword), re.IGNORECASE))
        if level:
            patterns.append(re.compile(rf"\b{level}\b", re.IGNORECASE))

        for i, line in enumerate(lines):
            matched = all(p.search(line) for p in patterns) if patterns else True
            if matched:
                start = max(0, i - lines_before)
                end = min(len(lines), i + lines_after + 1)
                context = "".join(lines[start:end]).strip()
                results.append({
                    "file": os.path.basename(filepath),
                    "line_number": i + 1,
                    "matched_line": line.strip(),
                    "context": context,
                })
                if len(results) >= max_matches:
                    break
    except Exception as e:
        results.append({"file": filepath, "error": str(e)})
    return results


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    log_dir = args.get("log_dir", "/var/log")
    keyword = args.get("keyword")
    level = args.get("level")  # ERROR / WARN / INFO
    file_pattern = args.get("file_pattern", "*.log")
    lines_after = args.get("lines_after", 3)
    lines_before = args.get("lines_before", 1)
    max_matches = args.get("max_matches", 50)

    # 查找匹配的日志文件
    search_pattern = os.path.join(log_dir, "**", file_pattern)
    log_files = glob.glob(search_pattern, recursive=True)

    if not log_files:
        # 如果目录不存在，返回模拟结果（开发环境兼容）
        return {
            "log_dir": log_dir,
            "keyword": keyword,
            "level": level,
            "files_searched": 0,
            "matches": [],
            "note": f"日志目录 {log_dir} 未找到匹配文件，请确认路径正确",
        }

    all_results = []
    for filepath in log_files[:10]:  # 限制最多搜索10个文件
        matches = _search_log_file(filepath, keyword, level, lines_after, lines_before, max_matches)
        all_results.extend(matches)

    return {
        "log_dir": log_dir,
        "keyword": keyword,
        "level": level,
        "files_searched": len(log_files[:10]),
        "total_matches": len(all_results),
        "matches": all_results[:max_matches],
    }


def register():
    tool_registry.register(ChatToolDefinition(
        name="log_search",
        description="搜索日志文件，支持关键词和级别过滤",
        parameters={
            "type": "object",
            "properties": {
                "log_dir": {"type": "string"},
                "keyword": {"type": "string"},
                "level": {
                    "type": "string",
                    "enum": ["ERROR", "WARN", "INFO", "DEBUG", "FATAL"],
                },
                "file_pattern": {"type": "string"},
                "lines_after": {"type": "integer"},
                "lines_before": {"type": "integer"},
                "max_matches": {"type": "integer"},
            },
            "required": ["log_dir"],
        },
        execute=execute,
        format_input=lambda args: f"日志搜索: {args.get('log_dir', '')} keyword={args.get('keyword', '')} level={args.get('level', '')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["日志", "搜索", "检索", "grep", "error", "log", "search", "排查"],
    ))
