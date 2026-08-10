"""测试股票分析 API"""
import httpx

r = httpx.post('http://127.0.0.1:8000/api/stock-analysis', json={'code': '000725'}, timeout=30)
print(f"HTTP status: {r.status_code}")
d = r.json()
print(f"code: {d.get('code')}")
print(f"close: {d.get('close')}")
print(f"stock_name: {d.get('stock_name')}")
print(f"factors count: {len(d.get('factors', []))}")
print(f"tech verdict: {d.get('summary', {}).get('verdict')}")
esg = d.get('esg', {})
print(f"esg verdict: {esg.get('verdict')}")
print(f"esg score: {esg.get('total_score')}/{esg.get('max_score')}")
print("API OK!")
