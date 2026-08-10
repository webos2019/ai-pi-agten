"""测试 Bing 搜索功能"""
import asyncio
from tools.web_search import execute


async def main():
    # 测试1: 搜索菜根谭
    print("=" * 60)
    print("测试1: 搜索 '菜根谭 内容简介'")
    result = await execute({"query": "菜根谭 内容简介"}, {})
    print(f"结果: {result}")
    if result.get("results"):
        for i, r in enumerate(result["results"][:3], 1):
            print(f"  {i}. {r['title']}")
            print(f"     URL: {r['url']}")
            print(f"     摘要: {r['snippet'][:100]}...")
    print()

    # 测试2: 搜索技术概念
    print("=" * 60)
    print("测试2: 搜索 'Python GIL 全局解释器锁'")
    result = await execute({"query": "Python GIL 全局解释器锁"}, {})
    print(f"结果: {result}")
    if result.get("results"):
        for i, r in enumerate(result["results"][:3], 1):
            print(f"  {i}. {r['title']}")
            print(f"     URL: {r['url']}")
            print(f"     摘要: {r['snippet'][:100]}...")
    print()

    # 测试3: 搜索英文内容
    print("=" * 60)
    print("测试3: 搜索 'what is machine learning'")
    result = await execute({"query": "what is machine learning", "max_results": 3}, {})
    print(f"结果: {result}")
    if result.get("results"):
        for i, r in enumerate(result["results"][:3], 1):
            print(f"  {i}. {r['title']}")
            print(f"     URL: {r['url']}")


if __name__ == "__main__":
    asyncio.run(main())
