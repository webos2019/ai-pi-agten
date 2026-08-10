#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试深度搜索功能"""

import sys
import asyncio
import os

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 加载 .env
from dotenv import load_dotenv
load_dotenv()

# 导入搜索工具
import tools  # 触发自动注册
from tools.web_search import _generate_query_variants, _deep_search, execute


def test_query_variants():
    """测试查询变体生成"""
    print("=" * 60)
    print("测试 1: 查询变体生成")
    print("=" * 60)

    test_cases = [
        "什么是量子计算",
        "介绍一下《菜根谭》讲了什么",
        "帮我查查区块链的原理",
        "人工智能的发展历史",
        "什么是提示工程",
    ]

    for query in test_cases:
        variants = _generate_query_variants(query)
        print(f"\n原始查询: {query}")
        print(f"变体 ({len(variants)}):")
        for i, v in enumerate(variants, 1):
            print(f"  {i}. {v}")


async def test_deep_search():
    """测试深度搜索"""
    print("\n" + "=" * 60)
    print("测试 2: 深度搜索 (Bing)")
    print("=" * 60)

    query = "什么是量子计算"
    print(f"\n搜索: {query}")
    print("正在深度搜索 (可能需要 10-15 秒)...")

    result = await _deep_search(query, max_results=5, engine="auto")

    print(f"\n深度搜索: {result.get('deep_search', False)}")
    print(f"尝试的变体: {result.get('variants_tried', [])}")
    print(f"结果数量: {result.get('result_count', 0)}")
    print()

    for i, r in enumerate(result.get("results", []), 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        snippet = r.get("snippet", "")
        source = r.get("source_query", "")

        print(f"--- 结果 {i} ---")
        print(f"标题: {title}")
        print(f"URL: {url}")
        print(f"来源查询: {source}")
        if content:
            print(f"正文 ({len(content)} 字): {content[:200]}...")
        elif snippet:
            print(f"摘要: {snippet[:200]}")
        print()


async def test_execute():
    """测试 execute 函数"""
    print("\n" + "=" * 60)
    print("测试 3: execute() 完整调用")
    print("=" * 60)

    result = await execute(
        {"query": "区块链技术原理", "deep_search": True},
        {},
    )

    print(f"\n查询: {result.get('query', '')}")
    print(f"深度搜索: {result.get('deep_search', False)}")
    print(f"变体: {result.get('variants_tried', [])}")
    print(f"结果数: {result.get('result_count', 0)}")

    for i, r in enumerate(result.get("results", [])[:3], 1):
        print(f"\n--- 结果 {i} ---")
        print(f"标题: {r.get('title', '')}")
        print(f"URL: {r.get('url', '')}")
        content = r.get("content", "")
        if content:
            print(f"正文 ({len(content)} 字): {content[:150]}...")


if __name__ == "__main__":
    test_query_variants()
    asyncio.run(test_deep_search())
    asyncio.run(test_execute())
    print("\n✅ 所有测试完成!")
