"""扫描沪深A股+创业板: 金叉 + ESG评分前30

策略:
  1. 从新浪获取全部沪深A股列表 (sh_a + sz_a, 含创业板300/301)
  2. 批量获取K线数据 (60根), 检测 MACD金叉 或 KDJ金叉 (最近3个交易日内)
  3. 对金叉股票, 获取财务基本面, 计算ESG评分
  4. 按ESG总分降序排列, 取前30只

注意: 全市场约5200只股票, 需要一定时间运行
"""

import asyncio
import json
import sys
import time
import re
from typing import Any

import httpx

# ─── 复用 stock_analysis.py 中的函数 ──────────────────────
sys.path.insert(0, "c:/newtask-pi/tools")
from stock_analysis import (
    SINA_HEADERS,
    _to_sina_symbol,
    _ema,
    _classify_env_risk,
    _GREEN_KEYWORDS,
    calc_esg,
)

# ════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════

KLINE_DATALen = 60          # K线条数
CONCURRENCY_KLINE = 30      # K线并发数
CONCURRENCY_FUND = 20       # 财务数据并发数
GOLDEN_CROSS_DAYS = 3       # 最近N个交易日内出现金叉
TOP_N = 30                  # 取前N只


# ════════════════════════════════════════════════════════
#  1. 获取全部股票列表
# ════════════════════════════════════════════════════════

async def fetch_all_stocks() -> list[dict[str, str]]:
    """获取沪深全部A股列表 (含创业板)

    返回: [{"symbol": "sh600000", "code": "600000", "name": "浦发银行"}, ...]
    """
    headers = SINA_HEADERS
    all_stocks: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for node in ["sh_a", "sz_a"]:
            # 先获取总数
            count_url = (
                f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"Market_Center.getHQNodeStockCount?node={node}"
            )
            resp = await client.get(count_url, headers=headers)
            total = int(resp.text.strip().strip('"'))
            pages = (total + 99) // 100
            print(f"  [{node}] 共 {total} 只股票, {pages} 页")

            for page in range(1, pages + 1):
                url = (
                    f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                    f"Market_Center.getHQNodeData?page={page}&num=100&node={node}&sort=symbol&asc=1"
                )
                try:
                    resp = await client.get(url, headers=headers)
                    data = resp.json()
                    if isinstance(data, list):
                        for s in data:
                            symbol = s.get("symbol", "")
                            code = s.get("code", "")
                            name = s.get("name", "")
                            if symbol and code:
                                all_stocks.append({
                                    "symbol": symbol,
                                    "code": code,
                                    "name": name,
                                })
                except Exception as e:
                    print(f"    [警告] {node} page {page} 获取失败: {e}")

                if page % 5 == 0:
                    print(f"    [{node}] 已获取 {page}/{pages} 页, 累计 {len(all_stocks)} 只")
                    await asyncio.sleep(0.1)  # 小憩

    return all_stocks


# ════════════════════════════════════════════════════════
#  2. 获取K线 & 检测金叉
# ════════════════════════════════════════════════════════

async def fetch_klines(client: httpx.AsyncClient, symbol: str, datalen: int = 60) -> dict | None:
    """获取日K线数据"""
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
    )
    try:
        resp = await client.get(url, headers=SINA_HEADERS)
        if resp.status_code != 200:
            return None
        klines = resp.json()
        if not klines or len(klines) < 30:
            return None

        return {
            "dates": [k.get("day", "") for k in klines],
            "opens": [float(k.get("open", 0)) for k in klines],
            "closes": [float(k.get("close", 0)) for k in klines],
            "highs": [float(k.get("high", 0)) for k in klines],
            "lows": [float(k.get("low", 0)) for k in klines],
            "volumes": [float(k.get("volume", 0)) for k in klines],
        }
    except Exception:
        return None


def check_macd_golden_cross(closes: list[float], lookback: int = 3) -> bool:
    """检测最近N个交易日内是否出现MACD金叉

    金叉: DIF 从下方上穿 DEA
    """
    if len(closes) < 35:
        return False

    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = _ema(dif, 9)

    n = len(closes)
    for i in range(max(1, n - lookback), n):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            return True
    return False


def check_kdj_golden_cross(
    highs: list[float], lows: list[float], closes: list[float], n: int = 9, lookback: int = 3
) -> bool:
    """检测最近N个交易日内是否出现KDJ金叉

    金叉: K 从下方上穿 D
    """
    if len(closes) < n + 1:
        return False

    k_values: list[float] = [50.0]
    d_values: list[float] = [50.0]

    for i in range(1, len(closes)):
        start = max(0, i - n + 1)
        hh = max(highs[start : i + 1])
        ll = min(lows[start : i + 1])
        if hh == ll:
            rsv = 50.0
        else:
            rsv = (closes[i] - ll) / (hh - ll) * 100
        k = (2 / 3) * k_values[-1] + (1 / 3) * rsv
        d = (2 / 3) * d_values[-1] + (1 / 3) * k
        k_values.append(k)
        d_values.append(d)

    total = len(k_values)
    for i in range(max(1, total - lookback), total):
        if k_values[i] > d_values[i] and k_values[i - 1] <= d_values[i - 1]:
            return True
    return False


async def scan_golden_cross(stocks: list[dict[str, str]]) -> list[dict]:
    """批量扫描所有股票, 筛选金叉股票

    返回: [{"symbol", "code", "name", "macd_cross", "kdj_cross", "klines"}, ...]
    """
    sem = asyncio.Semaphore(CONCURRENCY_KLINE)
    results: list[dict] = []
    processed = 0
    total = len(stocks)
    start_time = time.time()

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def check_one(stock: dict[str, str]):
            nonlocal processed
            async with sem:
                klines = await fetch_klines(client, stock["symbol"], KLINE_DATALen)
                processed += 1

                if klines is None:
                    if processed % 200 == 0:
                        elapsed = time.time() - start_time
                        rate = processed / elapsed if elapsed > 0 else 0
                        eta = (total - processed) / rate if rate > 0 else 0
                        print(f"  进度: {processed}/{total} ({processed/total*100:.1f}%) "
                              f"金叉: {len(results)} | 速率: {rate:.1f}/s | ETA: {eta:.0f}s")
                    return

                closes = klines["closes"]
                highs = klines["highs"]
                lows = klines["lows"]

                macd_cross = check_macd_golden_cross(closes, GOLDEN_CROSS_DAYS)
                kdj_cross = check_kdj_golden_cross(highs, lows, closes, lookback=GOLDEN_CROSS_DAYS)

                if macd_cross or kdj_cross:
                    cross_types = []
                    if macd_cross:
                        cross_types.append("MACD金叉")
                    if kdj_cross:
                        cross_types.append("KDJ金叉")

                    results.append({
                        "symbol": stock["symbol"],
                        "code": stock["code"],
                        "name": stock["name"],
                        "cross_types": cross_types,
                        "klines": klines,
                        "last_close": closes[-1],
                    })

                if processed % 200 == 0:
                    elapsed = time.time() - start_time
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (total - processed) / rate if rate > 0 else 0
                    print(f"  进度: {processed}/{total} ({processed/total*100:.1f}%) "
                          f"金叉: {len(results)} | 速率: {rate:.1f}/s | ETA: {eta:.0f}s")

        tasks = [check_one(s) for s in stocks]
        await asyncio.gather(*tasks)

    return results


# ════════════════════════════════════════════════════════
#  3. 获取基本面 & 计算ESG
# ════════════════════════════════════════════════════════

async def fetch_fundamentals(client: httpx.AsyncClient, code: str) -> dict[str, Any]:
    """从新浪财经获取基本面数据"""
    url = (
        f"https://money.finance.sina.com.cn/corp/go.php/"
        f"vFD_FinancialGuideLine/stockid/{code}/displaytype/4.phtml"
    )
    try:
        resp = await client.get(url, headers=SINA_HEADERS, timeout=12)
        if resp.status_code != 200:
            return {}
        text = resp.content.decode("gbk", errors="replace")

        # 提取股票名称
        title_match = re.search(r"<title>(.+?)\(", text)
        stock_name = title_match.group(1) if title_match else ""

        def _extract_value(keyword: str) -> float | None:
            idx = text.find(keyword)
            if idx < 0:
                return None
            snippet = text[idx:idx + 500]
            tds = re.findall(r"<td[^>]*>([\d.]+)</td>", snippet)
            return float(tds[0]) if tds else None

        result: dict[str, Any] = {"stock_name": stock_name}
        for key, field in [
            ("净资产收益率", "roe"),
            ("资产负债率", "debt_ratio"),
            ("净利率", "net_margin"),
            ("每股收益", "eps"),
            ("每股净资产", "bvps"),
            ("流动比率", "current_ratio"),
            ("速动比率", "quick_ratio"),
        ]:
            val = _extract_value(key)
            if val is not None:
                result[field] = val

        return result
    except Exception:
        return {}


async def calculate_esg_for_stocks(golden_stocks: list[dict]) -> list[dict]:
    """为金叉股票计算ESG评分"""
    sem = asyncio.Semaphore(CONCURRENCY_FUND)
    results: list[dict] = []
    processed = 0
    total = len(golden_stocks)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def process_one(stock: dict):
            nonlocal processed
            async with sem:
                code = stock["code"]
                fund = await fetch_fundamentals(client, code)
                processed += 1

                # 用列表中的名称作为后备
                stock_name = fund.get("stock_name", "") or stock["name"]

                klines = stock["klines"]
                esg = calc_esg(fund, stock_name, klines["closes"], klines["volumes"])

                results.append({
                    "symbol": stock["symbol"],
                    "code": code,
                    "name": stock_name or stock["name"],
                    "last_close": stock["last_close"],
                    "cross_types": stock["cross_types"],
                    "esg_total": esg["total_score"],
                    "esg_e": esg["e_score"],
                    "esg_s": esg["s_score"],
                    "esg_g": esg["g_score"],
                    "esg_verdict": esg["verdict"],
                    "fundamentals": esg.get("fundamentals", {}),
                    "esg_signals_e": esg["dimensions"][0]["signals"],
                    "esg_signals_s": esg["dimensions"][1]["signals"],
                    "esg_signals_g": esg["dimensions"][2]["signals"],
                })

                if processed % 10 == 0:
                    print(f"  ESG计算进度: {processed}/{total}")

        tasks = [process_one(s) for s in golden_stocks]
        await asyncio.gather(*tasks)

    # 按ESG总分降序排列
    results.sort(key=lambda x: x["esg_total"], reverse=True)
    return results


# ════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════

async def main():
    print("=" * 70)
    print("  沪深A股+创业板 金叉 + ESG评分 扫描器")
    print("=" * 70)

    # Step 1: 获取股票列表
    print("\n[1/4] 获取股票列表...")
    t0 = time.time()
    stocks = await fetch_all_stocks()
    print(f"  共获取 {len(stocks)} 只股票 ({time.time()-t0:.1f}s)")

    # 统计创业板数量
    cyb_count = sum(1 for s in stocks if s["code"].startswith("300") or s["code"].startswith("301"))
    print(f"  其中创业板(300/301): {cyb_count} 只")

    # Step 2: 扫描金叉
    print(f"\n[2/4] 扫描金叉信号 (最近{GOLDEN_CROSS_DAYS}个交易日)...")
    t0 = time.time()
    golden_stocks = await scan_golden_cross(stocks)
    print(f"  金叉股票: {len(golden_stocks)} 只 ({time.time()-t0:.1f}s)")

    # 分类统计
    macd_only = sum(1 for s in golden_stocks if s["cross_types"] == ["MACD金叉"])
    kdj_only = sum(1 for s in golden_stocks if s["cross_types"] == ["KDJ金叉"])
    both = sum(1 for s in golden_stocks if len(s["cross_types"]) == 2)
    print(f"  MACD金叉: {macd_only}, KDJ金叉: {kdj_only}, 双金叉: {both}")

    if not golden_stocks:
        print("  未发现金叉股票!")
        return

    # Step 3: 计算ESG
    print(f"\n[3/4] 计算ESG评分 ({len(golden_stocks)} 只金叉股票)...")
    t0 = time.time()
    esg_results = await calculate_esg_for_stocks(golden_stocks)
    print(f"  ESG计算完成 ({time.time()-t0:.1f}s)")

    # Step 4: 输出前30
    print(f"\n[4/4] ESG评分前{TOP_N}:")
    print("=" * 100)

    top = esg_results[:TOP_N]
    for i, s in enumerate(top, 1):
        fund = s["fundamentals"]
        roe_str = f"ROE={fund.get('roe','?')}" if fund.get("roe") else "ROE=N/A"
        debt_str = f"负债率={fund.get('debt_ratio','?')}%" if fund.get("debt_ratio") else "负债率=N/A"
        eps_str = f"EPS={fund.get('eps','?')}" if fund.get("eps") else "EPS=N/A"

        print(
            f"  {i:2d}. {s['name']:<8s} ({s['code']}) "
            f"价:{s['last_close']:<8.2f} "
            f"{'+'.join(s['cross_types']):<12s} "
            f"ESG:{s['esg_total']}/9 "
            f"(E={s['esg_e']} S={s['esg_s']} G={s['esg_g']}) "
            f"{s['esg_verdict']:<12s} "
            f"| {roe_str} {debt_str} {eps_str}"
        )

    # 保存完整结果到JSON
    output = {
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks_scanned": len(stocks),
        "golden_cross_count": len(golden_stocks),
        "top_n": TOP_N,
        "results": [
            {
                "rank": i,
                "code": s["code"],
                "name": s["name"],
                "symbol": s["symbol"],
                "last_close": s["last_close"],
                "cross_types": s["cross_types"],
                "esg_total": s["esg_total"],
                "esg_e": s["esg_e"],
                "esg_s": s["esg_s"],
                "esg_g": s["esg_g"],
                "esg_verdict": s["esg_verdict"],
                "fundamentals": s["fundamentals"],
                "signals_e": s["esg_signals_e"],
                "signals_s": s["esg_signals_s"],
                "signals_g": s["esg_signals_g"],
            }
            for i, s in enumerate(top, 1)
        ],
    }

    with open("c:/newtask-pi/_scan_result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  完整结果已保存至 _scan_result.json")

    # 也保存所有金叉股票的ESG结果
    all_output = {
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_golden_cross": len(esg_results),
        "all_results": [
            {
                "rank": i,
                "code": s["code"],
                "name": s["name"],
                "last_close": s["last_close"],
                "cross_types": s["cross_types"],
                "esg_total": s["esg_total"],
                "esg_e": s["esg_e"],
                "esg_s": s["esg_s"],
                "esg_g": s["esg_g"],
                "esg_verdict": s["esg_verdict"],
                "fundamentals": s["fundamentals"],
            }
            for i, s in enumerate(esg_results, 1)
        ],
    }
    with open("c:/newtask-pi/_scan_all_golden.json", "w", encoding="utf-8") as f:
        json.dump(all_output, f, ensure_ascii=False, indent=2)
    print(f"  全部金叉股票ESG结果已保存至 _scan_all_golden.json")

    print("\n" + "=" * 70)
    print("  扫描完成!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
