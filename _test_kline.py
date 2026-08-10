import asyncio, httpx

async def test():
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz399006&scale=240&ma=no&datalen=5"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        data = r.json()
        for d in data:
            print(d["day"], d["close"])

asyncio.run(test())
