"""扫描 JasonWade001/chtxt 仓库结构"""
import httpx
import json

resp = httpx.get(
    "https://api.github.com/repos/JasonWade001/chtxt/contents/",
    headers={"User-Agent": "Pi-Agent/1.0"},
    timeout=15,
)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    items = resp.json()
    for item in items:
        print(f'{item["type"]:4s}  {item["name"]:40s}  {item.get("size",0):>10} bytes')
else:
    print(resp.text[:500])
