"""地理位置工具 — 通过 IP 获取地理位置信息"""

import json
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ip = args.get("ip") or context.get("clientIP")
    if not ip or ip == "127.0.0.1":
        # 本地/无 IP 时尝试通过公网 API 获取位置
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://ipapi.co/json/")
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "city": data.get("city", "未知"),
                        "regionName": data.get("region", "未知"),
                        "country": data.get("country_name", "未知"),
                        "ip": data.get("ip", ip or "127.0.0.1"),
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                    }
        except Exception:
            pass
        return {
            "city": "北京",
            "country": "中国",
            "ip": ip or "127.0.0.1",
            "timezone": "Asia/Shanghai",
        }

    # 通过 IP 查询地理位置
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?lang=zh-CN")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return {
                        "city": data.get("city", "未知"),
                        "regionName": data.get("regionName", "未知"),
                        "country": data.get("country", "未知"),
                        "ip": ip,
                        "latitude": data.get("lat"),
                        "longitude": data.get("lon"),
                    }
    except Exception:
        pass

    return {
        "city": "北京",
        "country": "中国",
        "ip": ip,
        "timezone": "Asia/Shanghai",
    }


def _fmt_input(args: dict[str, Any]) -> str:
    ip = args.get("ip", "")
    return f"获取位置，IP: {ip}" if ip else "获取位置"


def register():
    tool_registry.register(ChatToolDefinition(
        name="get_location",
        description="获取IP地址对应的地理位置",
        parameters={
            "type": "object",
            "properties": {
                "ip": {
                    "type": "string",
                    "description": "IP地址，可选，默认使用客户端IP",
                }
            },
        },
        execute=execute,
        format_input=lambda args: _fmt_input(args),
        planning_category="information",
        decision_weight=0.7,
        keywords=["位置", "IP", "城市", "地区", "定位"],
    ))
