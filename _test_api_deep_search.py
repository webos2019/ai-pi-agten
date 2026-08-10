#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通过 API 测试深度搜索"""
import sys
import json
import httpx
import asyncio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def test_chat():
    """测试 /api/chat 端到端"""
    print("=" * 60)
    print("测试: /api/chat 端到端 — '什么是量子计算'")
    print("=" * 60)

    body = {
        "messages": [{"role": "user", "content": "什么是量子计算"}],
        "sessionId": "test_deep_e2e_001",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "http://localhost:8000/api/chat",
            json=body,
        )

        print(f"状态码: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('content-type', '')}")
        print()

        # 解析 NDJSON
        lines = resp.text.strip().split("\n")
        print(f"收到 {len(lines)} 行 NDJSON")
        print()

        for i, line in enumerate(lines):
            try:
                chunk = json.loads(line)
                chunk_type = chunk.get("type", "unknown")

                if chunk_type == "text":
                    text = chunk.get("text", "")
                    print(f"[{i}] text: {text[:200]}")
                elif chunk_type == "tool_call":
                    name = chunk.get("tool_name", "")
                    args = chunk.get("arguments", {})
                    print(f"[{i}] tool_call: {name}({json.dumps(args, ensure_ascii=False)[:200]})")
                elif chunk_type == "tool_result":
                    name = chunk.get("tool_name", "")
                    result = chunk.get("result", "")
                    if isinstance(result, str) and len(result) > 300:
                        result = result[:300] + "..."
                    print(f"[{i}] tool_result ({name}): {result}")
                elif chunk_type == "usage":
                    print(f"[{i}] usage: {chunk}")
                elif chunk_type == "error":
                    print(f"[{i}] ERROR: {chunk.get('error', '')}")
                else:
                    summary = json.dumps(chunk, ensure_ascii=False)[:200]
                    print(f"[{i}] {chunk_type}: {summary}")

            except json.JSONDecodeError:
                print(f"[{i}] (非 JSON): {line[:200]}")

    print("\n✅ 测试完成!")


if __name__ == "__main__":
    asyncio.run(test_chat())
