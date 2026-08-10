"""网页抓取工具 — 增强版 HTML→Markdown 提取

相比旧版 web_browse:
- 使用 BeautifulSoup4 精确解析 HTML
- 智能提取正文内容（article/main/content 区域优先）
- 转换为结构化 Markdown（保留标题、列表、链接、代码块）
- 移除导航栏、广告、脚本等噪音
- 支持自定义 User-Agent
- 支持字符数限制
"""

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from tool_registry import tool_registry, ChatToolDefinition


def _html_to_markdown(soup: BeautifulSoup, max_chars: int = 5000) -> str:
    """将 BeautifulSoup 解析的 HTML 转为简洁 Markdown"""

    # 移除不需要的标签
    for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                               "aside", "iframe", "noscript"]):
        tag.decompose()

    # 优先提取正文区域
    content = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"class": re.compile(r"content|article|post|entry", re.I)})
        or soup.find(attrs={"id": re.compile(r"content|article|post|main", re.I)})
        or soup.find("body")
        or soup
    )

    lines: list[str] = []

    def process_element(el: Tag, depth: int = 0) -> None:
        for child in el.children:
            if isinstance(child, str):
                text = child.strip()
                if text:
                    lines.append(text)
                continue

            if not isinstance(child, Tag):
                continue

            tag_name = child.name.lower()

            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag_name[1])
                text = child.get_text(strip=True)
                if text:
                    lines.append(f"\n{'#' * level} {text}\n")

            elif tag_name == "p":
                text = child.get_text(strip=True)
                if text:
                    lines.append(f"\n{text}\n")

            elif tag_name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        prefix = "-" if tag_name == "ul" else f"{len(lines)}."
                        lines.append(f"{prefix} {text}")
                lines.append("")

            elif tag_name == "pre":
                code = child.get_text()
                lang = ""
                code_tag = child.find("code")
                if code_tag and code_tag.get("class"):
                    classes = code_tag.get("class", [])
                    for cls in classes:
                        if cls.startswith(("language-", "lang-")):
                            lang = cls.split("-", 1)[1]
                            break
                lines.append(f"\n```{lang}\n{code.strip()}\n```\n")

            elif tag_name == "blockquote":
                text = child.get_text(strip=True)
                if text:
                    lines.append(f"\n> {text}\n")

            elif tag_name == "table":
                rows = child.find_all("tr")
                if rows:
                    # 表头
                    header_cells = rows[0].find_all(["th", "td"])
                    if header_cells:
                        header = "| " + " | ".join(c.get_text(strip=True) for c in header_cells) + " |"
                        separator = "| " + " | ".join("---" for _ in header_cells) + " |"
                        lines.append(f"\n{header}\n{separator}")
                        for row in rows[1:]:
                            cells = row.find_all(["th", "td"])
                            if cells:
                                lines.append("| " + " | ".join(c.get_text(strip=True) for c in cells) + " |")
                    lines.append("")

            elif tag_name == "a":
                text = child.get_text(strip=True)
                href = child.get("href", "")
                if text and href:
                    lines.append(f"[{text}]({href})")

            elif tag_name in ("div", "section", "span", "br"):
                process_element(child, depth + 1)

            else:
                text = child.get_text(strip=True)
                if text:
                    lines.append(text)

    process_element(content)

    result = "\n".join(lines)
    # 清理多余空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n... (内容已截断)"

    return result


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "").strip()
    max_chars = int(args.get("max_chars", 5000))
    selector = args.get("selector", "")

    if not url:
        return {"error": "URL 不能为空"}

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
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                return {"url": url, "error": f"HTTP {resp.status_code}"}

            content_type = resp.headers.get("content-type", "")
            html = resp.text

            soup = BeautifulSoup(html, "lxml")

            # 提取标题
            title_tag = soup.find("title")
            page_title = title_tag.get_text(strip=True) if title_tag else ""

            # 如果指定了 CSS 选择器，只提取匹配的部分
            if selector:
                selected = soup.select(selector)
                if selected:
                    # 创建一个新的 BeautifulSoup 只包含选中元素
                    new_soup = BeautifulSoup("<div></div>", "lxml")
                    container = new_soup.find("div")
                    for el in selected:
                        container.append(el)
                    soup = new_soup

            # 转为 Markdown
            markdown = _html_to_markdown(soup, max_chars)

            # 提取页面描述
            meta_desc = soup.find("meta", attrs={"name": "description"})
            description = meta_desc.get("content", "") if meta_desc else ""

            return {
                "url": url,
                "title": page_title,
                "description": description,
                "content": markdown,
                "content_type": content_type,
                "truncated": len(markdown) >= max_chars,
                "char_count": len(markdown),
            }

    except httpx.TimeoutException:
        return {"url": url, "error": "请求超时（20秒）"}
    except Exception as e:
        return {"url": url, "error": f"网页抓取失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="web_fetch",
        description="抓取网页并转为Markdown，支持CSS选择器",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "number", "default": 5000},
                "selector": {"type": "string"},
            },
            "required": ["url"],
        },
        execute=execute,
        format_input=lambda args: f"抓取网页: {args.get('url', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.8,
        keywords=["抓取", "网页", "fetch", "爬虫", "HTML", "内容提取"],
    ))
