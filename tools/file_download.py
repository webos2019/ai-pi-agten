"""文件下载存储工具 — 从 URL 下载文件并保存到本地 downloads/ 目录

支持能力:
- 下载任意 URL 指向的文件（图片、PDF、文档、音频、视频、压缩包等）
- 自动推断文件扩展名（从 URL 或 Content-Type）
- 存储到项目根目录下的 downloads/ 目录
- 列出已下载的文件
- 删除已下载的文件
- 读取已下载的文本文件内容

安全策略:
- 限制最大文件大小（默认 50MB）
- 文件名安全化（去除路径遍历字符）
- 仅存储到 downloads/ 目录
"""

import os
import re
import time
import hashlib
from urllib.parse import urlparse, unquote
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition

# ─── 配置 ────────────────────────────────────────────────

DOWNLOADS_DIR = os.path.join(os.getcwd(), "downloads")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Content-Type → 扩展名映射
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/x-msvideo": ".avi",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/css": ".css",
    "text/javascript": ".js",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/epub+zip": ".epub",
    "application/x-mobipocket-ebook": ".mobi",
    "application/octet-stream": "",  # 未知类型，依赖 URL 扩展名
}

# 允许读取内容的文本文件扩展名
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".css",
    ".js", ".ts", ".py", ".java", ".c", ".cpp", ".h", ".go",
    ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".sql",
}


def _ensure_downloads_dir() -> str:
    """确保 downloads 目录存在"""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    return DOWNLOADS_DIR


def _sanitize_filename(filename: str) -> str:
    """文件名安全化：去除路径分隔符、特殊字符"""
    # 去除路径部分
    filename = os.path.basename(filename)
    # URL 解码
    filename = unquote(filename)
    # 去除特殊字符（保留中文、字母、数字、点、横线、下划线）
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    # 去除前后点和空格
    filename = filename.strip('. ')
    # 如果文件名为空或过长
    if not filename:
        filename = f"file_{int(time.time())}"
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200 - len(ext)] + ext
    return filename


def _infer_extension(url: str, content_type: str) -> str:
    """从 URL 或 Content-Type 推断文件扩展名"""
    # 先从 URL 路径提取
    parsed = urlparse(url)
    url_path = unquote(parsed.path)
    _, ext = os.path.splitext(url_path)
    if ext and len(ext) <= 10:
        return ext.lower()

    # 从 Content-Type 推断
    ct = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(ct, "")


def _generate_filename(url: str, content_type: str, custom_name: str = "") -> str:
    """生成最终文件名"""
    if custom_name:
        name = _sanitize_filename(custom_name)
        # 如果没有扩展名，尝试推断
        if not os.path.splitext(name)[1]:
            ext = _infer_extension(url, content_type)
            if ext:
                name += ext
        return name

    # 从 URL 提取文件名
    parsed = urlparse(url)
    url_filename = os.path.basename(unquote(parsed.path))

    if url_filename and url_filename != "/":
        return _sanitize_filename(url_filename)

    # URL 没有文件名 → 用 URL hash 生成
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    ext = _infer_extension(url, content_type)
    return f"download_{url_hash}{ext}"


def _unique_filepath(filename: str) -> tuple[str, str]:
    """确保文件名唯一，返回 (完整路径, 最终文件名)"""
    _ensure_downloads_dir()
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(filepath):
        return filepath, filename

    # 文件已存在 → 添加序号
    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_name = f"{name}_{counter}{ext}"
        new_path = os.path.join(DOWNLOADS_DIR, new_name)
        if not os.path.exists(new_path):
            return new_path, new_name
        counter += 1


def _format_file_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"


# ─── 执行函数 ────────────────────────────────────────────

async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """文件下载存储工具主入口"""
    action = args.get("action", "download")

    if action == "download":
        return await _action_download(args)
    elif action == "fetch_save":
        return await _action_fetch_save(args)
    elif action == "save_text":
        return _action_save_text(args)
    elif action == "list":
        return _action_list(args)
    elif action == "delete":
        return _action_delete(args)
    elif action == "read":
        return _action_read(args)
    else:
        return {"error": f"不支持的操作: {action}。支持: download, fetch_save, save_text, list, delete, read"}


def _action_save_text(args: dict[str, Any]) -> dict[str, Any]:
    """将文本内容直接保存为本地文件

    用于 Agent 将搜索到的知识、整理后的内容直接保存为 .md/.txt 文件。
    当网页无法直接抓取正文（JS渲染/反爬）时，Agent 可以通过 web_search 获取信息后，
    用此功能将整理好的内容保存为文件。
    """
    content = args.get("content", "").strip()
    filename = args.get("filename", "").strip()

    if not content:
        return {"error": "内容不能为空"}
    if not filename:
        # 自动生成文件名
        filename = f"note_{int(time.time())}.md"

    # 确保文件名安全
    filename = _sanitize_filename(filename)
    # 如果没有扩展名，默认 .md
    if not os.path.splitext(filename)[1]:
        filename += ".md"

    filepath, final_name = _unique_filepath(filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        file_size = os.path.getsize(filepath)
        return {
            "action": "save_text",
            "filename": final_name,
            "filepath": f"downloads/{final_name}",
            "size": file_size,
            "size_human": _format_file_size(file_size),
            "char_count": len(content),
            "message": f"已保存到 downloads/{final_name}（{len(content)} 字符，{_format_file_size(file_size)}）",
        }
    except Exception as e:
        return {"error": f"保存失败: {e}"}


async def _action_fetch_save(args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "").strip()
    custom_name = args.get("filename", "").strip()
    max_chars = int(args.get("max_chars", 20000))
    timeout = float(args.get("timeout", 30))

    if not url:
        return {"error": "URL 不能为空"}

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"error": f"无效的 URL: {url}"}
    if parsed.scheme not in ("http", "https"):
        return {"error": f"不支持的协议: {parsed.scheme}"}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        # 复用 web_fetch 的 HTML→Markdown 逻辑
        from bs4 import BeautifulSoup, Tag
        import re as _re

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"url": url, "error": f"HTTP {resp.status_code}"}

            html = resp.text
            soup = BeautifulSoup(html, "lxml")

            # 提取标题
            title_tag = soup.find("title")
            page_title = title_tag.get_text(strip=True) if title_tag else "web_page"

            # 移除不需要的标签
            for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                       "aside", "iframe", "noscript", "audio",
                                       "video", "svg", "form", "button"]):
                tag.decompose()

            # ── 针对古诗文网/ctext.org 等网站的特殊提取 ──
            content = None

            # 古诗文网: 诗词正文在 .contson, 译文在 .conYiwen .conview
            if "gushiwen" in url or "shiwens" in url:
                parts = []
                # 诗词标题
                title_el = soup.find("h1") or soup.find(attrs={"class": _re.compile(r"title", _re.I)})
                if title_el:
                    parts.append(f"# {title_el.get_text(strip=True)}\n")
                # 诗词正文
                contson = soup.find("div", class_="contson")
                if contson:
                    parts.append(contson.get_text(separator="\n", strip=True))
                # 译文/赏析
                for yiwen in soup.find_all("div", class_=_re.compile(r"conYiwen|conview|contyishishang", _re.I)):
                    label = yiwen.find_previous(["h2", "h3", "strong"])
                    label_text = label.get_text(strip=True) if label else ""
                    if label_text:
                        parts.append(f"\n## {label_text}\n")
                    parts.append(yiwen.get_text(separator="\n", strip=True))
                if parts:
                    text = "\n\n".join(parts)

            # ctext.org: 正文在 .ctext
            if "ctext.org" in url and not content:
                ctext_content = soup.find("div", class_="ctext") or soup.find("td", class_="ctext")
                if ctext_content:
                    parts = []
                    for p in ctext_content.find_all(["p", "br"]):
                        t = p.get_text(strip=True) if p.name == "p" else ""
                        if t:
                            parts.append(t)
                    if parts:
                        text = "\n".join(parts)

            # 通用提取 (如果上面没匹配到)
            if not content and not locals().get("text"):
                content = (
                    soup.find("article")
                    or soup.find("main")
                    or soup.find(attrs={"class": _re.compile(r"content|article|post|entry|contson", _re.I)})
                    or soup.find(attrs={"id": _re.compile(r"content|article|post|main", _re.I)})
                    or soup.find("body")
                    or soup
                )
                text = content.get_text(separator="\n", strip=True)

            # 清理多余空行和噪音
            text = _re.sub(r"\n{3,}", "\n\n", text).strip()
            # 移除常见导航噪音行
            noise_patterns = [
                r"^您的浏览器不支持.*$", r"^暂无内容$", r"^播放列表.*$",
                r"^[\d.]+x$", r"^列表循环$", r"^随机播放$", r"^单曲循环$", r"^单曲播放$",
                r"^初始的播放列表项$", r"^纠错$", r"^登录$", r"^© \d+",
            ]
            lines = text.split("\n")
            lines = [l for l in lines if not any(_re.match(p, l.strip()) for p in noise_patterns)]
            text = "\n".join(lines)
            text = _re.sub(r"\n{3,}", "\n\n", text).strip()

            if not text:
                return {"url": url, "error": "网页内容为空"}

            # 截断
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars] + "\n\n... (内容已截断)"

            # 生成文件名
            if custom_name:
                base_name = _sanitize_filename(custom_name)
                if not os.path.splitext(base_name)[1]:
                    base_name += ".md"
            else:
                # 用页面标题作为文件名
                base_name = _sanitize_filename(page_title) + ".md"

            filepath, final_name = _unique_filepath(base_name)

            # 写入文件（带元信息头）
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"---\n")
                f.write(f"source: {url}\n")
                f.write(f"title: {page_title}\n")
                f.write(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"---\n\n")
                f.write(text)

            file_size = os.path.getsize(filepath)

            return {
                "action": "fetch_save",
                "url": url,
                "title": page_title,
                "filename": final_name,
                "filepath": f"downloads/{final_name}",
                "size": file_size,
                "size_human": _format_file_size(file_size),
                "char_count": len(text),
                "truncated": truncated,
                "content_preview": text[:500] + ("..." if len(text) > 500 else ""),
                "message": f"已抓取网页内容并保存到 downloads/{final_name}（{len(text)} 字符，{_format_file_size(file_size)}）",
            }

    except httpx.TimeoutException:
        return {"url": url, "error": f"抓取超时（{timeout}秒）"}
    except Exception as e:
        return {"url": url, "error": f"抓取失败: {e}"}


async def _action_download(args: dict[str, Any]) -> dict[str, Any]:
    """下载文件"""
    url = args.get("url", "").strip()
    custom_name = args.get("filename", "").strip()
    timeout = float(args.get("timeout", 120))

    if not url:
        return {"error": "URL 不能为空"}

    # 验证 URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"error": f"无效的 URL: {url}"}
    if parsed.scheme not in ("http", "https"):
        return {"error": f"不支持的协议: {parsed.scheme}，仅支持 http/https"}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 先发 HEAD 请求获取文件信息
            try:
                head_resp = await client.head(url, headers=headers)
                content_type = head_resp.headers.get("content-type", "application/octet-stream")
                content_length = head_resp.headers.get("content-length", "")
            except Exception:
                content_type = "application/octet-stream"
                content_length = ""

            # 检查文件大小
            if content_length:
                try:
                    expected_size = int(content_length)
                    if expected_size > MAX_FILE_SIZE:
                        return {
                            "url": url,
                            "error": f"文件过大: {_format_file_size(expected_size)}，超过限制 {_format_file_size(MAX_FILE_SIZE)}",
                        }
                except ValueError:
                    pass

            # 生成文件名
            filename = _generate_filename(url, content_type, custom_name)
            filepath, final_name = _unique_filepath(filename)

            # 下载文件（流式写入）
            total_written = 0
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    return {"url": url, "error": f"HTTP {resp.status_code}"}

                # 重新获取 content-type（HEAD 可能不一致）
                content_type = resp.headers.get("content-type", content_type)

                with open(filepath, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        total_written += len(chunk)
                        if total_written > MAX_FILE_SIZE:
                            f.close()
                            os.remove(filepath)
                            return {
                                "url": url,
                                "error": f"文件过大，超过限制 {_format_file_size(MAX_FILE_SIZE)}",
                            }
                        f.write(chunk)

            file_size = os.path.getsize(filepath)
            download_time = ""  # 实际下载时间难以精确测量

            return {
                "action": "download",
                "url": url,
                "filename": final_name,
                "filepath": f"downloads/{final_name}",
                "size": file_size,
                "size_human": _format_file_size(file_size),
                "content_type": content_type,
                "download_url": f"/api/files/download/{final_name}",
                "message": f"文件已下载并保存到 downloads/{final_name}（{_format_file_size(file_size)}）",
            }

    except httpx.TimeoutException:
        return {"url": url, "error": f"下载超时（{timeout}秒）"}
    except Exception as e:
        return {"url": url, "error": f"下载失败: {e}"}


def _action_list(args: dict[str, Any]) -> dict[str, Any]:
    """列出已下载的文件"""
    _ensure_downloads_dir()
    try:
        files = []
        for name in sorted(os.listdir(DOWNLOADS_DIR)):
            filepath = os.path.join(DOWNLOADS_DIR, name)
            if not os.path.isfile(filepath):
                continue
            stat = os.stat(filepath)
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "filename": name,
                "size": stat.st_size,
                "size_human": _format_file_size(stat.st_size),
                "extension": ext,
                "created_at": int(stat.st_ctime),
                "modified_at": int(stat.st_mtime),
                "download_url": f"/api/files/download/{name}",
            })

        return {
            "action": "list",
            "count": len(files),
            "total_size": sum(f["size"] for f in files),
            "total_size_human": _format_file_size(sum(f["size"] for f in files)),
            "files": files,
        }
    except Exception as e:
        return {"error": f"列文件失败: {e}"}


def _action_delete(args: dict[str, Any]) -> dict[str, Any]:
    """删除已下载的文件"""
    filename = args.get("filename", "").strip()
    if not filename:
        return {"error": "文件名不能为空"}

    # 安全检查
    filename = _sanitize_filename(filename)
    filepath = os.path.join(DOWNLOADS_DIR, filename)

    if not os.path.isfile(filepath):
        return {"filename": filename, "error": "文件不存在"}

    try:
        os.remove(filepath)
        return {
            "action": "delete",
            "filename": filename,
            "success": True,
            "message": f"已删除文件: {filename}",
        }
    except Exception as e:
        return {"filename": filename, "error": f"删除失败: {e}"}


def _action_read(args: dict[str, Any]) -> dict[str, Any]:
    """读取已下载的文本文件内容"""
    filename = args.get("filename", "").strip()
    max_chars = int(args.get("max_chars", 10000))

    if not filename:
        return {"error": "文件名不能为空"}

    filename = _sanitize_filename(filename)
    filepath = os.path.join(DOWNLOADS_DIR, filename)

    if not os.path.isfile(filepath):
        return {"filename": filename, "error": "文件不存在"}

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _TEXT_EXTENSIONS:
        return {
            "filename": filename,
            "error": f"不支持读取 {ext} 格式文件，仅支持文本文件: {', '.join(sorted(_TEXT_EXTENSIONS))}",
        }

    file_size = os.path.getsize(filepath)
    if file_size > 1024 * 1024:  # 1MB
        return {"filename": filename, "error": "文件过大，超过 1MB 限制"}

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)

        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars] + "\n\n... (内容已截断)"

        return {
            "filename": filename,
            "content": content,
            "truncated": truncated,
            "char_count": len(content),
            "file_size": file_size,
        }
    except Exception as e:
        return {"filename": filename, "error": f"读取失败: {e}"}


# ─── 注册 ────────────────────────────────────────────────

def register():
    tool_registry.register(ChatToolDefinition(
        name="file_download",
        description=(
            "文件下载存储工具：从URL下载文件或保存文本内容到本地downloads目录。"
            "操作: download(下载文件) / fetch_save(抓取网页保存为文本) / save_text(直接保存文本内容为文件) / list(列文件) / delete(删除) / read(读文本)。"
            "当网站内容无法直接抓取(JS渲染)时，先web_search搜索获取信息，再用save_text保存整理后的内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["download", "fetch_save", "save_text", "list", "delete", "read"],
                    "default": "download",
                    "description": "download=下载文件, fetch_save=抓取网页保存为.md, save_text=直接保存文本为文件, list=列文件, delete=删除, read=读文本",
                },
                "url": {
                    "type": "string",
                    "description": "要下载或抓取的URL（action=download/fetch_save时必填）",
                },
                "content": {
                    "type": "string",
                    "description": "要保存的文本内容（action=save_text时必填）",
                },
                "filename": {
                    "type": "string",
                    "description": "自定义文件名（download/fetch_save/save_text时可选）或要操作的文件名（delete/read时必填）",
                },
                "max_chars": {
                    "type": "number",
                    "default": 20000,
                    "description": "fetch_save抓取网页时的最大字符数，或read读取时的最大字符数",
                },
                "timeout": {
                    "type": "number",
                    "default": 60,
                    "description": "下载超时秒数（action=download时可选）",
                },
            },
        },
        execute=execute,
        format_input=lambda args: f"文件操作({args.get('action', 'download')}): {args.get('url', '') or args.get('filename', '')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["下载", "保存", "文件", "存储", "download", "图片", "PDF", "文档", "音频", "视频"],
    ))
