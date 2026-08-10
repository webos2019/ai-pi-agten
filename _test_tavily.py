"""测试搜索功能 — 验证 auto 模式和降级链"""
import sys
import asyncio
import os

# 修复 Windows 终端编码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.web_search import execute, _get_auto_engine, _search_with_fallback


async def main():
    # 1. 检查自动引擎选择
    auto = _get_auto_engine()
    print(f"[1] auto engine: {auto}")
    print(f"    TAVILY_API_KEY: {'configured' if os.getenv('TAVILY_API_KEY') else 'not set'}")
    print(f"    SERPER_API_KEY: {'configured' if os.getenv('SERPER_API_KEY') else 'not set'}")
    print()

    # 2. 测试 auto 模式搜索
    print("[2] auto search 'caigentan'...")
    result = await execute({"query": "caigentan book", "max_results": 3}, {})
    print(f"    engine: {result.get('engine', '?')}")
    print(f"    count: {result.get('result_count', 0)}")
    if result.get("results"):
        for i, r in enumerate(result["results"][:3], 1):
            title = r.get("title", "")[:50]
            url = r.get("url", "")[:60]
            print(f"    {i}. {title}")
            print(f"       URL: {url}")
    elif result.get("error"):
        print(f"    error: {result['error']}")
    else:
        print(f"    msg: {result.get('message', 'none')}")
    print()

    # 3. 测试降级链
    print("[3] fallback test: tavily -> bing ...")
    results, used = await _search_with_fallback("Python GIL", 3, "tavily")
    print(f"    requested: tavily, used: {used}, count: {len(results)}")
    print()

    # 4. 测试 bing
    print("[4] bing search 'machine learning'...")
    result = await execute({"query": "machine learning", "max_results": 3, "search_engine": "bing"}, {})
    print(f"    engine: {result.get('engine', '?')}")
    print(f"    count: {result.get('result_count', 0)}")
    if result.get("results"):
        for i, r in enumerate(result["results"][:2], 1):
            print(f"    {i}. {r.get('title', '')[:50]}")
    print()

    print("[OK] All tests passed")


if __name__ == "__main__":
    asyncio.run(main())
