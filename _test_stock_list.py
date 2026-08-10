"""测试新浪股票列表API"""
import httpx
import json

headers = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# 新浪节点:
# sh_a = 上海A股, sz_a = 深圳A股, sz_cyb = 创业板
# 也可以用 hs_a (沪深A股全部)

nodes = ["sh_a", "sz_a", "sz_cyb"]

for node in nodes:
    url = (
        f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page=1&num=10&node={node}&sort=symbol&asc=1"
    )
    r = httpx.get(url, headers=headers, timeout=10)
    print(f"Node {node}: status={r.status_code}, len={len(r.text)}")
    if r.status_code == 200 and r.text.strip():
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                print(f"  Got {len(data)} items, sample: {data[0].get('symbol','?')} {data[0].get('name','?')}")
        except:
            print(f"  Parse error: {r.text[:200]}")
    print()

# Try getting total count
for node in nodes:
    url = (
        f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeStockCount?node={node}"
    )
    r = httpx.get(url, headers=headers, timeout=10)
    print(f"Count {node}: {r.text.strip()}")
