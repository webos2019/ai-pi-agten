"""股票行情工具 — 新浪财经实时行情（A股/创业板/ETF，无需 API Key）

数据来源: 新浪财经实时行情接口
- 行情: hq.sinajs.cn/list=sz000001,sh600519
- 搜索: suggest3.sinajs.cn/suggest/type=

支持:
- 沪市 (60xxxx)、深市 (00xxxx)、创业板 (30xxxx)、科创板 (68xxxx)
- ETF (51xxxx/15xxxx/50xxxx)
- 指数 (sh000001/sz399001 等)
- 批量查询多只股票
"""

import re
import json
import asyncio
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


# 新浪行情接口
SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
SINA_SEARCH_URL = "https://suggest3.sinajs.cn/suggest/type="

# 请求头（新浪必须带 Referer）
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _to_sina_code(code: str) -> str:
    """将股票代码转为新浪格式 (sh/sz + 6位代码)"""
    code = code.strip().lower()
    # 已带前缀
    if code.startswith(("sh", "sz")):
        return code
    # 纯数字
    if code.isdigit() and len(code) == 6:
        # 上证指数特殊处理（必须在沪市判断之前）
        if code == "000001":
            return "sh000001"
        if code[0] in ("6", "5", "7", "9"):  # 沪市: 60/68/50/51/52/73/78
            return f"sh{code}"
        else:  # 深市: 00/30/15/16/12
            return f"sz{code}"
    return f"sz{code}"


def _format_volume(vol: float) -> str:
    """格式化成交量（新浪返回的是股数，100股=1手）"""
    hands = vol / 100
    if hands >= 100_000_000:
        return f"{hands / 100_000_000:.2f}亿手"
    if hands >= 10_000:
        return f"{hands / 10_000:.2f}万手"
    return f"{hands:.0f}手"


def _format_amount(amt: float) -> str:
    """格式化成交额"""
    if amt >= 100_000_000:
        return f"{amt / 100_000_000:.2f}亿"
    if amt >= 10_000:
        return f"{amt / 10_000:.2f}万"
    return f"{amt:.2f}"


def _format_market_cap(cap: float) -> str:
    """格式化市值"""
    if cap >= 100_000_000_000:
        return f"{cap / 100_000_000_000:.2f}万亿"
    if cap >= 100_000_000:
        return f"{cap / 100_000_000:.2f}亿"
    if cap >= 10_000:
        return f"{cap / 10_000:.2f}万"
    return f"{cap:.2f}"


async def _fetch_quotes(code_list: list[str]) -> list[dict[str, Any]]:
    """从新浪获取批量行情（一次请求）"""
    sina_codes = [_to_sina_code(c) for c in code_list]
    url = SINA_QUOTE_URL + ",".join(sina_codes)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=SINA_HEADERS)
        if resp.status_code != 200:
            return [{"error": f"HTTP {resp.status_code}"} for _ in code_list]

        # 新浪返回 GBK 编码的文本
        text = resp.content.decode("gbk", errors="replace")

    results = []
    # 解析每行: var hq_str_sh600519="贵州茅台,1815.00,1812.00,...";
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines:
        result = _parse_sina_line(line)
        results.append(result)

    # 补齐缺失的结果
    while len(results) < len(code_list):
        results.append({"error": "数据缺失"})

    return results


def _parse_sina_line(line: str) -> dict[str, Any]:
    """解析新浪行情单行数据"""
    # var hq_str_sh600519="名称,今开,昨收,最新价,最高,最低,...";
    match = re.match(r'var hq_str_\w+="(.+)"', line)
    if not match:
        return {"error": "解析失败", "raw": line[:100]}

    fields = match.group(1).split(",")
    if len(fields) < 10:
        return {"error": "数据不完整"}

    # 新浪行情字段（股票/ETF 格式）:
    # 0:名称 1:今开 2:昨收 3:最新价 4:最高 5:最低
    # 6:买一价 7:卖一价 8:成交量(股) 9:成交额(元)
    # 10-19: 五档买卖量价 20:日期 21:时间 22:状态
    name = fields[0]
    open = float(fields[1]) if fields[1] else 0
    pre_close = float(fields[2]) if fields[2] else 0
    price = float(fields[3]) if fields[3] else 0
    high = float(fields[4]) if fields[4] else 0
    low = float(fields[5]) if fields[5] else 0
    volume = float(fields[8]) if fields[8] else 0  # 股
    amount = float(fields[9]) if fields[9] else 0  # 元
    date = fields[30] if len(fields) > 30 else ""
    time_str = fields[31] if len(fields) > 31 else ""
    status = fields[32] if len(fields) > 32 else ""

    # 计算涨跌
    change = round(price - pre_close, 2) if pre_close else 0
    change_pct = round((change / pre_close) * 100, 2) if pre_close else 0

    # 振幅
    amplitude = round(((high - low) / pre_close) * 100, 2) if pre_close else 0

    # 换手率无法从新浪基础行情获取，留空
    # PE/PB 也需要额外接口，这里留空

    # 从代码提取市场和纯数字代码
    code_match = re.match(r'var hq_str_(\w+?)(\d{6})', line)
    market = code_match.group(1) if code_match else ""
    code = code_match.group(2) if code_match else ""

    result = {
        "code": code,
        "name": name,
        "market": market.upper(),
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "change_pct_str": f"{change_pct:+.2f}%",
        "open": open,
        "high": high,
        "low": low,
        "pre_close": pre_close,
        "volume": volume,
        "amount": amount,
        "amplitude": amplitude,
        "amplitude_str": f"{amplitude:.2f}%",
        "volume_str": _format_volume(volume),
        "amount_str": _format_amount(amount),
        "date": date,
        "time": time_str,
        "status": status,
    }

    # 涨停/跌停计算 (主板±10%, 创业板/科创板±20%, ETF±10%)
    if code and pre_close:
        if code.startswith("30") or code.startswith("68"):
            limit_pct = 0.20
        elif code.startswith(("50", "51", "52", "15", "16")):
            limit_pct = 0.10  # ETF
        else:
            limit_pct = 0.10  # 主板
        result["limit_up"] = round(pre_close * (1 + limit_pct), 2)
        result["limit_down"] = round(pre_close * (1 - limit_pct), 2)

    # 趋势
    if change > 0:
        result["trend"] = "up"
    elif change < 0:
        result["trend"] = "down"
    else:
        result["trend"] = "flat"

    return result


def _extract_stock_codes(text: str) -> list[str]:
    """从用户输入中提取6位股票代码列表，支持 '名称 代码' 格式"""
    # 先匹配所有6位数字代码
    codes = re.findall(r'\b(\d{6})\b', text)
    if codes:
        return codes
    # 再匹配 sh/sz 前缀代码
    codes = re.findall(r'\b(sh|sz)(\d{6})\b', text, re.IGNORECASE)
    if codes:
        return [c[0].lower() + c[1] for c in codes]
    # 兜底：按空白/逗号分隔
    return [c.strip() for c in re.split(r"[,\s]+", text) if c.strip()]


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    codes = args.get("codes") or args.get("code") or ""

    if isinstance(codes, str):
        code_list = _extract_stock_codes(codes)
    elif isinstance(codes, list):
        code_list = [str(c).strip() for c in codes if c]
    else:
        return {"error": "codes 参数必须为股票代码字符串或数组"}

    if not code_list:
        return {"error": "请提供至少一个股票代码"}

    if len(code_list) > 10:
        return {"error": "一次最多查询 10 只股票"}

    try:
        results = await _fetch_quotes(code_list)
    except Exception as e:
        return {"error": f"行情查询失败: {e}"}

    quotes = []
    errors = []
    for code, result in zip(code_list, results):
        if "error" in result:
            errors.append({"code": code, "error": result["error"]})
        else:
            quotes.append(result)

    response: dict[str, Any] = {
        "count": len(quotes),
        "quotes": quotes,
    }
    if errors:
        response["errors"] = errors
    if len(quotes) == 1:
        response.update(quotes[0])

    return response


# ─── 股票搜索 ──────────────────────────────────────────

async def execute_search(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """搜索股票/ETF/指数"""
    keyword = args.get("keyword", "").strip()
    if not keyword:
        return {"error": "搜索关键词不能为空"}

    # 新浪搜索接口: https://suggest3.sinajs.cn/suggest/type=&key=关键词&name=suggestdata
    url = f"https://suggest3.sinajs.cn/suggest/type=&key={keyword}&name=suggestdata"

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers=SINA_HEADERS)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}

        text = resp.content.decode("gbk", errors="replace")

    # 解析: var suggestdata="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99,1,ESG,,"
    match = re.search(r'var suggestdata="(.+?)"', text)
    if not match or not match.group(1):
        return {"keyword": keyword, "count": 0, "results": []}

    results = []
    for item in match.group(1).split(";"):
        parts = item.split(",")
        if len(parts) < 4:
            continue
        name = parts[0]
        # full_code 在第 3 个位置 (sh600519)
        full_code = parts[3]
        simple_code = full_code[2:] if len(full_code) > 2 and full_code[:2] in ("sh", "sz") else parts[2]
        market = full_code[:2].upper() if len(full_code) > 2 else ""

        # 只保留 A 股市场
        if market not in ("SH", "SZ"):
            continue

        # 类型判断
        if simple_code.startswith(("50", "51", "52", "15", "16")):
            stock_type = "ETF"
        elif simple_code.startswith("30"):
            stock_type = "创业板"
        elif simple_code.startswith("68"):
            stock_type = "科创板"
        elif simple_code.startswith("60"):
            stock_type = "沪市A股"
        elif simple_code.startswith("00"):
            stock_type = "深市A股"
        elif simple_code.startswith(("000001", "399")):
            stock_type = "指数"
        else:
            stock_type = "其他"

        results.append({
            "code": simple_code,
            "name": name,
            "type": stock_type,
            "market": market,
            "full_code": full_code,
        })

        if len(results) >= 10:
            break

    return {
        "keyword": keyword,
        "count": len(results),
        "results": results,
    }


# ─── 注册 ──────────────────────────────────────────────

def register():
    tool_registry.register(ChatToolDefinition(
        name="stock_quote",
        description="查询A股实时行情(沪深/创业板/科创板/ETF/指数)，支持批量",
        parameters={
            "type": "object",
            "properties": {
                "codes": {"type": "string"},
            },
            "required": ["codes"],
        },
        execute=execute,
        format_input=lambda args: f"股票行情: {args.get('codes', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.9,
        keywords=["股票", "行情", "A股", "创业板", "ETF", "涨跌", "股价", "stock", "quote", "市值", "涨停", "跌停"],
    ))

    tool_registry.register(ChatToolDefinition(
        name="stock_search",
        description="搜索A股股票/ETF/指数的代码和名称",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
            },
            "required": ["keyword"],
        },
        execute=execute_search,
        format_input=lambda args: f"搜索股票: {args.get('keyword', '')}",
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.85,
        keywords=["搜索", "查找", "股票代码", "search", "stock search"],
    ))
