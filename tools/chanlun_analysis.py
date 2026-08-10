"""缠论技术面分析工具 — 分型/笔/线段/中枢/背驰

缠论核心概念实现:
  1. 分型 (Fractal)     — 顶分型/底分型
  2. 笔 (Stroke)        — 相邻顶底分型连接
  3. 线段 (LineSegment) — 至少3笔构成
  4. 中枢 (CentralPivot)— 至少3次连续重叠区间
  5. 背驰 (Divergence)  — 趋势力度减弱信号
  6. 三类买卖点          — 基于中枢和背驰的定位

数据来源: 新浪财经 K线
"""

import json
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition
from .stock_analysis import SINA_HEADERS, _to_sina_symbol


# ════════════════════════════════════════════════════════
#  1. 分型 (Fractal)
# ════════════════════════════════════════════════════════

def find_fractals(
    highs: list[float], lows: list[float], dates: list[str]
) -> list[dict[str, Any]]:
    """识别顶分型和底分型

    顶分型: 连续3根K线, 中间那根高点最高, 且低点也高于左右两根的低点
    底分型: 连续3根K线, 中间那根低点最低, 且高点也低于左右两根的高点

    返回: [{"index": int, "date": str, "type": "top"/"bottom",
            "high": float, "low": float, "close": float}, ...]
    """
    fractals: list[dict[str, Any]] = []
    n = len(highs)
    if n < 3:
        return fractals

    for i in range(1, n - 1):
        # 顶分型
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            if lows[i] > lows[i - 1] and lows[i] > lows[i + 1]:
                fractals.append({
                    "index": i,
                    "date": dates[i],
                    "type": "top",
                    "high": round(highs[i], 3),
                    "low": round(lows[i], 3),
                })
        # 底分型
        elif lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            if highs[i] < highs[i - 1] and highs[i] < highs[i + 1]:
                fractals.append({
                    "index": i,
                    "date": dates[i],
                    "type": "bottom",
                    "high": round(highs[i], 3),
                    "low": round(lows[i], 3),
                })

    return fractals


# ════════════════════════════════════════════════════════
#  2. 笔 (Stroke)
# ════════════════════════════════════════════════════════

def build_strokes(fractals: list[dict], closes: list[float], min_klines: int = 5) -> list[dict[str, Any]]:
    """从分型构建笔

    规则:
      - 顶分型后必须接底分型, 底分型后必须接顶分型
      - 相邻同向分型取极值 (顶取最高, 底取最低)
      - 笔内至少包含 min_klines 根K线 (不含分型本身)
      - 向上笔: 底分型 -> 顶分型 (价格上升)
      - 向下笔: 顶分型 -> 底分型 (价格下降)

    返回: [{"start_idx", "end_idx", "start_date", "end_date",
            "type": "up"/"down", "high", "low", "start_price", "end_price"}, ...]
    """
    if len(fractals) < 2:
        return []

    # 合并相邻同向分型, 取极值
    merged: list[dict] = [fractals[0]]
    for f in fractals[1:]:
        last = merged[-1]
        if f["type"] == last["type"]:
            # 同向合并
            if f["type"] == "top":
                if f["high"] > last["high"]:
                    merged[-1] = f
            else:
                if f["low"] < last["low"]:
                    merged[-1] = f
        else:
            merged.append(f)

    # 构建笔: 顶底交替
    strokes: list[dict[str, Any]] = []
    i = 0
    while i < len(merged) - 1:
        curr = merged[i]
        next_f = merged[i + 1]

        # 必须顶接底, 底接顶
        if curr["type"] == next_f["type"]:
            i += 1
            continue

        # 检查K线数量
        kline_count = next_f["index"] - curr["index"] - 1
        if kline_count < min_klines:
            i += 1
            continue

        # 笔的方向由起点分型决定:
        # 底分型 -> 顶分型 = 向上笔 (价格从低点走向高点)
        # 顶分型 -> 底分型 = 向下笔 (价格从高点走向低点)
        if curr["type"] == "bottom" and next_f["type"] == "top":
            stroke_type = "up"
        elif curr["type"] == "top" and next_f["type"] == "bottom":
            stroke_type = "down"
        else:
            i += 1
            continue

        # 过滤: 向上笔的终点必须高于起点, 向下笔的终点必须低于起点
        # (否则说明不是有效的笔, 可能是包含关系未处理干净)
        start_price = closes[curr["index"]]
        end_price = closes[next_f["index"]]
        if stroke_type == "up" and end_price <= start_price * 1.001:
            # 允许极小的向下偏差
            pass  # 仍然接受, 因为缠论中笔的定义基于分型, 不完全依赖收盘价
        if stroke_type == "down" and end_price >= start_price * 0.999:
            pass

        strokes.append({
            "start_idx": curr["index"],
            "end_idx": next_f["index"],
            "start_date": curr["date"],
            "end_date": next_f["date"],
            "type": stroke_type,
            "high": round(max(curr["high"], next_f["high"]), 3),
            "low": round(min(curr["low"], next_f["low"]), 3),
            "start_price": round(start_price, 3),
            "end_price": round(end_price, 3),
            "kline_count": kline_count,
        })
        i += 1

    return strokes


# ════════════════════════════════════════════════════════
#  3. 线段 (Line Segment)
# ════════════════════════════════════════════════════════

def build_segments(strokes: list[dict]) -> list[dict[str, Any]]:
    """从笔构建线段

    规则: 至少3笔构成一个线段
      - 向上线段: 底开始, 顶结束, 中间有向上笔 (down-up-down 笔序列)
      - 向下线段: 顶开始, 底结束, 中间有向下笔 (up-down-up 笔序列)

    关键: 线段由3笔构成, 第1笔和第3笔同向, 第2笔反向
         向上线段 = down笔 + up笔 + down笔 (底-顶-底, 整体向上)
         向下线段 = up笔 + down笔 + up笔 (顶-底-顶, 整体向下)
    """
    if len(strokes) < 3:
        return []

    segments: list[dict[str, Any]] = []
    i = 0
    while i < len(strokes) - 2:
        s1, s2, s3 = strokes[i], strokes[i + 1], strokes[i + 2]

        # 线段: 3笔方向交替 (up-down-up 或 down-up-down)
        if s1["type"] != s2["type"] and s2["type"] != s3["type"]:
            # 判断线段方向: 看整体趋势
            # 如果 s1.start < s3.end 且是 down-up-down, 则为向上线段
            # 如果 s1.start > s3.end 且是 up-down-up, 则为向下线段
            overall_up = s3["end_price"] > s1["start_price"]
            seg_type = "up" if overall_up else "down"

            seg_high = max(s1["high"], s2["high"], s3["high"])
            seg_low = min(s1["low"], s2["low"], s3["low"])

            segments.append({
                "start_idx": s1["start_idx"],
                "end_idx": s3["end_idx"],
                "start_date": s1["start_date"],
                "end_date": s3["end_date"],
                "type": seg_type,
                "high": round(seg_high, 3),
                "low": round(seg_low, 3),
                "stroke_count": 3,
                "start_price": round(s1["start_price"], 3),
                "end_price": round(s3["end_price"], 3),
            })
            i += 3
        else:
            i += 1

    return segments


# ════════════════════════════════════════════════════════
#  4. 中枢 (Central Pivot)
# ════════════════════════════════════════════════════════

def find_pivots(segments: list[dict]) -> list[dict[str, Any]]:
    """识别中枢

    定义: 某级别走势类型中, 被至少3次连续重叠的价格区间
    简化: 从线段中找连续3个线段的 price overlap

    返回: [{"start_idx", "end_idx", "start_date", "end_date",
            "upper": float, "lower": float, "type": "up"/"down"}, ...]
    """
    if len(segments) < 3:
        return []

    pivots: list[dict[str, Any]] = []
    i = 0
    while i < len(segments) - 2:
        s1, s2, s3 = segments[i], segments[i + 1], segments[i + 2]

        # 找三段的 price overlap
        upper = min(s1["high"], s2["high"], s3["high"])
        lower = max(s1["low"], s2["low"], s3["low"])

        if upper > lower:
            # 有重叠区间, 构成中枢
            pivots.append({
                "start_idx": s1["start_idx"],
                "end_idx": s3["end_idx"],
                "start_date": s1["start_date"],
                "end_date": s3["end_date"],
                "upper": round(upper, 3),
                "lower": round(lower, 3),
                "mid": round((upper + lower) / 2, 3),
                "height": round(upper - lower, 3),
                "type": s2["type"],  # 中枢方向取中间段
            })
            i += 2
        else:
            i += 1

    return pivots


# ════════════════════════════════════════════════════════
#  5. 背驰 (Divergence)
# ════════════════════════════════════════════════════════

def find_divergences(
    segments: list[dict],
    closes: list[float],
    volumes: list[float],
) -> list[dict[str, Any]]:
    """识别背驰

    趋势背驰: 两个同向线段, 后一段力度弱于前一段
    力度比较:
      - 价格幅度: |end - start|
      - MACD面积 (简化: 用成交量加权)
      - 斜率

    返回: [{"type": "trend"/"盘整", "direction": "up"/"down",
            "seg1_idx", "seg2_idx", "strength_ratio", "signal"}, ...]
    """
    if len(segments) < 4:
        return []

    divergences: list[dict[str, Any]] = []

    for i in range(len(segments) - 3):
        # 找 a-A-b 结构: 两段同向趋势中间夹一个反向段
        s1 = segments[i]      # 第一段趋势
        s2 = segments[i + 1]  # 反向段 (中枢组成部分)
        s3 = segments[i + 2]  # 第二段趋势

        if s1["type"] != s3["type"]:
            continue

        direction = s1["type"]

        # 计算力度 (简化: 价格变动幅度 / K线数量)
        s1_range = abs(s1["end_price"] - s1["start_price"])
        s3_range = abs(s3["end_price"] - s3["start_price"])

        s1_length = s1["end_idx"] - s1["start_idx"]
        s3_length = s3["end_idx"] - s3["start_idx"]

        s1_strength = s1_range / max(s1_length, 1)
        s3_strength = s3_range / max(s3_length, 1)

        # 背驰判断: 后一段力度明显弱于前一段
        if s3_strength < s1_strength * 0.7 and s3_range < s1_range:
            ratio = round(s3_strength / s1_strength, 3) if s1_strength > 0 else 0

            # 额外检查: 成交量是否也萎缩
            vol1 = sum(volumes[s1["start_idx"]:s1["end_idx"]+1]) / max(s1_length, 1)
            vol3 = sum(volumes[s3["start_idx"]:s3["end_idx"]+1]) / max(s3_length, 1)
            vol_shrink = vol3 < vol1 * 0.8

            signal = "趋势背驰"
            if direction == "up":
                signal += "(上涨乏力)"
            else:
                signal += "(下跌衰竭)"

            if vol_shrink:
                signal += "+成交量萎缩确认"

            divergences.append({
                "type": "trend",
                "direction": direction,
                "seg1_idx": i,
                "seg2_idx": i + 2,
                "seg1_strength": round(s1_strength, 4),
                "seg2_strength": round(s3_strength, 4),
                "strength_ratio": ratio,
                "vol_shrink": vol_shrink,
                "signal": signal,
                "start_date": s3["start_date"],
                "end_date": s3["end_date"],
            })

    return divergences


# ════════════════════════════════════════════════════════
#  6. 三类买卖点
# ════════════════════════════════════════════════════════

def find_buy_sell_points(
    segments: list[dict],
    pivots: list[dict],
    divergences: list[dict],
    closes: list[float],
    dates: list[str],
) -> list[dict[str, Any]]:
    """识别三类买卖点

    第一类买点: 趋势背驰后的反转点 (底背驰)
    第一类卖点: 趋势背驰后的反转点 (顶背驰)

    第二类买卖点: 第一类后回抽不创新低/新高

    第三类买卖点: 中枢破坏后回抽不回到中枢

    简化版: 主要识别第一类买卖点 (背驰点)
    """
    points: list[dict[str, Any]] = []

    for div in divergences:
        if div["direction"] == "down":
            # 下跌背驰 = 第一类买点
            idx = segments[div["seg2_idx"]]["end_idx"]
            points.append({
                "type": "第一类买点",
                "category": "buy",
                "level": 1,
                "date": div["end_date"],
                "index": idx,
                "price": round(closes[idx], 3),
                "signal": div["signal"],
                "description": "下跌趋势背驰, 力度衰竭, 可能反转向上",
            })
        else:
            # 上涨背驰 = 第一类卖点
            idx = segments[div["seg2_idx"]]["end_idx"]
            points.append({
                "type": "第一类卖点",
                "category": "sell",
                "level": 1,
                "date": div["end_date"],
                "index": idx,
                "price": round(closes[idx], 3),
                "signal": div["signal"],
                "description": "上涨趋势背驰, 力度衰竭, 可能反转向下",
            })

    # 第二类买卖点: 在第一类之后, 回抽不创新低/新高
    for p in points:
        if p["level"] == 1:
            idx = p["index"]
            # 向后找5-15根K线内的极值
            look_end = min(idx + 15, len(closes) - 1)
            if p["category"] == "buy":
                # 找回调低点, 不创新低
                min_price = min(closes[idx:look_end + 1])
                min_idx = closes[idx:look_end + 1].index(min_price) + idx
                if min_price >= p["price"] * 0.98:
                    points.append({
                        "type": "第二类买点",
                        "category": "buy",
                        "level": 2,
                        "date": dates[min_idx],
                        "index": min_idx,
                        "price": round(min_price, 3),
                        "signal": "回抽不创新低",
                        "description": "第一类买点后回抽确认, 不创新低",
                    })
            else:
                # 找反弹高点, 不创新高
                max_price = max(closes[idx:look_end + 1])
                max_idx = closes[idx:look_end + 1].index(max_price) + idx
                if max_price <= p["price"] * 1.02:
                    points.append({
                        "type": "第二类卖点",
                        "category": "sell",
                        "level": 2,
                        "date": dates[max_idx],
                        "index": max_idx,
                        "price": round(max_price, 3),
                        "signal": "回抽不创新高",
                        "description": "第一类卖点后回抽确认, 不创新高",
                    })

    # 去重并排序
    seen = set()
    unique: list[dict] = []
    for p in points:
        key = (p["date"], p["type"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    unique.sort(key=lambda x: x["index"])
    return unique


# ════════════════════════════════════════════════════════
#  主分析流程
# ════════════════════════════════════════════════════════

async def _fetch_klines_chanlun(code: str, limit: int = 120) -> dict[str, list]:
    """获取K线数据"""
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

    return {
        "dates": [k.get("day", "") for k in klines],
        "opens": [float(k.get("open", 0)) for k in klines],
        "closes": [float(k.get("close", 0)) for k in klines],
        "highs": [float(k.get("high", 0)) for k in klines],
        "lows": [float(k.get("low", 0)) for k in klines],
        "volumes": [float(k.get("volume", 0)) for k in klines],
    }


async def analyze_chanlun(code: str, limit: int = 120) -> dict[str, Any]:
    """缠论完整分析

    返回完整分析结果, 包含:
      - klines: K线数据
      - fractals: 分型列表
      - strokes: 笔列表
      - segments: 线段列表
      - pivots: 中枢列表
      - divergences: 背驰列表
      - points: 买卖点列表
      - summary: 分析摘要
    """
    data = await _fetch_klines_chanlun(code, limit)
    dates = data["dates"]
    highs = data["highs"]
    lows = data["lows"]
    closes = data["closes"]
    volumes = data["volumes"]

    # 1. 分型
    fractals = find_fractals(highs, lows, dates)

    # 2. 笔
    strokes = build_strokes(fractals, closes, min_klines=5)

    # 3. 线段
    segments = build_segments(strokes)

    # 4. 中枢
    pivots = find_pivots(segments)

    # 5. 背驰
    divergences = find_divergences(segments, closes, volumes)

    # 6. 买卖点
    points = find_buy_sell_points(segments, pivots, divergences, closes, dates)

    # 7. 生成摘要
    summary = _build_summary(
        code, data, fractals, strokes, segments, pivots, divergences, points
    )

    return {
        "code": code,
        "klines": {
            "dates": dates,
            "opens": [round(o, 3) for o in data["opens"]],
            "closes": [round(c, 3) for c in closes],
            "highs": [round(h, 3) for h in highs],
            "lows": [round(l, 3) for l in lows],
            "volumes": [round(v, 0) for v in volumes],
        },
        "fractals": fractals,
        "strokes": strokes,
        "segments": segments,
        "pivots": pivots,
        "divergences": divergences,
        "points": points,
        "summary": summary,
    }


def _build_summary(
    code: str,
    data: dict,
    fractals: list,
    strokes: list,
    segments: list,
    pivots: list,
    divergences: list,
    points: list,
) -> dict[str, Any]:
    """生成分析摘要"""
    closes = data["closes"]
    dates = data["dates"]
    latest_price = closes[-1] if closes else 0
    latest_date = dates[-1] if dates else ""

    # 趋势判断
    trend = "未知"
    if segments:
        last_seg = segments[-1]
        if last_seg["type"] == "up":
            trend = "上升趋势"
        else:
            trend = "下降趋势"

    # 当前位置判断
    position = "未知"
    if pivots:
        last_pivot = pivots[-1]
        if latest_price > last_pivot["upper"]:
            position = "中枢上方(强势)"
        elif latest_price < last_pivot["lower"]:
            position = "中枢下方(弱势)"
        else:
            position = "中枢区间内(震荡)"

    # 最新信号
    latest_signal = "无明确信号"
    if points:
        recent = [p for p in points if dates.index(p["date"]) >= len(dates) - 10]
        if recent:
            latest_signal = recent[-1]["type"]

    # 背驰状态
    div_status = "无背驰"
    if divergences:
        recent_div = [d for d in divergences
                      if dates.index(d["end_date"]) >= len(dates) - 15]
        if recent_div:
            div = recent_div[-1]
            div_status = f"{div['signal']} (力度比{div['strength_ratio']})"

    return {
        "code": code,
        "latest_price": round(latest_price, 3),
        "latest_date": latest_date,
        "trend": trend,
        "position": position,
        "latest_signal": latest_signal,
        "divergence_status": div_status,
        "fractal_count": len(fractals),
        "stroke_count": len(strokes),
        "segment_count": len(segments),
        "pivot_count": len(pivots),
        "divergence_count": len(divergences),
        "point_count": len(points),
        "buy_points": [p for p in points if p["category"] == "buy"],
        "sell_points": [p for p in points if p["category"] == "sell"],
    }


def format_chanlun_report(result: dict[str, Any]) -> str:
    """格式化缠论分析报告为文本"""
    s = result["summary"]
    lines = [
        "=" * 60,
        f"  缠论分析报告 — {result['code']}",
        "=" * 60,
        "",
        f"【最新行情】{s['latest_date']} 收盘价: {s['latest_price']}",
        f"【趋势判断】{s['trend']}",
        f"【当前位置】{s['position']}",
        f"【背驰状态】{s['divergence_status']}",
        f"【最新信号】{s['latest_signal']}",
        "",
        f"【结构统计】",
        f"  分型: {s['fractal_count']} 个",
        f"  笔: {s['stroke_count']} 笔",
        f"  线段: {s['segment_count']} 段",
        f"  中枢: {s['pivot_count']} 个",
        f"  背驰: {s['divergence_count']} 处",
        f"  买卖点: {s['point_count']} 个",
        "",
    ]

    if result["pivots"]:
        lines.append("【中枢详情】")
        for i, p in enumerate(result["pivots"][-3:], 1):
            lines.append(
                f"  中枢{i}: {p['start_date']}~{p['end_date']} "
                f"区间[{p['lower']}, {p['upper']}] 中轴{p['mid']}"
            )
        lines.append("")

    if s["buy_points"]:
        lines.append("【买点信号】")
        for p in s["buy_points"][-3:]:
            lines.append(
                f"  {p['type']} — {p['date']} @ {p['price']} | {p['signal']}"
            )
        lines.append("")

    if s["sell_points"]:
        lines.append("【卖点信号】")
        for p in s["sell_points"][-3:]:
            lines.append(
                f"  {p['type']} — {p['date']} @ {p['price']} | {p['signal']}"
            )
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


# ════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════

def register() -> ChatToolDefinition:
    return ChatToolDefinition(
        name="chanlun_analysis",
        description=(
            "缠论技术面分析：自动识别分型/笔/线段/中枢/背驰/买卖点。"
            "输入股票代码返回完整缠论结构分析。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "limit": {"type": "integer", "default": 120},
            },
            "required": ["code"],
        },
        execute=lambda params: asyncio.run(_execute_tool(params)),
        format_output=format_chanlun_report,
    )


async def _execute_tool(params: dict[str, Any]) -> dict[str, Any]:
    code = params.get("code", "")
    limit = params.get("limit", 120)
    return await analyze_chanlun(code, limit)


# 注册
tool_registry.register(register())
