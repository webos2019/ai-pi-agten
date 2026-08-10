"""检查深圳A股是否包含创业板(300xxx)股票"""
import httpx
import json

headers = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# Get a larger page from sz_a and check for 300xxx stocks
url = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page=1&num=100&node=sz_a&sort=symbol&asc=1"
)
r = httpx.get(url, headers=headers, timeout=15)
data = r.json()

cyb_count = 0
sample_cyb = []
for s in data:
    code = s.get('code', '')
    if code.startswith('300'):
        cyb_count += 1
        if len(sample_cyb) < 5:
            sample_cyb.append(f"{s.get('symbol','')} {s.get('name','')}")

print(f"Total in this page: {len(data)}")
print(f"ChiNext (300xxx) stocks in sz_a page 1: {cyb_count}")
for s in sample_cyb:
    print(f"  {s}")

# Also check the last page of sz_a to see 300xxx stocks
url2 = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page=29&num=100&node=sz_a&sort=symbol&asc=1"
)
r2 = httpx.get(url2, headers=headers, timeout=15)
data2 = r2.json()
print(f"\nPage 29 (last): {len(data2)} stocks")
for s in data2[-5:]:
    print(f"  {s.get('symbol','')} {s.get('name','')} code={s.get('code','')}")
