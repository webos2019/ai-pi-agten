"""通用股票分析脚本 — python _run_analysis.py 000725"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.stock_analysis import execute, format_report


async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "000725"
    print(f"正在分析 {code} ...")
    report = await execute({"code": code}, {})
    if "error" in report:
        print(f"错误: {report['error']}")
        return
    print(format_report(report))


if __name__ == "__main__":
    asyncio.run(main())
