"""网络搜索工具 — 多搜索引擎支持（Tavily / Serper / Bing / DuckDuckGo / Google CSE）

引擎优先级（auto 模式自动选择）:
1. Tavily   — 专为 AI 设计，返回正文摘要 + AI 答案，效果最好（需 API Key）
2. Serper   — Google 搜索 API 代理，结果丰富（需 API Key）
3. Bing     — 免费无需 Key，国内可直连（默认）
4. DuckDuckGo — 免费无需 Key，国内可能不可达（降级备选）
5. Google CSE — 需 API Key + CSE ID

支持:
- 多语言搜索
- 结果摘要 + URL
- 可配置结果数量
- 自动降级: Tavily → Serper → Bing → DuckDuckGo
- Tavily 模式额外返回 AI 生成答案 (answer 字段)
- 深度搜索 (deep_search): 自动生成查询变体 + 并行搜索 + 结果去重 + 自动抓取正文
  借鉴 Hermes Agent 的设计理念: 搜索智能内置在工具中，不依赖外层 LLM 决定是否重试
"""

import os
import re
import json
import asyncio
from typing import Any
from urllib.parse import quote_plus, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup, Tag

from tool_registry import tool_registry, ChatToolDefinition

# 默认 User-Agent
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 请求超时
_SEARCH_TIMEOUT = 15


# ─── Tavily ────────────────────────────────────────────

async def _tavily_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """通过 Tavily API 搜索（专为 AI 设计，返回正文摘要 + AI 答案）

    Tavily 优势:
    - 返回的 content 是网页正文摘要（非简短 snippet），信息量大
    - include_answer=true 时返回 AI 生成的直接答案
    - 无需二次 web_fetch 抓取

    需配置环境变量: TAVILY_API_KEY
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY 未配置")

    url = "https://api.tavily.com/search"
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",       # advanced 返回更详细的内容
        "include_answer": True,           # 返回 AI 生成的答案
        "include_raw_content": False,
        "include_images": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    results: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Tavily API 返回 {resp.status_code}: {resp.text[:200]}")

        data = resp.json()

        # Tavily 的 AI 生成答案（作为第一条结果插入，方便 LLM 直接使用）
        answer = data.get("answer", "")
        if answer:
            results.append({
                "title": "[AI 摘要]",
                "url": "",
                "snippet": answer,
                "content": answer,
            })

        for item in data.get("results", []):
            title = item.get("title", "")
            link = item.get("url", "")
            # Tavily 的 content 字段是网页正文摘要，比普通 snippet 丰富得多
            content = item.get("content", "")
            snippet = content if content else item.get("snippet", "")

            if title and link:
                results.append({
                    "title": title,
                    "url": link,
                    "snippet": snippet[:500] if snippet else "",
                    "content": content,
                })

            if len(results) >= max_results:
                break

    return results


# ─── Serper (Google Search API) ────────────────────────

async def _serper_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """通过 Serper API 搜索（Google 搜索 API 代理）

    Serper 优势:
    - 返回 Google 搜索结果，质量高
    - 响应速度快
    - 结果包含 rich snippet

    需配置环境变量: SERPER_API_KEY
    """
    api_key = os.getenv("SERPER_API_KEY", "")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY 未配置")

    url = "https://google.serper.dev/search"
    payload = {
        "q": query,
        "num": max_results,
        "gl": "cn",        # 地理位置为中国
        "hl": "zh-cn",     # 语言为中文
    }
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    results: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Serper API 返回 {resp.status_code}: {resp.text[:200]}")

        data = resp.json()

        # Knowledge Graph（如果有，作为第一条插入）
        kg = data.get("knowledgeGraph", {})
        if kg:
            kg_title = kg.get("title", "")
            kg_desc = kg.get("description", "")
            if kg_title and kg_desc:
                results.append({
                    "title": f"[知识图谱] {kg_title}",
                    "url": kg.get("descriptionLink", ""),
                    "snippet": kg_desc,
                    "content": kg.get("description", ""),
                })

        for item in data.get("organic", []):
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")

            # Serper 有时返回 richSnippet
            if not snippet and item.get("richSnippet"):
                snippet = json.dumps(item["richSnippet"], ensure_ascii=False)

            if title and link:
                results.append({
                    "title": title,
                    "url": link,
                    "snippet": snippet,
                    "content": snippet,
                })

            if len(results) >= max_results:
                break

    return results


# ─── Bing ──────────────────────────────────────────────

async def _bing_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """通过 Bing 搜索（无需 API Key，国内可直连）"""
    results: list[dict[str, str]] = []

    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results * 2}"
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "lxml")

        # Bing 搜索结果选择器: <li class="b_algo">
        for item in soup.select("li.b_algo"):
            title_tag = item.select_one("h2 a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")

            # 摘要: 优先 .b_caption p，其次 .b_focusTextSmall，最后任意 p
            snippet = ""
            for selector in [".b_caption p", ".b_focusTextSmall", "p"]:
                snippet_tag = item.select_one(selector)
                if snippet_tag:
                    text = snippet_tag.get_text(strip=True)
                    # 过滤掉 Bing 的预览提示文本
                    if text and "无法提供" not in text and "预览" not in text:
                        snippet = text
                        break
                    elif not snippet and text:
                        snippet = text

            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet, "content": snippet})

            if len(results) >= max_results:
                break

    return results


# ─── DuckDuckGo ────────────────────────────────────────

async def _duckduckgo_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """通过 DuckDuckGo HTML 页面搜索（无需 API Key）"""
    results: list[dict[str, str]] = []

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": _DEFAULT_UA,
    }

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "lxml")

        for item in soup.select("div.result"):
            title_tag = item.select_one("a.result__a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if "uddg=" in href:
                parsed = urlparse(href)
                params = parse_qs(parsed.query)
                href = params.get("uddg", [href])[0]
            elif href.startswith("//"):
                href = "https:" + href

            snippet_tag = item.select_one("a.result__snippet")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet, "content": snippet})

            if len(results) >= max_results:
                break

    return results


# ─── Google CSE ────────────────────────────────────────

async def _google_cse_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """通过 Google Custom Search API 搜索（需配置 API Key + CSE ID）"""
    api_key = os.getenv("GOOGLE_CSE_API_KEY", "")
    cse_id = os.getenv("GOOGLE_CSE_ID", "")

    if not api_key or not cse_id:
        return await _bing_search(query, max_results)

    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": cse_id, "q": query, "num": min(max_results, 10)}

    results: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return await _bing_search(query, max_results)

        data = resp.json()
        for item in data.get("items", []):
            snippet = item.get("snippet", "")
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": snippet,
                "content": snippet,
            })
            if len(results) >= max_results:
                break

    return results


# ─── 自动引擎选择 ──────────────────────────────────────

def _get_auto_engine() -> str:
    """根据已配置的 API Key 自动选择最佳搜索引擎

    优先级: Tavily > Serper > Bing
    """
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("SERPER_API_KEY"):
        return "serper"
    return "bing"


# 降级链: 每个引擎失败后尝试的下一个引擎
_FALLBACK_CHAIN: dict[str, str] = {
    "tavily": "serper",
    "serper": "bing",
    "bing": "duckduckgo",
    "duckduckgo": "",
    "google": "bing",
    "auto": "",  # auto 单独处理
}

# 引擎 → 搜索函数映射
_ENGINE_FUNCS = {
    "tavily": _tavily_search,
    "serper": _serper_search,
    "bing": _bing_search,
    "duckduckgo": _duckduckgo_search,
    "google": _google_cse_search,
}


async def _search_with_fallback(
    query: str,
    max_results: int,
    engine: str,
) -> tuple[list[dict[str, str]], str]:
    """执行搜索，失败时按降级链自动尝试下一个引擎

    返回: (结果列表, 实际使用的引擎名)
    """
    if engine == "auto":
        engine = _get_auto_engine()

    tried: set[str] = set()
    current = engine

    while current and current not in tried:
        tried.add(current)
        func = _ENGINE_FUNCS.get(current)
        if not func:
            current = _FALLBACK_CHAIN.get(current, "")
            continue

        try:
            results = await func(query, max_results)
            if results:
                return results, current
        except Exception as e:
            print(f"[web_search] 引擎 {current} 失败: {e}")

        # 降级到下一个引擎
        current = _FALLBACK_CHAIN.get(current, "")

    return [], ""


# ─── 深度搜索 (Hermes 式智能搜索) ──────────────────────

# 中文问句中的疑问/修饰词模式，搜索时应去除
_NOISE_PATTERNS = [
    r"什么是", r"是什么", r"介绍一下", r"介绍下", r"告诉我",
    r"详解", r"解释一下", r"解释下", r"怎么样", r"是怎样的",
    r"是谁", r"是谁的", r"讲了什么", r"说了什么",
    r"帮我查", r"帮我搜", r"帮我找", r"查查", r"查一下",
    r"搜一下", r"搜搜", r"找一下", r"找找",
    r"请问", r"请问一下", r"问一下",
    r"的作者", r"的内容", r"的简介", r"的历史",
    r"的原理", r"的概念", r"的意思",
    r"为什么", r"怎么办", r"如何",
]

# 常见中英文术语映射（用于生成英文查询变体）
_CN_EN_MAP = {
    "人工智能": "artificial intelligence AI",
    "机器学习": "machine learning",
    "深度学习": "deep learning",
    "区块链": "blockchain",
    "量子计算": "quantum computing",
    "新能源": "renewable energy",
    "芯片": "semiconductor chip",
    "股票": "stock",
    "基金": "fund investment",
    "算法": "algorithm",
    "数据结构": "data structure",
    "操作系统": "operating system",
    "数据库": "database",
    "云计算": "cloud computing",
    "物联网": "IoT internet of things",
    "5G": "5G wireless",
    "元宇宙": "metaverse",
    "大模型": "large language model LLM",
    "提示工程": "prompt engineering",
    "微服务": "microservices",
}


def _is_literature_query(query: str) -> bool:
    """检测是否为古籍/文学类查询"""
    return any(kw in query for kw in _LITERATURE_KEYWORDS)


def _generate_query_variants(query: str) -> list[str]:
    """从用户原始查询生成多个搜索查询变体

    策略（借鉴 Hermes Agent 的 query expansion）:
    1. 原始查询（清理后）
    2. 去除疑问词的精简版
    3. 提取核心实体 + 上下文词（简介/百科/详解）
    4. 英文翻译版（如果检测到可翻译术语）
    5. 古籍/文学查询 → 追加 site:ctext.org / site:gushiwen.cn 站点搜索

    返回去重后的变体列表（最多 4 个）
    """
    variants: list[str] = []
    original = query.strip()

    # 1. 原始查询
    variants.append(original)

    # 2. 去除疑问/修饰词
    cleaned = original
    for pattern in _NOISE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)
    cleaned = re.sub(r"[？?！!。，,]", "", cleaned).strip()
    if cleaned and cleaned != original:
        variants.append(cleaned)

    # 3. 核心实体 + 上下文词
    # 提取引号内的内容作为核心实体
    quoted = re.findall(r"[《「\"\"'''](.+?)[》」\"\"''']", original)
    if quoted:
        for q in quoted[:2]:
            variants.append(f"{q} 简介 百科")
            variants.append(f"{q} 是什么")
    elif cleaned:
        # 没有引号，用清理后的查询 + 上下文词
        variants.append(f"{cleaned} 简介 百科")
        variants.append(f"{cleaned} 详解")

    # 4. 英文翻译
    for cn_term, en_term in _CN_EN_MAP.items():
        if cn_term in original:
            variants.append(original.replace(cn_term, en_term))
            break

    # 去重 + 限制数量
    seen: set[str] = set()
    unique: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            unique.append(v)
        if len(unique) >= 4:
            break

    return unique


async def _auto_fetch_content(
    url: str,
    max_chars: int = 3000,
) -> str:
    """自动抓取网页正文内容（用于增强搜索结果）

    与 web_fetch 工具类似的逻辑，但简化版，仅用于搜索结果增强。
    """
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return ""

            soup = BeautifulSoup(resp.text, "lxml")

            # 移除噪音标签
            for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
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

            # 提取纯文本
            paragraphs: list[str] = []
            for p in content.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 20:  # 过滤短文本
                    paragraphs.append(text)

            if not paragraphs:
                # 回退：直接获取所有文本
                text = content.get_text(separator="\n", strip=True)
                paragraphs = [text]

            result = "\n".join(paragraphs[:10])  # 最多取前 10 段

            if len(result) > max_chars:
                result = result[:max_chars] + "..."

            return result

    except Exception:
        return ""


def _deduplicate_results(
    results: list[dict[str, str]],
    max_count: int = 8,
) -> list[dict[str, str]]:
    """对多轮搜索结果去重 + 简单排序

    去重策略: URL 去重 + 标题相似度去重
    排序策略: 有 content 的结果优先（信息量更大）
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[dict[str, str]] = []

    for r in results:
        url = r.get("url", "").rstrip("/")
        title = r.get("title", "").strip()

        # URL 去重
        if url and url in seen_urls:
            continue
        # 标题去重（完全相同）
        if title and title in seen_titles:
            continue

        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        unique.append(r)

        if len(unique) >= max_count:
            break

    # 排序：有 content 且 content 较长的优先
    unique.sort(
        key=lambda r: -len(r.get("content", "") or r.get("snippet", "")),
    )

    return unique[:max_count]


async def _deep_search(
    query: str,
    max_results: int,
    engine: str,
    auto_fetch: bool = True,
) -> dict[str, Any]:
    """深度搜索 — Hermes 式智能搜索

    流程:
    1. 生成查询变体 (query expansion)
    2. 并行搜索所有变体 (asyncio.gather)
    3. 合并 + 去重结果
    4. 自动抓取 Top 2 结果的正文内容
    5. 返回增强后的结果

    一次工具调用 = 多轮智能搜索，不再依赖外层 LLM 决定是否重试。
    """
    # 1. 生成查询变体
    variants = _generate_query_variants(query)

    # 2. 并行搜索所有变体
    async def _search_one(q: str) -> list[dict[str, str]]:
        results, _ = await _search_with_fallback(q, max_results, engine)
        return results

    search_tasks = [_search_one(v) for v in variants]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # 3. 合并所有结果
    all_results: list[dict[str, str]] = []
    for i, result in enumerate(search_results):
        if isinstance(result, Exception):
            print(f"[deep_search] 变体 '{variants[i]}' 搜索失败: {result}")
            continue
        # 给每个结果标注来源查询
        for r in result:
            r["source_query"] = variants[i]
        all_results.extend(result)

    # 4. 去重
    deduped = _deduplicate_results(all_results, max_count=max_results + 3)

    if not deduped:
        return {
            "query": query,
            "variants": variants,
            "results": [],
            "message": "所有搜索变体均未找到相关结果",
        }

    # 5. 自动抓取 Top 2 结果的正文内容
    if auto_fetch and len(deduped) > 0:
        top_urls = [
            r["url"] for r in deduped[:2]
            if r.get("url") and not r.get("content")
        ]
        if top_urls:
            fetch_tasks = [_auto_fetch_content(url) for url in top_urls]
            fetched_contents = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            for i, content in enumerate(fetched_contents):
                if isinstance(content, str) and content:
                    url = top_urls[i]
                    for r in deduped:
                        if r.get("url") == url:
                            r["content"] = content
                            # 更新 snippet 如果原来为空
                            if not r.get("snippet"):
                                r["snippet"] = content[:300]
                            break

    return {
        "query": query,
        "variants_tried": variants,
        "engine": engine,
        "result_count": len(deduped),
        "results": deduped,
        "deep_search": True,
    }


# ─── 主执行函数 ────────────────────────────────────────

async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "").strip()
    max_results = int(args.get("max_results", 5))
    search_engine = args.get("search_engine", "auto")
    deep_search = args.get("deep_search", True)  # 默认开启深度搜索

    if not query:
        return {"error": "搜索关键词不能为空"}

    try:
        # 深度搜索模式：自动查询变体 + 并行搜索 + 去重 + 自动抓取
        if deep_search:
            return await _deep_search(query, max_results, search_engine)

        # 普通搜索模式
        results, used_engine = await _search_with_fallback(
            query, max_results, search_engine,
        )

        if not results:
            return {"query": query, "results": [], "message": "未找到相关结果"}

        return {
            "query": query,
            "engine": used_engine or search_engine,
            "requested_engine": search_engine,
            "result_count": len(results),
            "results": results,
        }
    except Exception as e:
        return {"query": query, "error": f"搜索失败: {e}"}


def register():
    # 动态生成引擎列表和默认值
    has_tavily = bool(os.getenv("TAVILY_API_KEY"))
    has_serper = bool(os.getenv("SERPER_API_KEY"))

    default_engine = "auto"
    desc_parts = ["自动选择最佳引擎"]
    if has_tavily:
        desc_parts.append("Tavily 已配置（AI 优化搜索，返回正文摘要）")
    if has_serper:
        desc_parts.append("Serper 已配置（Google 搜索代理）")
    desc_parts.append("Bing（免费，国内可用）")

    tool_registry.register(ChatToolDefinition(
        name="web_search",
        description=(
            "互联网搜索，返回标题/URL/摘要/正文。"
            f"支持: {', '.join(desc_parts)}。"
            "默认开启深度搜索模式：自动生成查询变体 + 并行多轮搜索 + 结果去重 + 自动抓取正文。"
            "当用户询问任何你不确定的知识时，务必使用此工具搜索。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "number", "default": 5},
                "search_engine": {
                    "type": "string",
                    "enum": ["auto", "tavily", "serper", "bing", "duckduckgo", "google"],
                    "default": "auto",
                    "description": "auto=自动选择最佳引擎, tavily=AI优化搜索, serper=Google代理, bing=必应, duckduckgo=DuckDuckGo, google=Google CSE",
                },
                "deep_search": {
                    "type": "boolean",
                    "default": True,
                    "description": "深度搜索模式：自动生成查询变体(去除疑问词/提取核心实体/英文翻译) + 并行搜索 + 结果去重 + 自动抓取Top2正文。默认开启，一次调用等于多轮智能搜索。",
                },
            },
            "required": ["query"],
        },
        execute=execute,
        format_input=lambda args: f"搜索: {args.get('query', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.85,
        keywords=["搜索", "search", "查找", "谷歌", "百度", "必应", "互联网", "网络搜索", "tavily", "serper"],
    ))
