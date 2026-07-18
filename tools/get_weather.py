"""天气查询工具 — 通过 wttr.in 获取实时天气"""

import json
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    city = args.get("city", "")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://wttr.in/{city}?format=j1",
                headers={"Accept-Language": "zh-CN"},
            )
            if resp.status_code != 200:
                return {"city": city, "error": f"HTTP {resp.status_code}"}

            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            forecast = data.get("weather", [{}])[0]

            if not current:
                return {"city": city, "error": f"无法获取 {city} 的天气信息"}

            weather_desc = ""
            desc_list = current.get("weatherDesc", [])
            if desc_list:
                weather_desc = desc_list[0].get("value", "未知")

            return {
                "city": city,
                "weather": weather_desc,
                "temperature": current.get("temp_C", "未知"),
                "feelsLike": current.get("FeelsLikeC", "未知"),
                "temperatureMax": forecast.get("maxtempC", "未知"),
                "temperatureMin": forecast.get("mintempC", "未知"),
                "humidity": current.get("humidity", "未知"),
                "windSpeed": current.get("windspeedKmph", "未知"),
                "visibility": current.get("visibility", "未知"),
                "source": "wttr.in",
            }
    except Exception as e:
        return {"city": city, "error": f"天气查询失败: {e}"}


def register():
    tool_registry.register(ChatToolDefinition(
        name="get_weather",
        description="获取指定城市的天气信息",
        parameters={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称，如: 北京",
                }
            },
            "required": ["city"],
        },
        execute=execute,
        format_input=lambda args: f"查询天气: {args.get('city', '')}",
        result_is_authoritative=True,
        planning_category="information",
        decision_weight=0.8,
        keywords=["天气", "温度", "预报", "城市"],
    ))
