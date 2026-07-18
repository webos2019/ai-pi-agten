"""DeepSeek API 异步客户端 — 基于 OpenAI 兼容接口"""

import os
from typing import Any

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_client: AsyncOpenAI | None = None


def get_deepseek_client() -> AsyncOpenAI:
    """获取 DeepSeek API 异步客户端单例"""
    global _client
    if not API_KEY or API_KEY == "your_deepseek_api_key_here":
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置。请在 .env 文件中设置有效的 DEEPSEEK_API_KEY。"
        )
    if _client is None:
        _client = AsyncOpenAI(
            api_key=API_KEY,
            base_url=API_BASE,
        )
    return _client


def get_model() -> str:
    """获取模型名称"""
    return MODEL


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> Any:
    """异步调用 chat completion 接口"""
    client = get_deepseek_client()
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


def reset_client() -> None:
    """重置客户端实例（配置变更后调用）"""
    global _client
    _client = None
