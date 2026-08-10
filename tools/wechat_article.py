"""微信公众号文章读取工具 — 专门抓取 mp.weixin.qq.com 文章

微信公众号文章有特殊的反爬机制:
- 需要特定 User-Agent 头
- 正文在 id="js_content" 的 div 中
- 元数据在 <meta property="og:..."> 标签中
- 普通抓取会返回"环境异常"验证页面

本工具针对微信文章做了专门优化:
- 模拟微信内置浏览器 User-Agent
- 提取 og:title / og:description / og:article:author 等元数据
- 精确提取 js_content 区域正文
- 转换为结构化 Markdown
- 清理 &nbsp;/\xa0 等特殊字符
"""

import re
import json
from typing import Any

import httpx
from bs4 import BeautifulSoup

from tool_registry import tool_registry, ChatToolDefinition


# 微信内置浏览器 User-Agent（比普通浏览器更容易通过验证）
WECHAT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36 MicroMessenger/8.0.1"
)


def _clean_text(text: str) -> str:
    """清理文本中的特殊字符

    微信文章 HTML 中的 &nbsp; 被 BeautifulSoup 解析为 \\xa0 (U+00A0)，
    在前端 marked 渲染时会显示为 &nbsp; 符号，需要替换为普通空格。
    """
    if not text:
        return text
    # \xa0 (non-breaking space) → 普通空格
    text = text.replace("\xa0", " ")
    # 其他不可见字符
    text = text.replace("\u200b", "")   # zero-width space
    text = text.replace("\ufeff", "")   # BOM
    text = text.replace("\u2002", " ")  # en space
    text = text.replace("\u2003", " ")  # em space
    text = text.replace("\u2009", " ")  # thin space
    # 连续空格压缩为单个
    text = re.sub(r" {3,}", "  ", text)
    return text.strip()


def _extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """提取微信文章元数据 (og: 标签)"""
    meta: dict[str, str] = {}

    og_mappings = {
        "og:title": "title",
        "og:description": "description",
        "og:url": "url",
        "og:image": "cover_image",
        "og:article:author": "author",
        "og:article:published_time": "publish_time",
    }

    for og_prop, key in og_mappings.items():
        tag = soup.find("meta", attrs={"property": og_prop})
        if tag:
            meta[key] = _clean_text(tag.get("content", ""))

    # 公众号名称
    nickname_tag = soup.find(attrs={"class": re.compile(r"rich_media_meta_nickname", re.I)})
    if nickname_tag:
        meta["account_name"] = _clean_text(nickname_tag.get_text())

    name_tag = soup.find(id="js_name")
    if name_tag and "account_name" not in meta:
        meta["account_name"] = _clean_text(name_tag.get_text())

    # 发布时间
    time_tag = soup.find(id="js_publish_time") or soup.find("em", id="publish_time")
    if time_tag and "publish_time" not in meta:
        meta["publish_time"] = _clean_text(time_tag.get_text())

    return meta


# 块级标签集合
_BLOCK_TAGS = frozenset([
    "p", "section", "div", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "pre", "blockquote", "table", "tr",
])


def _has_block_children(el) -> bool:
    """检查元素是否包含块级子元素"""
    for child in el.children:
        if hasattr(child, "name") and child.name and child.name.lower() in _BLOCK_TAGS:
            return True
    return False


def _extract_content_markdown(soup: BeautifulSoup, max_chars: int = 10000) -> str:
    """提取微信文章正文并转为 Markdown

    微信文章正文在 id="js_content" 的 div 中。
    针对微信文章特点优化:
    - 清理 &nbsp;/\\xa0 为普通空格
    - section 标签递归提取，保留段落结构
    - <br> 标签转为换行
    """
    # 移除不需要的标签
    for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                               "aside", "iframe", "noscript"]):
        tag.decompose()

    # 优先提取 js_content 区域
    content = soup.find(id="js_content")
    if not content:
        content = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"class": re.compile(r"rich_media_content", re.I)})
            or soup.find("body")
            or soup
        )

    lines: list[str] = []

    def process_element(el, depth=0):
        for child in el.children:
            # 文本节点
            if isinstance(child, str):
                text = _clean_text(child)
                if text:
                    lines.append(text)
                continue

            if not hasattr(child, "name") or child.name is None:
                continue

            tag_name = child.name.lower()

            if tag_name == "br":
                lines.append("")
                continue

            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag_name[1])
                text = _clean_text(child.get_text())
                if text:
                    lines.append(f"\n{'#' * level} {text}\n")

            elif tag_name == "p":
                # <p> 内可能有 <br>，递归处理保留换行
                if _has_block_children(child):
                    process_element(child, depth + 1)
                else:
                    text = _clean_text(child.get_text())
                    if text:
                        lines.append(f"\n{text}\n")

            elif tag_name in ("ul", "ol"):
                idx = 1
                for li in child.find_all("li", recursive=False):
                    text = _clean_text(li.get_text())
                    if text:
                        prefix = "-" if tag_name == "ul" else f"{idx}."
                        lines.append(f"{prefix} {text}")
                        idx += 1
                lines.append("")

            elif tag_name == "pre":
                code = _clean_text(child.get_text())
                lines.append(f"\n```\n{code}\n```\n")

            elif tag_name == "blockquote":
                text = _clean_text(child.get_text())
                if text:
                    lines.append(f"\n> {text}\n")

            elif tag_name == "img":
                alt = _clean_text(child.get("alt", ""))
                src = child.get("data-src") or child.get("src", "")
                if src:
                    lines.append(f"\n![{alt}]({src})\n")

            elif tag_name == "a":
                text = _clean_text(child.get_text())
                href = child.get("href", "")
                if text and href:
                    lines.append(f"[{text}]({href})")

            elif tag_name == "section":
                # 微信文章大量使用 section 做排版布局
                # 有块级子元素 → 递归处理保留结构
                if _has_block_children(child):
                    process_element(child, depth + 1)
                else:
                    # 叶子 section：提取文本作为一个段落
                    text = _clean_text(child.get_text())
                    if text:
                        lines.append(f"\n{text}\n")

            elif tag_name in ("div", "span", "strong", "em", "b", "i"):
                if _has_block_children(child):
                    process_element(child, depth + 1)
                else:
                    text = _clean_text(child.get_text())
                    if text:
                        lines.append(text)

            else:
                text = _clean_text(child.get_text())
                if text:
                    lines.append(text)

    process_element(content)

    result = "\n".join(lines)
    # 清理开头的 --- (微信文章的分割线，不是 Markdown hr)
    result = re.sub(r"^#{0,3}\s*-{3,}\s*\n", "", result)
    # 清理连续空行 (最多保留两个换行)
    result = re.sub(r"\n{3,}", "\n\n", result)
    # 清理行首尾多余空格
    result = "\n".join(line.rstrip() for line in result.split("\n"))
    result = result.strip()

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n... (文章内容已截断)"

    return result


def _is_verification_page(html: str) -> bool:
    """检测是否被微信验证拦截"""
    verification_markers = [
        "环境异常",
        "完成验证后即可继续访问",
        "js_verify",
    ]
    return any(marker in html for marker in verification_markers)


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "").strip()
    max_chars = int(args.get("max_chars", 10000))
    extract_metadata_only = args.get("metadata_only", False)

    if not url:
        return {"error": "url 参数不能为空"}

    # 验证 URL 是否为微信公众号文章
    if "mp.weixin.qq.com" not in url:
        return {"error": "此工具仅支持微信公众号文章 (mp.weixin.qq.com)"}

    headers = {
        "User-Agent": WECHAT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                return {"url": url, "error": f"HTTP {resp.status_code}"}

            html = resp.text

            # 检测验证拦截
            if _is_verification_page(html):
                return {
                    "url": url,
                    "error": "被微信验证拦截（环境异常）",
                    "hint": "微信文章触发了反爬验证。请稍后重试，或在浏览器中打开链接完成验证后重试。",
                }

            soup = BeautifulSoup(html, "lxml")

            # 提取元数据
            metadata = _extract_metadata(soup)

            # 检测是否为验证页面（二次检查）
            if not metadata.get("title") and "完成验证" in html:
                return {
                    "url": url,
                    "error": "被微信验证拦截",
                    "hint": "请稍后重试或在浏览器中打开链接。",
                }

            if extract_metadata_only:
                return {
                    "url": url,
                    **metadata,
                }

            # 提取正文
            content = _extract_content_markdown(soup, max_chars)

            return {
                "url": url,
                **metadata,
                "content": content,
                "truncated": len(content) >= max_chars,
                "char_count": len(content),
            }

    except httpx.TimeoutException:
        return {"url": url, "error": "请求超时（20秒）"}
    except Exception as e:
        return {"url": url, "error": f"微信文章读取失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="wechat_article",
        description="提取微信公众号文章标题、作者和正文(Markdown)",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "number", "default": 10000},
                "metadata_only": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
        execute=execute,
        format_input=lambda args: f"微信公众号文章: {args.get('url', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.9,
        keywords=["微信公众号", "公众号", "wechat", "mp.weixin", "文章", "微信文章"],
    ))
