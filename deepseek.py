"""DeepSeek API 异步客户端 — 基于 OpenAI 兼容接口

支持运行时动态切换 API 配置（provider / model / key）。
支持 per-session_id 配置隔离：每个工作区（公司）可拥有独立的 LLM 配置。
默认从 .env 读取初始配置，运行时可通过 update_config() 覆盖。
"""

import os
import json
import threading
from typing import Any

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── 预设 LLM 提供商 ──────────────────────────────────

LLM_PRESETS: list[dict[str, str]] = [
    {"id": "deepseek", "label": "DeepSeek", "apiBase": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"id": "doubao", "label": "豆包 (Doubao)", "apiBase": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-32k"},
    {"id": "doubao-lite", "label": "豆包 Lite", "apiBase": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-lite-32k"},
    {"id": "doubao-vision", "label": "豆包 Vision", "apiBase": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-vision-pro-32k"},
    {"id": "openai", "label": "OpenAI", "apiBase": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"id": "openai-gpt4o", "label": "OpenAI GPT-4o", "apiBase": "https://api.openai.com/v1", "model": "gpt-4o"},
    {"id": "moonshot", "label": "Moonshot (Kimi)", "apiBase": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"id": "moonshot-32k", "label": "Kimi 32K", "apiBase": "https://api.moonshot.cn/v1", "model": "moonshot-v1-32k"},
    {"id": "zhipu", "label": "智谱 GLM-4-Flash", "apiBase": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    {"id": "zhipu-air", "label": "智谱 GLM-4-Air", "apiBase": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-air"},
    {"id": "dashscope", "label": "通义千问 Turbo", "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo"},
    {"id": "dashscope-plus", "label": "通义千问 Plus", "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"id": "dashscope-max", "label": "通义千问 Max", "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max"},
    {"id": "baichuan", "label": "百川 Baichuan", "apiBase": "https://api.baichuan-ai.com/v1", "model": "Baichuan4"},
    {"id": "minimax", "label": "MiniMax", "apiBase": "https://api.minimax.chat/v1", "model": "abab6.5s-chat"},
    {"id": "yi", "label": "零一万物 Yi", "apiBase": "https://api.lingyiwanwu.com/v1", "model": "yi-large"},
    {"id": "stepfun", "label": "阶跃星辰 Step", "apiBase": "https://api.stepfun.com/v1", "model": "step-1-8k"},
    {"id": "custom", "label": "自定义", "apiBase": "", "model": ""},
]

# ─── 配置文件路径 ──────────────────────────────────────

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "data")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "llm_config.json")


def _session_config_file(session_id: str) -> str:
    """每个 session_id 对应一个独立的配置文件"""
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    return os.path.join(_CONFIG_DIR, f"llm_config_{safe_id}.json")


# ─── 全局默认配置 (从 .env + data/llm_config.json 加载) ─────

_config_lock = threading.Lock()
_config: dict[str, str] = {
    "provider": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "apiKey": os.getenv("DEEPSEEK_API_KEY", ""),
}
_client: AsyncOpenAI | None = None

# ─── per-session 配置缓存 ──────────────────────────────

_session_configs: dict[str, dict[str, str]] = {}
_session_clients: dict[str, AsyncOpenAI] = {}


def _load_config_from_file() -> None:
    """从 data/llm_config.json 加载全局默认配置（如果存在）"""
    global _config
    try:
        if os.path.isfile(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict) and saved.get("apiKey"):
                _config.update({
                    "provider": saved.get("provider", _config["provider"]),
                    "model": saved.get("model", _config["model"]),
                    "apiKey": saved.get("apiKey", _config["apiKey"]),
                })
    except Exception:
        pass


def _save_config_to_file() -> None:
    """持久化全局默认配置到 data/llm_config.json"""
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_session_config(session_id: str) -> None:
    """从文件加载 per-session 配置（如果存在），否则以全局默认为基底"""
    if session_id in _session_configs:
        return
    cfg = dict(_config)  # 以全局默认为基底
    config_file = _session_config_file(session_id)
    try:
        if os.path.isfile(config_file):
            with open(config_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                cfg["provider"] = saved.get("provider", cfg["provider"])
                cfg["model"] = saved.get("model", cfg["model"])
                if saved.get("apiKey"):
                    cfg["apiKey"] = saved["apiKey"]
    except Exception:
        pass
    _session_configs[session_id] = cfg


def _save_session_config(session_id: str) -> None:
    """持久化 per-session 配置到文件"""
    cfg = _session_configs.get(session_id)
    if not cfg:
        return
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        config_file = _session_config_file(session_id)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_effective_config(session_id: str = "") -> dict[str, str]:
    """获取生效的配置：有 session_id 则用 per-session，否则用全局默认"""
    if session_id:
        _load_session_config(session_id)
        return _session_configs.get(session_id, _config)
    return _config


# 启动时加载全局持久化配置
_load_config_from_file()


# ─── 公开 API ──────────────────────────────────────────

def get_config(session_id: str = "") -> dict[str, str]:
    """获取 LLM 配置（apiKey 脱敏）。传入 session_id 获取 per-session 配置。"""
    with _config_lock:
        cfg = _get_effective_config(session_id)
        key = cfg["apiKey"]
        masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        return {
            "provider": cfg["provider"],
            "model": cfg["model"],
            "apiKeyMasked": masked,
            "hasKey": bool(key),
        }


def update_config(
    provider: str, model: str, api_key: str, session_id: str = "",
) -> dict[str, str]:
    """更新 LLM 配置并重置客户端。持久化到文件。

    传入 session_id 时更新 per-session 配置，否则更新全局默认。
    """
    with _config_lock:
        if session_id:
            _load_session_config(session_id)
            cfg = _session_configs.setdefault(session_id, dict(_config))
            cfg["provider"] = provider or cfg["provider"]
            cfg["model"] = model or cfg["model"]
            if api_key and api_key != "***":
                cfg["apiKey"] = api_key
            _save_session_config(session_id)
            _session_clients.pop(session_id, None)
        else:
            _config["provider"] = provider or _config["provider"]
            _config["model"] = model or _config["model"]
            if api_key and api_key != "***":
                _config["apiKey"] = api_key
            _save_config_to_file()
            _client = None  # type: ignore[assignment]  # 触发重建
    return get_config(session_id)


def get_presets() -> list[dict[str, str]]:
    """获取预设 LLM 提供商列表"""
    return LLM_PRESETS


def get_deepseek_client(session_id: str = "") -> AsyncOpenAI:
    """获取 API 异步客户端（基于当前配置）。

    传入 session_id 时返回 per-session 客户端，否则返回全局客户端。
    """
    global _client

    if session_id:
        if session_id not in _session_clients:
            with _config_lock:
                if session_id not in _session_clients:
                    cfg = _get_effective_config(session_id)
                    api_key = cfg["apiKey"]
                    if not api_key or api_key == "your_deepseek_api_key_here":
                        raise RuntimeError(
                            "API Key 未配置。请在设置面板中配置 LLM API Key。"
                        )
                    _session_clients[session_id] = AsyncOpenAI(
                        api_key=api_key,
                        base_url=cfg["provider"],
                    )
        return _session_clients[session_id]

    # 全局客户端
    if _client is None:
        with _config_lock:
            if _client is None:
                api_key = _config["apiKey"]
                if not api_key or api_key == "your_deepseek_api_key_here":
                    raise RuntimeError(
                        "API Key 未配置。请在设置面板中配置 LLM API Key。"
                    )
                _client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=_config["provider"],
                )
    return _client


def get_model(session_id: str = "") -> str:
    """获取当前模型名称。传入 session_id 获取 per-session 模型。"""
    return _get_effective_config(session_id)["model"]


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    session_id: str = "",
) -> Any:
    """异步调用 chat completion 接口。

    传入 session_id 时使用 per-session 的 API Key 和模型。
    """
    client = get_deepseek_client(session_id)
    model = get_model(session_id)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


def reset_client(session_id: str = "") -> None:
    """重置客户端实例（配置变更后调用）。

    传入 session_id 时重置 per-session 客户端，否则重置全局客户端。
    """
    global _client
    if session_id:
        _session_clients.pop(session_id, None)
    else:
        _client = None
