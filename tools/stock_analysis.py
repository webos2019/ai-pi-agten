"""股票技术分析工具 — 趋势/动量 7 因子 + ESG 有效性评判体系

技术面 7 因子:
  1. MACD     — 指数平滑异同移动平均线 (Gerald Appel, 1970s)
  2. ROC      — 价格变动率 (Fred Hitschler, 1970s)
  3. Williams %R — 威廉指标 (Larry Williams, 1973)
  4. RSI      — 相对强弱指数 (J.W. Wilder Jr., 1978)
  5. KDJ      — 随机指标 (George Lane, 1950s)
  6. CCI      — 顺势指标 (Donald Lambert, 1980)
  7. DMI/ADX  — 趋向指标 (J.W. Wilder Jr., 1978)

ESG 有效性因子 (基本面维度):
  E (Environmental) — 行业环境风险 + 绿色业务占比
  S (Social)        — 盈利能力 + 流动性健康 + 市场认可
  G (Governance)    — ROE + 资产负债率 + 每股收益趋势

数据来源:
  K线: 新浪财经 (money.finance.sina.com.cn)
  财务: 新浪财经财务指标页 (vFD_FinancialGuideLine)

评判规则:
  技术面: 7 因子中 >=5 个偏多 → 偏多（强信号）; 3-4 → 中性偏多; <=2 → 偏空
  ESG:    E/S/G 各维度满分 3 分，总分 9 分; >=6 为 ESG 有效（正面）; 3-5 中性; <=2 负面
"""

import json
import re
from typing import Any
from dataclasses import dataclass

import httpx

from tool_registry import tool_registry, ChatToolDefinition


# ─── 新浪财经 K 线接口 ──────────────────────────────────

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _to_sina_symbol(code: str) -> str:
    """将股票代码转为新浪格式 (sh/sz + 6位代码)"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    if code.isdigit() and len(code) == 6:
        if code[0] in ("6", "5", "7", "9"):
            return f"sh{code}"
        return f"sz{code}"
    return f"sz{code}"


async def _fetch_klines(code: str, limit: int = 120) -> dict[str, list]:
    """从新浪财经获取日 K 线数据

    返回: {"dates": [], "opens": [], "closes": [], "highs": [], "lows": [], "volumes": []}
    """
    symbol = _to_sina_symbol(code)
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=SINA_HEADERS)
        if resp.status_code != 200:
            raise RuntimeError(f"K线接口返回 HTTP {resp.status_code}")
        klines = resp.json()

    if not klines:
        raise RuntimeError(f"未获取到 {code} 的K线数据")

    # 解析: [{"day":"2026-07-08","open":"7.690","high":"7.900","low":"7.260","close":"7.630","volume":"3322287793"}, ...]
    result: dict[str, list] = {
        "dates": [], "opens": [], "closes": [],
        "highs": [], "lows": [], "volumes": [],
    }
    for k in klines:
        result["dates"].append(k.get("day", ""))
        result["opens"].append(float(k.get("open", 0)))
        result["closes"].append(float(k.get("close", 0)))
        result["highs"].append(float(k.get("high", 0)))
        result["lows"].append(float(k.get("low", 0)))
        result["volumes"].append(float(k.get("volume", 0)))

    return result


# ════════════════════════════════════════════════════════
#  指标计算函数
# ════════════════════════════════════════════════════════

def _ema(values: list[float], period: int) -> list[float]:
    """指数移动平均"""
    k = 2 / (period + 1)
    result = [values[0]]
    for i in range(1, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def _sma(values: list[float], period: int) -> list[float]:
    """简单移动平均"""
    result: list[float] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(values[i])  # 不足时用自身填充
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


# ─── 1. MACD ────────────────────────────────────────────

def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD 指标

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(DIF, signal)
    MACD柱 = (DIF - DEA) * 2

    偏多条件:
      - DIF > DEA → 多头排列 (+1)
      - DIF 上穿 DEA → 金叉 (+2)
      - DIF > 0 且 DEA > 0 → 零轴上方 (+1)
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = _ema(dif, signal)
    macd_bar = [(dif[i] - dea[i]) * 2 for i in range(len(closes))]

    last = len(closes) - 1
    prev = last - 1

    score = 0
    signals = []

    if dif[last] > dea[last]:
        score += 1
        signals.append("DIF>DEA 多头排列")
    else:
        signals.append("DIF<DEA 空头排列")

    # 金叉检测
    if prev >= 0 and dif[last] > dea[last] and dif[prev] <= dea[prev]:
        score += 2
        signals.append("MACD金叉信号")
    elif prev >= 0 and dif[last] < dea[last] and dif[prev] >= dea[prev]:
        signals.append("MACD死叉信号")

    # 零轴上方
    if dif[last] > 0 and dea[last] > 0:
        score += 1
        signals.append("零轴上方多头")

    is_bullish = score > 0

    return {
        "name": "MACD",
        "values": {
            "dif": round(dif[last], 4),
            "dea": round(dea[last], 4),
            "macd_bar": round(macd_bar[last], 4),
        },
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 2. ROC ─────────────────────────────────────────────

def calc_roc(closes: list[float], period: int = 12) -> dict:
    """价格变动率 (Rate of Change)

    ROC = (Close_t - Close_{t-n}) / Close_{t-n} * 100

    偏多条件:
      - ROC > 0 → 正动量 (+1)
      - ROC 上升 → 动量加速 (+1)
      - ROC 上穿 0 轴 → 由空转多 (+2)
    """
    if len(closes) < period + 1:
        return _insufficient_data("ROC")

    roc_values: list[float] = [0.0] * period
    for i in range(period, len(closes)):
        roc_values.append((closes[i] - closes[i - period]) / closes[i - period] * 100)

    last = len(roc_values) - 1
    prev = last - 1

    score = 0
    signals = []

    if roc_values[last] > 0:
        score += 1
        signals.append(f"ROC={roc_values[last]:.2f} 正动量")
    else:
        signals.append(f"ROC={roc_values[last]:.2f} 负动量")

    # 动量加速
    if prev >= period and roc_values[last] > roc_values[prev]:
        score += 1
        signals.append("动量加速")
    else:
        signals.append("动量减速")

    # 上穿 0 轴
    if prev >= period and roc_values[last] > 0 and roc_values[prev] <= 0:
        score += 2
        signals.append("ROC上穿0轴 由空转多")

    is_bullish = score > 0

    return {
        "name": "ROC",
        "values": {"roc": round(roc_values[last], 2)},
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 3. Williams %R ────────────────────────────────────

def calc_williams_r(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> dict:
    """威廉指标 (Williams %R)

    %R = (HighestHigh_n - Close) / (HighestHigh_n - LowestLow_n) * (-100)

    取值范围: -100 ~ 0
    -100 = 处于最低点, 0 = 处于最高点

    偏多条件:
      - %R > -20 → 强势区 (+1)
      - %R 从 -80 下方回升至 -80 上方 → 超卖反转 (+2)
      - %R > -50 → 中枢偏强 (+1)
    """
    if len(closes) < period:
        return _insufficient_data("Williams %R")

    wr_values: list[float] = [-50.0] * (period - 1)
    for i in range(period - 1, len(closes)):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        if hh == ll:
            wr_values.append(-50.0)
        else:
            wr_values.append((hh - closes[i]) / (hh - ll) * -100)

    last = len(wr_values) - 1
    prev = last - 1

    score = 0
    signals = []
    wr = wr_values[last]

    if wr > -20:
        score += 1
        signals.append(f"%R={wr:.1f} 强势区(>-20)")
    elif wr < -80:
        signals.append(f"%R={wr:.1f} 超卖区(<-80)")
    else:
        signals.append(f"%R={wr:.1f} 正常区间")

    # 超卖反转
    if prev >= period - 1 and wr_values[prev] < -80 and wr > -80:
        score += 2
        signals.append("超卖反转信号")

    # 中枢偏强
    if wr > -50:
        score += 1
        signals.append("中枢偏强(>-50)")

    is_bullish = score > 0

    return {
        "name": "Williams %R",
        "values": {"williams_r": round(wr, 1)},
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 4. RSI ─────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> dict:
    """相对强弱指数 (RSI) — Wilder 平滑法

    RS = AvgGain / AvgLoss
    RSI = 100 - 100 / (1 + RS)

    偏多条件:
      - RSI > 50 → 多方占优 (+1)
      - RSI 上升 → 多方增强 (+1)
      - RSI 上穿 50 → 由弱转强 (+2)
    """
    if len(closes) < period + 1:
        return _insufficient_data("RSI")

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    rsi_values: list[float] = [50.0] * period
    # 初始 SMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - 100 / (1 + rs))

    last = len(rsi_values) - 1
    prev = last - 1

    score = 0
    signals = []
    rsi_val = rsi_values[last]

    if rsi_val > 50:
        score += 1
        signals.append(f"RSI={rsi_val:.1f} 多方占优(>50)")
    else:
        signals.append(f"RSI={rsi_val:.1f} 空方占优(<50)")

    # RSI 上升
    if prev >= period and rsi_val > rsi_values[prev]:
        score += 1
        signals.append("RSI上升 多方增强")

    # 上穿 50
    if prev >= period and rsi_val > 50 and rsi_values[prev] <= 50:
        score += 2
        signals.append("RSI上穿50 由弱转强")

    is_bullish = score > 0

    return {
        "name": "RSI",
        "values": {"rsi": round(rsi_val, 1)},
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 5. KDJ ─────────────────────────────────────────────

def calc_kdj(highs: list[float], lows: list[float], closes: list[float], n: int = 9) -> dict:
    """KDJ 随机指标

    RSV = (Close - LL_n) / (HH_n - LL_n) * 100
    K = 2/3 * K_prev + 1/3 * RSV
    D = 2/3 * D_prev + 1/3 * K
    J = 3K - 2D

    偏多条件:
      - K > D → 多头排列 (+1)
      - K 上穿 D → 金叉 (+2)
      - J > 0 且 K,D > 20 → 超卖回升 (+1)
    """
    if len(closes) < n:
        return _insufficient_data("KDJ")

    k_values: list[float] = [50.0]
    d_values: list[float] = [50.0]
    j_values: list[float] = [50.0]

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
        j = 3 * k - 2 * d
        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    last = len(k_values) - 1
    prev = last - 1

    score = 0
    signals = []
    k_val, d_val, j_val = k_values[last], d_values[last], j_values[last]

    if k_val > d_val:
        score += 1
        signals.append(f"K={k_val:.1f}>D={d_val:.1f} 多头排列")
    else:
        signals.append(f"K={k_val:.1f}<D={d_val:.1f} 空头排列")

    # 金叉
    if prev >= 0 and k_val > d_val and k_values[prev] <= d_values[prev]:
        score += 2
        signals.append("KDJ金叉信号")

    # 超卖回升
    if j_val > 0 and k_val > 20 and d_val > 20:
        score += 1
        signals.append(f"J={j_val:.1f} 超卖回升")

    is_bullish = score > 0

    return {
        "name": "KDJ",
        "values": {"k": round(k_val, 1), "d": round(d_val, 1), "j": round(j_val, 1)},
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 6. CCI ─────────────────────────────────────────────

def calc_cci(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> dict:
    """顺势指标 (Commodity Channel Index)

    TP = (High + Low + Close) / 3
    MA_TP = SMA(TP, n)
    MD = mean(|TP_i - MA_TP|)
    CCI = (TP - MA_TP) / (0.015 * MD)

    偏多条件:
      - CCI > 0 → 零轴上方 (+1)
      - CCI > +100 → 强势区 (+1)
      - CCI 上穿 -100 → 超卖回升 (+1)
    """
    if len(closes) < period:
        return _insufficient_data("CCI")

    tps = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    cci_values: list[float] = [0.0] * (period - 1)

    for i in range(period - 1, len(tps)):
        ma_tp = sum(tps[i - period + 1 : i + 1]) / period
        md = sum(abs(tps[j] - ma_tp) for j in range(i - period + 1, i + 1)) / period
        if md == 0:
            cci_values.append(0.0)
        else:
            cci_values.append((tps[i] - ma_tp) / (0.015 * md))

    last = len(cci_values) - 1
    prev = last - 1

    score = 0
    signals = []
    cci_val = cci_values[last]

    if cci_val > 0:
        score += 1
        signals.append(f"CCI={cci_val:.1f} 零轴上方")
    else:
        signals.append(f"CCI={cci_val:.1f} 零轴下方")

    if cci_val > 100:
        score += 1
        signals.append("CCI>+100 强势区")

    # 上穿 -100
    if prev >= period - 1 and cci_values[prev] < -100 and cci_val > -100:
        score += 1
        signals.append("CCI上穿-100 超卖回升")

    is_bullish = score > 0

    return {
        "name": "CCI",
        "values": {"cci": round(cci_val, 1)},
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ─── 7. DMI/ADX ────────────────────────────────────────

def calc_dmi(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> dict:
    """趋向指标 (Directional Movement Index)

    +DM, -DM, TR → Wilder 平滑 → +DI, -DI → DX → ADX

    偏多条件:
      - +DI > -DI → 多头方向 (+1)
      - ADX > 25 → 强趋势确认 (+1)
      - +DI 上穿 -DI → 多头转向 (+2)
    """
    if len(closes) < period + 1:
        return _insufficient_data("DMI/ADX")

    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    tr: list[float] = [0.0]

    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    # Wilder 平滑
    plus_di: list[float] = [0.0] * period
    minus_di: list[float] = [0.0] * period
    dx_values: list[float] = [0.0] * (2 * period - 1)

    atr = sum(tr[:period])
    apdm = sum(plus_dm[:period])
    mdm = sum(minus_dm[:period])

    pdi_val, mdi_val = 0.0, 0.0

    for i in range(period, len(closes)):
        atr = atr - atr / period + tr[i]
        apdm = apdm - apdm / period + plus_dm[i]
        mdm = mdm - mdm / period + minus_dm[i]

        pdi_val = apdm / atr * 100 if atr else 0
        mdi_val = mdm / atr * 100 if atr else 0
        plus_di.append(pdi_val)
        minus_di.append(mdi_val)

        denom = pdi_val + mdi_val
        dx = abs(pdi_val - mdi_val) / denom * 100 if denom else 0
        dx_values.append(dx)

    # ADX = Wilder 平滑 DX
    if len(dx_values) > 2 * period:
        adx_val = sum(dx_values[period : 2 * period]) / period
        for i in range(2 * period, len(dx_values)):
            adx_val = (adx_val * (period - 1) + dx_values[i]) / period
    else:
        adx_val = 0.0

    last = len(plus_di) - 1
    prev = last - 1

    score = 0
    signals = []

    if pdi_val > mdi_val:
        score += 1
        signals.append(f"+DI={pdi_val:.1f}>-DI={mdi_val:.1f} 多头方向")
    else:
        signals.append(f"+DI={pdi_val:.1f}<-DI={mdi_val:.1f} 空头方向")

    if adx_val > 25:
        score += 1
        signals.append(f"ADX={adx_val:.1f}>25 强趋势确认")

    # +DI 上穿 -DI
    if prev >= period and plus_di[last] > minus_di[last] and plus_di[prev] <= minus_di[prev]:
        score += 2
        signals.append("+DI上穿-DI 多头转向")

    is_bullish = score > 0

    return {
        "name": "DMI/ADX",
        "values": {
            "plus_di": round(pdi_val, 1),
            "minus_di": round(mdi_val, 1),
            "adx": round(adx_val, 1),
        },
        "score": score,
        "is_bullish": is_bullish,
        "signals": signals,
    }


# ════════════════════════════════════════════════════════
#  ESG 有效性因子
# ════════════════════════════════════════════════════════

# 行业环境风险映射 (E 因子)
# 基于行业碳排放、污染程度、资源消耗的综合评估
_INDUSTRY_ENV_RISK: dict[str, int] = {
    # 高污染高排放 → 环境风险 0 分
    "化工": 0, "化学": 0, "钢铁": 0, "有色": 0, "采掘": 0,
    "煤炭": 0, "电力": 0, "火电": 0, "水泥": 0, "造纸": 0,
    "纺织": 0, "印染": 0, "冶金": 0, "石油": 0, "石化": 0,
    # 中等环境风险 → 1 分
    "制造": 1, "汽车": 1, "机械": 1, "电气": 1, "家电": 1,
    "建筑": 1, "建材": 1, "运输": 1, "物流": 1, "航空": 1,
    "航运": 1, "农业": 1, "养殖": 1, "食品": 1, "饮料": 1,
    "电子": 1, "半导体": 1, "芯片": 1, "光电": 1, "显示": 1,
    # 低环境风险 → 2 分
    "医药": 2, "医疗": 2, "生物": 2, "软件": 2, "信息": 2,
    "通信": 2, "互联网": 2, "传媒": 2, "游戏": 2, "教育": 2,
    "金融": 2, "银行": 2, "保险": 2, "证券": 2, "房地产": 2,
    "商业": 2, "零售": 2, "贸易": 2, "服务": 2, "咨询": 2,
    # 绿色产业 → 3 分
    "新能源": 3, "光伏": 3, "风电": 3, "储能": 3, "环保": 3,
    "节能": 3, "碳": 3, "氢能": 3, "锂电": 3, "充电": 3,
}

# 绿色概念关键词 (用于加分)
_GREEN_KEYWORDS = ["新能源", "光伏", "风电", "储能", "环保", "节能", "碳中和",
                    "氢能", "锂电", "充电桩", "电池", "太阳能", "绿色"]

# 高污染行业关键词
_POLLUTION_KEYWORDS = ["化工", "化学", "钢铁", "有色", "煤炭", "火电", "水泥",
                        "造纸", "印染", "冶金", "石油", "石化", "采掘"]


# 知名股票的绿色/高污染行业映射 (股票名称不含关键词时使用)
_STOCK_INDUSTRY_OVERRIDE: dict[str, tuple[int, str]] = {
    # 绿色产业
    "宁德时代": (3, "绿色产业(锂电) 环境风险低"),
    "比亚迪": (3, "绿色产业(新能源车+电池) 环境风险低"),
    "隆基绿能": (3, "绿色产业(光伏) 环境风险低"),
    "阳光电源": (3, "绿色产业(光伏逆变器) 环境风险低"),
    "通威股份": (3, "绿色产业(光伏+养殖) 环境风险低"),
    "天合光能": (3, "绿色产业(光伏) 环境风险低"),
    "晶澳科技": (3, "绿色产业(光伏) 环境风险低"),
    "亿纬锂能": (3, "绿色产业(锂电) 环境风险低"),
    "国轩高科": (3, "绿色产业(锂电) 环境风险低"),
    "欣旺达": (3, "绿色产业(锂电) 环境风险低"),
    "孚能科技": (3, "绿色产业(锂电) 环境风险低"),
    "蔚蓝锂芯": (3, "绿色产业(锂电) 环境风险低"),
    "派能科技": (3, "绿色产业(储能) 环境风险低"),
    "德业股份": (3, "绿色产业(光伏逆变器) 环境风险低"),
    "固德威": (3, "绿色产业(光伏逆变器) 环境风险低"),
    "锦浪科技": (3, "绿色产业(光伏逆变器) 环境风险低"),
    "禾迈股份": (3, "绿色产业(光伏) 环境风险低"),
    "昱能科技": (3, "绿色产业(光伏) 环境风险低"),
    "金盘科技": (3, "绿色产业(储能) 环境风险低"),
    "南网科技": (3, "绿色产业(储能) 环境风险低"),
    # 高污染
    "宝钢股份": (0, "高污染行业(钢铁) 环境风险高"),
    "鞍钢股份": (0, "高污染行业(钢铁) 环境风险高"),
    "中国铝业": (0, "高污染行业(有色) 环境风险高"),
    "江西铜业": (0, "高污染行业(有色) 环境风险高"),
    "中国神华": (0, "高污染行业(煤炭) 环境风险高"),
    "中煤能源": (0, "高污染行业(煤炭) 环境风险高"),
    "华能国际": (0, "高污染行业(火电) 环境风险高"),
    "华电国际": (0, "高污染行业(火电) 环境风险高"),
    "中国石化": (0, "高污染行业(石化) 环境风险高"),
    "中国石油": (0, "高污染行业(石油) 环境风险高"),
    "万华化学": (0, "高污染行业(化工) 环境风险高"),
    "荣盛石化": (0, "高污染行业(石化) 环境风险高"),
    "恒力石化": (0, "高污染行业(石化) 环境风险高"),
    "海螺水泥": (0, "高污染行业(水泥) 环境风险高"),
    # 低污染 (科技/医药/金融)
    "京东方A": (1, "中等污染行业(光电显示) 环境风险中等"),
    "浪潮信息": (2, "低污染行业(信息技术) 环境风险较低"),
    "中芯国际": (1, "中等污染行业(半导体) 环境风险中等"),
    "三安光电": (1, "中等污染行业(光电) 环境风险中等"),
    "贵州茅台": (2, "低污染行业(食品饮料) 环境风险较低"),
    "五粮液": (2, "低污染行业(食品饮料) 环境风险较低"),
    "恒瑞医药": (2, "低污染行业(医药) 环境风险较低"),
    "迈瑞医疗": (2, "低污染行业(医疗器械) 环境风险较低"),
    "特锐德": (1, "中等污染行业(电气制造) 环境风险中等"),
    "云赛智联": (2, "低污染行业(智慧城市/信息技术) 环境风险较低"),
}


def _classify_env_risk(stock_name: str) -> tuple[int, str]:
    """根据股票名称推断行业环境风险等级

    返回: (风险得分 0-3, 风险描述)
    """
    # 优先使用人工映射表
    if stock_name in _STOCK_INDUSTRY_OVERRIDE:
        return _STOCK_INDUSTRY_OVERRIDE[stock_name]

    for kw in _POLLUTION_KEYWORDS:
        if kw in stock_name:
            return 0, f"高污染行业({kw}) 环境风险高"

    for kw in _GREEN_KEYWORDS:
        if kw in stock_name:
            return 3, f"绿色产业({kw}) 环境风险低"

    for industry, score in _INDUSTRY_ENV_RISK.items():
        if industry in stock_name:
            if score >= 3:
                desc = f"绿色产业({industry}) 环境风险低"
            elif score >= 2:
                desc = f"低污染行业({industry}) 环境风险较低"
            elif score >= 1:
                desc = f"中等污染行业({industry}) 环境风险中等"
            else:
                desc = f"高污染行业({industry}) 环境风险高"
            return score, desc

    # 默认中等风险
    return 1, "行业风险未知 默认中等"


async def _fetch_fundamentals(code: str) -> dict[str, Any]:
    """从新浪财经财务指标页面获取基本面数据

    返回: {"roe": float, "debt_ratio": float, "net_margin": float,
           "current_ratio": float, "quick_ratio": float, "eps": float,
           "bvps": float, "stock_name": str}
    """
    symbol = _to_sina_symbol(code)
    url = (
        f"https://money.finance.sina.com.cn/corp/go.php/"
        f"vFD_FinancialGuideLine/stockid/{code}/displaytype/4.phtml"
    )

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=SINA_HEADERS)
        if resp.status_code != 200:
            raise RuntimeError(f"财务指标页返回 HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")

    # 从 title 提取股票名称
    title_match = re.search(r"<title>(.+?)\(", text)
    stock_name = title_match.group(1) if title_match else ""

    def _extract_value(keyword: str) -> float | None:
        """提取关键词后的第一个数值"""
        idx = text.find(keyword)
        if idx < 0:
            return None
        snippet = text[idx:idx + 500]
        tds = re.findall(r"<td[^>]*>([\d.]+)</td>", snippet)
        return float(tds[0]) if tds else None

    result: dict[str, Any] = {"stock_name": stock_name}

    # 净资产收益率 ROE
    roe = _extract_value("净资产收益率")
    if roe is not None:
        result["roe"] = roe

    # 资产负债率
    debt = _extract_value("资产负债率")
    if debt is not None:
        result["debt_ratio"] = debt

    # 净利率
    margin = _extract_value("净利率")
    if margin is not None:
        result["net_margin"] = margin

    # 每股收益
    eps = _extract_value("每股收益")
    if eps is not None:
        result["eps"] = eps

    # 每股净资产
    bvps = _extract_value("每股净资产")
    if bvps is not None:
        result["bvps"] = bvps

    # 流动比率
    cr = _extract_value("流动比率")
    if cr is not None:
        result["current_ratio"] = cr

    # 速动比率
    qr = _extract_value("速动比率")
    if qr is not None:
        result["quick_ratio"] = qr

    # 主营业务利润率
    opm = _extract_value("主营业务利润率")
    if opm is not None:
        result["operating_margin"] = opm

    return result


def calc_esg(
    fundamentals: dict[str, Any],
    stock_name: str,
    closes: list[float],
    volumes: list[float],
) -> dict[str, Any]:
    """ESG 有效性评分

    三维度各满分 3 分，总分 9 分:
      E (Environmental) — 行业环境风险 + 绿色概念
      S (Social)        — 盈利能力(净利率) + 流动性健康 + 成交稳定性
      G (Governance)    — ROE + 资产负债率 + EPS趋势

    评判:
      >=6 分 → ESG 有效（正面）
      3-5 分 → 中性
      <=2 分 → 负面
    """
    e_score = 0
    s_score = 0
    g_score = 0
    e_signals: list[str] = []
    s_signals: list[str] = []
    g_signals: list[str] = []

    # ─── E: 环境维度 ───────────────────────────────
    env_score, env_desc = _classify_env_risk(stock_name)
    e_score += env_score
    e_signals.append(env_desc)

    # 额外绿色加分
    for kw in _GREEN_KEYWORDS:
        if kw in stock_name:
            e_score = min(e_score + 1, 3)
            e_signals.append(f"绿色概念({kw}) +1")
            break

    # ─── S: 社会维度 ───────────────────────────────
    # 净利率 → 盈利能力 (社会贡献)
    net_margin = fundamentals.get("net_margin")
    if net_margin is not None:
        if net_margin > 20:
            s_score += 1
            s_signals.append(f"净利率={net_margin:.1f}% 盈利能力强 社会贡献大")
        elif net_margin > 5:
            s_score += 1
            s_signals.append(f"净利率={net_margin:.1f}% 盈利能力正常")
        else:
            s_signals.append(f"净利率={net_margin:.1f}% 盈利能力弱")

    # 速动比率 → 流动性健康
    quick_ratio = fundamentals.get("quick_ratio")
    if quick_ratio is not None:
        if quick_ratio > 1.0:
            s_score += 1
            s_signals.append(f"速动比率={quick_ratio:.2f}>1.0 流动性健康")
        else:
            s_signals.append(f"速动比率={quick_ratio:.2f}<1.0 流动性偏紧")

    # 成交量稳定性 → 市场认可度
    if len(volumes) >= 20:
        recent_vol = sum(volumes[-5:]) / 5
        avg_vol = sum(volumes[-20:]) / 20
        vol_ratio = recent_vol / avg_vol if avg_vol else 1
        if 0.8 <= vol_ratio <= 2.0:
            s_score += 1
            s_signals.append(f"成交量稳定(近5日/20日={vol_ratio:.2f}) 市场认可度良好")
        elif vol_ratio > 2.0:
            s_signals.append(f"成交量放大(近5日/20日={vol_ratio:.2f}) 短期关注度激增")
        else:
            s_signals.append(f"成交量萎缩(近5日/20日={vol_ratio:.2f}) 关注度下降")

    # ─── G: 治理维度 ───────────────────────────────
    # ROE → 治理效率
    roe = fundamentals.get("roe")
    if roe is not None:
        if roe > 15:
            g_score += 1
            g_signals.append(f"ROE={roe:.1f}%>15% 治理效率优秀")
        elif roe > 8:
            g_score += 1
            g_signals.append(f"ROE={roe:.1f}%>8% 治理效率良好")
        else:
            s_signals_g = f"ROE={roe:.1f}%<8% 治理效率偏低"
            g_signals.append(s_signals_g)

    # 资产负债率 → 财务纪律
    debt_ratio = fundamentals.get("debt_ratio")
    if debt_ratio is not None:
        if debt_ratio < 40:
            g_score += 1
            g_signals.append(f"资产负债率={debt_ratio:.1f}%<40% 财务稳健")
        elif debt_ratio < 60:
            g_score += 1
            g_signals.append(f"资产负债率={debt_ratio:.1f}%<60% 财务可控")
        else:
            g_signals.append(f"资产负债率={debt_ratio:.1f}%>60% 杠杆偏高")

    # 每股收益趋势 (用近4季度是否为正)
    eps = fundamentals.get("eps")
    if eps is not None:
        if eps > 0:
            g_score += 1
            g_signals.append(f"EPS={eps}>0 盈利为正")
        else:
            g_signals.append(f"EPS={eps} 亏损")

    # ─── 汇总 ─────────────────────────────────────
    total = e_score + s_score + g_score
    if total >= 6:
        verdict = "ESG有效（正面）"
        verdict_level = "positive"
    elif total >= 3:
        verdict = "ESG中性"
        verdict_level = "neutral"
    else:
        verdict = "ESG负面"
        verdict_level = "negative"

    dimensions = [
        {"name": "E (环境)", "score": e_score, "max": 3, "signals": e_signals},
        {"name": "S (社会)", "score": s_score, "max": 3, "signals": s_signals},
        {"name": "G (治理)", "score": g_score, "max": 3, "signals": g_signals},
    ]

    return {
        "name": "ESG有效性",
        "dimensions": dimensions,
        "e_score": e_score,
        "s_score": s_score,
        "g_score": g_score,
        "total_score": total,
        "max_score": 9,
        "verdict": verdict,
        "verdict_level": verdict_level,
        "fundamentals": {k: v for k, v in fundamentals.items() if k != "stock_name"},
        "stock_name": stock_name,
    }


# ─── 辅助 ───────────────────────────────────────────────

def _insufficient_data(name: str) -> dict:
    return {
        "name": name,
        "values": {},
        "score": 0,
        "is_bullish": False,
        "signals": ["数据不足，无法计算"],
    }


def _ma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


# ════════════════════════════════════════════════════════
#  7 因子综合评判
# ════════════════════════════════════════════════════════

def analyze_momentum_7factors(
    dates: list[str],
    opens: list[float],
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> dict[str, Any]:
    """执行 7 因子趋势/动量分析，返回结构化报告"""

    # 逐个计算
    macd_r = calc_macd(closes)
    roc_r = calc_roc(closes, period=12)
    wr_r = calc_williams_r(highs, lows, closes, period=14)
    rsi_r = calc_rsi(closes, period=14)
    kdj_r = calc_kdj(highs, lows, closes, n=9)
    cci_r = calc_cci(highs, lows, closes, period=20)
    dmi_r = calc_dmi(highs, lows, closes, period=14)

    factors = [macd_r, roc_r, wr_r, rsi_r, kdj_r, cci_r, dmi_r]

    # 统计
    bullish_count = sum(1 for f in factors if f["is_bullish"])
    total_score = sum(f["score"] for f in factors)
    max_score = sum(max(f["score"], 1) for f in factors if f["signals"] != ["数据不足，无法计算"])

    # 总体评判
    if bullish_count >= 5:
        verdict = "偏多（强信号）"
        verdict_level = "bullish_strong"
    elif bullish_count >= 3:
        verdict = "中性偏多"
        verdict_level = "bullish_weak"
    elif bullish_count >= 2:
        verdict = "中性"
        verdict_level = "neutral"
    else:
        verdict = "偏空"
        verdict_level = "bearish"

    # 均线辅助
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)

    last_close = closes[-1]
    last_date = dates[-1] if dates else ""

    # 近期走势
    recent_5_pct = ((last_close / closes[-6] - 1) * 100) if len(closes) >= 6 else 0
    recent_20_pct = ((last_close / closes[-21] - 1) * 100) if len(closes) >= 21 else 0

    return {
        "date": last_date,
        "close": round(last_close, 2),
        "ma": {
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
        },
        "recent": {
            "5d_pct": round(recent_5_pct, 2),
            "20d_pct": round(recent_20_pct, 2),
            "120d_high": round(max(closes), 2),
            "120d_low": round(min(closes), 2),
        },
        "factors": factors,
        "summary": {
            "bullish_count": bullish_count,
            "total_factors": 7,
            "total_score": total_score,
            "verdict": verdict,
            "verdict_level": verdict_level,
        },
    }


# ════════════════════════════════════════════════════════
#  工具执行入口
# ════════════════════════════════════════════════════════

def _extract_stock_code(text: str) -> str:
    """从用户输入中提取6位股票代码，支持 '名称 代码' 格式"""
    text = text.strip()
    # 尝试匹配6位数字代码
    match = re.search(r'\b(\d{6})\b', text)
    if match:
        return match.group(1)
    # 如果没有6位数字，尝试匹配 sh/sz 前缀的代码
    match = re.search(r'\b(sh|sz)(\d{6})\b', text, re.IGNORECASE)
    if match:
        return match.group(1).lower() + match.group(2)
    return text


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """执行股票技术分析 + ESG 有效性评判"""
    raw_code = args.get("code", "").strip()
    if not raw_code:
        return {"error": "请提供股票代码"}

    code = _extract_stock_code(raw_code)
    if not code:
        return {"error": f"无法从 '{raw_code}' 中提取有效的6位股票代码"}

    limit = args.get("limit", 120)

    try:
        kdata = await _fetch_klines(code, limit=limit)
    except Exception as e:
        return {"error": f"获取K线数据失败: {e}"}

    if len(kdata["closes"]) < 30:
        return {"error": f"K线数据不足（仅 {len(kdata['closes'])} 条），至少需要 30 条"}

    # 7 因子技术分析
    report = analyze_momentum_7factors(
        kdata["dates"], kdata["opens"], kdata["closes"],
        kdata["highs"], kdata["lows"], kdata["volumes"],
    )

    report["code"] = code
    report["kline_count"] = len(kdata["closes"])

    # ESG 有效性分析
    try:
        fundamentals = await _fetch_fundamentals(code)
        stock_name = fundamentals.get("stock_name", "")
        esg = calc_esg(
            fundamentals, stock_name,
            kdata["closes"], kdata["volumes"],
        )
        report["esg"] = esg
        report["stock_name"] = stock_name
    except Exception as e:
        report["esg_error"] = f"ESG数据获取失败: {e}"

    return report


# ─── 格式化输出 ─────────────────────────────────────────

def format_report(report: dict[str, Any]) -> str:
    """将分析报告格式化为可读文本"""
    lines = []
    stock_name = report.get("stock_name", "")
    title = f"{report.get('code', '?')}"
    if stock_name:
        title += f" {stock_name}"
    title += " 技术分析 + ESG 有效性评判"

    lines.append("=" * 70)
    lines.append(f"  {title}")
    lines.append(f"  日期: {report.get('date', '?')}  收盘: {report.get('close', '?')}")
    lines.append("=" * 70)

    # 均线
    ma = report.get("ma", {})
    lines.append(f"\n【均线系统】")
    lines.append(f"  MA5: {ma.get('ma5', '-')}  MA10: {ma.get('ma10', '-')}")
    lines.append(f"  MA20: {ma.get('ma20', '-')}  MA60: {ma.get('ma60', '-')}")

    # 近期走势
    recent = report.get("recent", {})
    lines.append(f"\n【近期走势】")
    lines.append(f"  近5日: {recent.get('5d_pct', 0):+.2f}%  近20日: {recent.get('20d_pct', 0):+.2f}%")
    lines.append(f"  120日高: {recent.get('120d_high', '-')}  120日低: {recent.get('120d_low', '-')}")

    # 7 因子
    lines.append(f"\n{'─' * 70}")
    lines.append(f"  技术面 7 因子分析 (趋势/动量)")
    lines.append(f"{'─' * 70}")

    for f in report.get("factors", []):
        icon = "[多]" if f["is_bullish"] else "[空]"
        lines.append(f"\n  {icon} {f['name']}  (得分: {f['score']})")
        vals = "  ".join(f"{k}={v}" for k, v in f.get("values", {}).items())
        if vals:
            lines.append(f"    {vals}")
        for s in f.get("signals", []):
            lines.append(f"    → {s}")

    # 技术面汇总
    lines.append(f"\n{'═' * 70}")
    lines.append(f"  技术面综合评判")
    lines.append(f"{'═' * 70}")
    s = report.get("summary", {})
    lines.append(f"  偏多因子: {s.get('bullish_count', 0)} / 7")
    lines.append(f"  总得分: {s.get('total_score', 0)}")
    lines.append(f"  评判: {s.get('verdict', '?')}")

    # ESG 有效性
    esg = report.get("esg")
    if esg:
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  ESG 有效性分析 (基本面)")
        lines.append(f"{'─' * 70}")

        # 基本面数据
        fund = esg.get("fundamentals", {})
        if fund:
            fund_parts = []
            for k, v in fund.items():
                if isinstance(v, float):
                    fund_parts.append(f"{k}={v:.2f}")
                else:
                    fund_parts.append(f"{k}={v}")
            lines.append(f"  基本面: {'  '.join(fund_parts)}")

        for dim in esg.get("dimensions", []):
            lines.append(f"\n  {dim['name']}  得分: {dim['score']}/{dim['max']}")
            for sig in dim.get("signals", []):
                lines.append(f"    → {sig}")

        lines.append(f"\n  ESG 总分: {esg.get('total_score', 0)}/{esg.get('max_score', 9)}")
        lines.append(f"  ESG 评判: {esg.get('verdict', '?')}")
    elif "esg_error" in report:
        lines.append(f"\n  [ESG] {report['esg_error']}")

    # 综合结论
    lines.append(f"\n{'═' * 70}")
    lines.append(f"  综合结论")
    lines.append(f"{'═' * 70}")
    tech_verdict = s.get("verdict", "?")
    if esg:
        esg_verdict = esg.get("verdict", "?")
        esg_level = esg.get("verdict_level", "")
        # 技术面 + ESG 综合判断
        if "强信号" in tech_verdict and esg_level == "positive":
            conclusion = "技术面+ESG双重利好，强势确认"
        elif "强信号" in tech_verdict and esg_level == "negative":
            conclusion = "技术面强但ESG存疑，需警惕基本面风险"
        elif "偏空" in tech_verdict and esg_level == "positive":
            conclusion = "技术面偏空但ESG正面，可能超跌待反弹"
        elif "偏空" in tech_verdict and esg_level == "negative":
            conclusion = "技术面+ESG双重偏空，规避"
        else:
            conclusion = "技术面与ESG信号混合，中性观望"
        lines.append(f"  技术面: {tech_verdict}")
        lines.append(f"  ESG:    {esg_verdict}")
        lines.append(f"  → {conclusion}")
    else:
        lines.append(f"  技术面: {tech_verdict}")

    lines.append(f"\n  [注意] 以上仅为技术面+基本面分析，不构成投资建议。")

    return "\n".join(lines)


# ─── 注册 ───────────────────────────────────────────────

def register():
    tool_registry.register(ChatToolDefinition(
        name="stock_analysis",
        description=(
            "股票技术分析：MACD/ROC/Williams%R/RSI/KDJ/CCI/DMI七因子+ESG评分。"
            "数据来自新浪财经。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "limit": {"type": "integer", "default": 120},
            },
            "required": ["code"],
        },
        execute=execute,
        format_input=lambda args: f"技术分析: {args.get('code', '')}",
        format_output=lambda result: format_report(result) if isinstance(result, dict) and "factors" in result else json.dumps(result, ensure_ascii=False, indent=2),
        result_is_authoritative=False,
        planning_category="information",
        decision_weight=0.9,
        keywords=["股票", "技术分析", "MACD", "ROC", "Williams", "RSI", "KDJ", "CCI", "DMI", "ADX", "趋势", "动量", "指标", "stock", "analysis", "technical", "ESG", "环境", "社会", "治理", "基本面"],
    ))
