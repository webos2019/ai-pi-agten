"""测试搜索引擎可访问性"""
import asyncio
import httpx
from urllib.parse import quote_plus


async def test_duckduckgo():
    """测试 DuckDuckGo"""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus('菜根谭')}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            print(f"[DuckDuckGo] status={r.status_code}, len={len(r.text)}")
    except Exception as e:
        print(f"[DuckDuckGo] error: {type(e).__name__}: {e}")


async def test_bing():
    """测试 Bing"""
    url = "https://www.bing.com/search?q=菜根谭"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            print(f"[Bing] status={r.status_code}, len={len(r.text)}")
    except Exception as e:
        print(f"[Bing] error: {type(e).__name__}: {e}")


async def test_baidu():
    """测试百度"""
    url = "https://www.baidu.com/s?wd=菜根谭"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            print(f"[Baidu] status={r.status_code}, len={len(r.text)}")
    except Exception as e:
        print(f"[Baidu] error: {type(e).__name__}: {e}")


async def main():
    await test_duckduckgo()
    await test_bing()
    await test_baidu()


if __name__ == "__main__":
    asyncio.run(main())
