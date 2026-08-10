"""YouTube 视频分析工具 — 字幕提取 + 元信息获取

使用 yt-dlp 获取:
- 视频元信息（标题、频道、时长、观看数等）
- 字幕/CC（自动生成或手动上传的字幕）
- 字幕文本摘要（供 LLM 分析）

支持:
- YouTube URL 或视频 ID
- 多语言字幕选择
- 字幕格式: 纯文本（去除时间戳）
"""

import os
import re
import asyncio
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


def _extract_video_id(input_str: str) -> str | None:
    """从各种 YouTube URL 格式或纯 ID 中提取视频 ID"""
    input_str = input_str.strip()

    # 纯 ID (11 字符)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', input_str):
        return input_str

    # 各种 URL 格式
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, input_str)
        if match:
            return match.group(1)

    return None


async def _get_video_info(video_id: str) -> dict[str, Any]:
    """使用 yt-dlp 获取视频元信息"""
    url = f"https://www.youtube.com/watch?v={video_id}"

    # yt-dlp 命令行参数 — 只获取信息，不下载
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")[:200]
            return {"error": f"yt-dlp 获取信息失败: {error_msg}"}

        import json
        data = json.loads(stdout.decode("utf-8", errors="replace"))

        return {
            "video_id": video_id,
            "title": data.get("title", ""),
            "channel": data.get("uploader", "") or data.get("channel", ""),
            "duration": data.get("duration", 0),
            "view_count": data.get("view_count", 0),
            "like_count": data.get("like_count", 0),
            "upload_date": data.get("upload_date", ""),
            "description": (data.get("description") or "")[:1000],
            "tags": data.get("tags", []),
            "categories": data.get("categories", []),
            "url": url,
        }

    except asyncio.TimeoutError:
        return {"error": "yt-dlp 超时（30秒）"}
    except Exception as e:
        return {"error": f"获取视频信息失败: {e}"}


async def _get_subtitles(video_id: str, lang: str = "en") -> str:
    """使用 yt-dlp 获取字幕文本"""
    url = f"https://www.youtube.com/watch?v={video_id}"

    # 先尝试手动上传的字幕，再尝试自动生成的
    cmd = [
        "yt-dlp",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", lang,
        "--sub-format", "vtt",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        "-o", "pipe:",  # 输出到 stdout
        url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        # yt-dlp 字幕输出在 stderr 中（进度信息混合）
        # 实际字幕文件内容需要从文件系统或 --print-subs 读取
        # 改用直接提取方式

        # 如果上述方式失败，尝试用 yt-dlp --dump-json 获取字幕 URL
        cmd2 = [
            "yt-dlp",
            "--dump-json",
            "--no-warnings",
            "--no-playlist",
            "--skip-download",
            url,
        ]

        proc2 = await asyncio.create_subprocess_exec(
            *cmd2,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)

        if proc2.returncode != 0:
            return ""

        import json
        data = json.loads(stdout2.decode("utf-8", errors="replace"))

        # 查找字幕
        subtitles = data.get("subtitles", {}) or {}
        auto_subs = data.get("automatic_captions", {}) or {}

        # 优先手动字幕，其次自动字幕
        subs = subtitles.get(lang, []) or auto_subs.get(lang, [])

        if not subs:
            # 尝试任何可用语言
            if subtitles:
                any_lang = list(subtitles.keys())[0]
                subs = subtitles[any_lang]
            elif auto_subs:
                any_lang = list(auto_subs.keys())[0]
                subs = auto_subs[any_lang]

        if not subs:
            return ""

        # 获取 vtt 或 json3 格式的字幕
        sub_url = None
        for s in subs:
            if s.get("ext") in ("vtt", "json3", "srv1"):
                sub_url = s.get("url")
                break
            if s.get("ext") == "tt":
                sub_url = s.get("url")
                break

        if not sub_url and subs:
            sub_url = subs[0].get("url")

        if not sub_url:
            return ""

        # 下载字幕内容
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(sub_url)
            if resp.status_code != 200:
                return ""

            raw = resp.text

            # 解析 VTT 格式 — 去除时间戳和标签
            lines = raw.split("\n")
            text_lines = []
            seen = set()
            for line in lines:
                line = line.strip()
                # 跳过 VTT 头部、时间戳行、空行、标签行
                if not line:
                    continue
                if line.startswith("WEBVTT"):
                    continue
                if "-->" in line:
                    continue
                if line.startswith("<"):
                    continue
                if line.startswith("NOTE"):
                    continue
                # 去除 HTML 标签
                clean = re.sub(r"<[^>]+>", "", line)
                if clean and clean not in seen:
                    seen.add(clean)
                    text_lines.append(clean)

            return " ".join(text_lines)

    except asyncio.TimeoutError:
        return ""
    except Exception:
        return ""


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    video_input = args.get("url", "").strip()
    lang = args.get("lang", "en")
    max_chars = int(args.get("max_chars", 5000))
    include_subtitles = args.get("include_subtitles", True)

    if not video_input:
        return {"error": "url 参数不能为空（YouTube URL 或视频 ID）"}

    video_id = _extract_video_id(video_input)
    if not video_id:
        return {"error": f"无法解析 YouTube 视频: {video_input}"}

    # 获取视频信息
    info = await _get_video_info(video_id)

    if "error" in info:
        return info

    result: dict[str, Any] = {
        "video_id": video_id,
        **info,
    }

    # 获取字幕
    if include_subtitles:
        subtitle_text = await _get_subtitles(video_id, lang)
        if subtitle_text:
            if len(subtitle_text) > max_chars:
                subtitle_text = subtitle_text[:max_chars] + "\n\n... (字幕已截断)"
            result["subtitle"] = subtitle_text
            result["subtitle_lang"] = lang
        else:
            result["subtitle"] = ""
            result["subtitle_message"] = f"未找到 {lang} 字幕（视频可能没有字幕）"

    return result


def register():
    tool_registry.register(ChatToolDefinition(
        name="youtube_analyze",
        description="获取YouTube视频元信息和字幕文本",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "lang": {"type": "string", "default": "en"},
                "include_subtitles": {"type": "boolean", "default": True},
                "max_chars": {"type": "number", "default": 5000},
            },
            "required": ["url"],
        },
        execute=execute,
        format_input=lambda args: f"YouTube: {args.get('url', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.85,
        keywords=["youtube", "视频", "video", "字幕", "subtitle", "字幕"],
    ))
