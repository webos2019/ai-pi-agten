"""7 因子技术分析 — 单元测试 (模拟数据)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.stock_analysis import (
    calc_macd, calc_roc, calc_williams_r, calc_rsi,
    calc_kdj, calc_cci, calc_dmi, analyze_momentum_7factors,
    format_report,
)


def test_uptrend():
    """上涨趋势: 7 因子应大部分偏多"""
    closes = [10.0 + i * 0.2 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = [c - 0.1 for c in closes]
    volumes = [10000.0] * 30
    dates = [f"2024-01-{i+1:02d}" for i in range(30)]

    factors = [
        calc_macd(closes),
        calc_roc(closes, 12),
        calc_williams_r(highs, lows, closes, 14),
        calc_rsi(closes, 14),
        calc_kdj(highs, lows, closes, 9),
        calc_cci(highs, lows, closes, 20),
        calc_dmi(highs, lows, closes, 14),
    ]

    print("=" * 60)
    print("  上涨趋势测试 (30天连续上涨)")
    print("=" * 60)
    for f in factors:
        icon = "BULL" if f["is_bullish"] else "BEAR"
        print(f"  {f['name']:15s}  score={f['score']:2d}  {icon}")
        for s in f["signals"]:
            print(f"    -> {s}")

    report = analyze_momentum_7factors(dates, opens, closes, highs, lows, volumes)
    s = report["summary"]
    print(f"\n  综合: {s['bullish_count']}/7 偏多, 总分={s['total_score']}")
    print(f"  评判: {s['verdict']}")

    assert s["bullish_count"] >= 5, f"上涨趋势应>=5偏多, 实际{s['bullish_count']}"
    print("\n  PASSED\n")


def test_downtrend():
    """下跌趋势: 7 因子应大部分偏空"""
    closes = [20.0 - i * 0.2 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = [c + 0.1 for c in closes]
    volumes = [10000.0] * 30
    dates = [f"2024-01-{i+1:02d}" for i in range(30)]

    report = analyze_momentum_7factors(dates, opens, closes, highs, lows, volumes)
    s = report["summary"]

    print("=" * 60)
    print("  下跌趋势测试 (30天连续下跌)")
    print("=" * 60)
    for f in report["factors"]:
        icon = "BULL" if f["is_bullish"] else "BEAR"
        print(f"  {f['name']:15s}  score={f['score']:2d}  {icon}")

    print(f"\n  综合: {s['bullish_count']}/7 偏多, 总分={s['total_score']}")
    print(f"  评判: {s['verdict']}")

    assert s["bullish_count"] <= 3, f"下跌趋势应<=3偏多, 实际{s['bullish_count']}"
    print("\n  PASSED\n")


def test_format_report():
    """格式化报告测试"""
    closes = [10.0 + i * 0.2 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = [c - 0.1 for c in closes]
    volumes = [10000.0] * 30
    dates = [f"2024-01-{i+1:02d}" for i in range(30)]

    report = analyze_momentum_7factors(dates, opens, closes, highs, lows, volumes)
    report["code"] = "000725"

    text = format_report(report)
    assert "7 因子" in text
    assert "综合评判" in text
    print("=" * 60)
    print("  格式化报告测试")
    print("=" * 60)
    print(text)
    print("\n  PASSED\n")


def test_insufficient_data():
    """数据不足测试"""
    closes = [10.0, 10.5, 11.0]
    r = calc_macd(closes)
    # MACD 不检查数据不足, 但 ROC/RSI 等会
    r2 = calc_roc(closes, 12)
    assert "数据不足" in r2["signals"][0], f"应返回数据不足, 实际{r2['signals']}"
    print("=" * 60)
    print("  数据不足测试")
    print("=" * 60)
    print(f"  ROC (3条数据): {r2['signals'][0]}")
    print("  PASSED\n")


if __name__ == "__main__":
    test_uptrend()
    test_downtrend()
    test_format_report()
    test_insufficient_data()
    print("=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)
