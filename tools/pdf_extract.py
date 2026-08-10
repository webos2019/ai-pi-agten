"""PDF 文本提取工具 — 支持本地文件和远程 URL

使用 pypdf 库提取 PDF 文本内容:
- 本地 PDF 文件路径
- 远程 PDF URL（先下载再提取）
- 指定页码范围
- 自动提取元信息（标题、作者、页数等）
"""

import os
import tempfile
from typing import Any

import httpx
from pypdf import PdfReader

from tool_registry import tool_registry, ChatToolDefinition


def _extract_pdf_metadata(reader: PdfReader) -> dict[str, Any]:
    """提取 PDF 元信息"""
    meta = reader.metadata
    info: dict[str, Any] = {
        "page_count": len(reader.pages),
    }
    if meta:
        info["title"] = str(meta.get("/Title", "") or "")
        info["author"] = str(meta.get("/Author", "") or "")
        info["subject"] = str(meta.get("/Subject", "") or "")
        info["creator"] = str(meta.get("/Creator", "") or "")
        info["producer"] = str(meta.get("/Producer", "") or "")
        info["creation_date"] = str(meta.get("/CreationDate", "") or "")
    return info


def _extract_text(
    reader: PdfReader,
    start_page: int = 1,
    end_page: int = 0,
    max_chars: int = 10000,
) -> tuple[str, bool]:
    """提取指定页码范围的文本

    返回: (text, truncated)
    """
    total_pages = len(reader.pages)

    # 页码从 1 开始，转为 0-based
    start = max(0, start_page - 1)
    end = end_page if end_page and end_page > 0 else total_pages
    end = min(end, total_pages)

    texts: list[str] = []
    total_len = 0
    truncated = False

    for i in range(start, end):
        try:
            page_text = reader.pages[i].extract_text() or ""
            if total_len + len(page_text) > max_chars:
                remaining = max_chars - total_len
                if remaining > 0:
                    texts.append(page_text[:remaining])
                truncated = True
                break
            texts.append(page_text)
            total_len += len(page_text)
        except Exception:
            texts.append(f"[第 {i+1} 页文本提取失败]\n")

    result = "\n\n--- 页面分隔 ---\n\n".join(texts)
    return result, truncated


async def _download_pdf(url: str) -> tuple[str, bool]:
    """下载远程 PDF 到临时文件

    返回: (filepath, should_cleanup)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"下载 PDF 失败: HTTP {resp.status_code}")

        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and not url.lower().endswith(".pdf"):
            raise RuntimeError(f"URL 返回的不是 PDF (content-type: {content_type})")

        # 写入临时文件
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(resp.content)

        return tmp_path, True


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    source = args.get("source", "").strip()
    start_page = int(args.get("start_page", 1))
    end_page = int(args.get("end_page", 0))
    max_chars = int(args.get("max_chars", 10000))

    if not source:
        return {"error": "source 参数不能为空（本地文件路径或 PDF URL）"}

    is_remote = source.startswith(("http://", "https://"))
    tmp_path = None
    should_cleanup = False

    try:
        if is_remote:
            # 下载远程 PDF
            tmp_path, should_cleanup = await _download_pdf(source)
            filepath = tmp_path
        else:
            # 本地文件
            filepath = os.path.abspath(source)
            if not os.path.isfile(filepath):
                return {"error": f"文件不存在: {filepath}"}

        # 读取 PDF
        reader = PdfReader(filepath)

        # 提取元信息
        metadata = _extract_pdf_metadata(reader)

        # 提取文本
        text, truncated = _extract_text(
            reader, start_page, end_page, max_chars,
        )

        return {
            "source": source,
            "type": "url" if is_remote else "file",
            **metadata,
            "extracted_pages": f"{start_page}-{end_page or metadata['page_count']}",
            "content": text,
            "truncated": truncated,
            "char_count": len(text),
        }

    except RuntimeError as e:
        return {"source": source, "error": str(e)}
    except Exception as e:
        return {"source": source, "error": f"PDF 提取失败: {e}"}
    finally:
        # 清理临时文件
        if should_cleanup and tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def register():
    tool_registry.register(ChatToolDefinition(
        name="pdf_extract",
        description="提取PDF文档文本内容，支持本地路径和URL",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "start_page": {"type": "number", "default": 1},
                "end_page": {"type": "number", "default": 0},
                "max_chars": {"type": "number", "default": 10000},
            },
            "required": ["source"],
        },
        execute=execute,
        format_input=lambda args: f"PDF: {args.get('source', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.8,
        keywords=["pdf", "文档", "论文", "paper", "提取", "extract"],
    ))
