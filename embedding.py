"""
Embedding 客户端 — OpenAI 兼容接口，文本转向量

设计要点:
- 复用项目已有的 openai SDK（AsyncOpenAI），不引入额外依赖
- API Key / Base URL 可独立配置，也可回退到 DeepSeek 配置
- model id 固定，不受聊天模型切换影响（语义召回质量独立稳定）
- 延迟初始化：首次调用时才创建客户端
- 未配置时抛出明确错误，由上层 catch 做降级
- 返回值逐条校验：是不是数组、是不是数字、维度对不对

配置项 (.env):
  EMBEDDING_API_KEY       — embedding API Key（默认回退 DEEPSEEK_API_KEY）
  EMBEDDING_API_BASE      — embedding API Base（默认回退 DEEPSEEK_API_BASE）
  EMBEDDING_MODEL         — embedding 模型 id（必填，如 text-embedding-3-small）
  EMBEDDING_DIMENSIONS    — 向量维度（默认 1024）
"""

from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ──────────────────────────────────────────────

EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "") or os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))

# embedding 模型标识，用于 semantic 元数据校验（防止跨版本语义漂移）
EMBEDDING_MODEL_ID = EMBEDDING_MODEL or "unknown-embedding-model"
EMBEDDING_PROVIDER_KIND = "openai-compatible"

# 单次请求最大文本数（避免一次请求太多文本）
MAX_BATCH_SIZE = 64


# ─── 客户端单例 ────────────────────────────────────────

_client: AsyncOpenAI | None = None
_client_initialized: bool = False


def _get_client() -> AsyncOpenAI:
    """延迟创建 AsyncOpenAI 客户端单例"""
    global _client, _client_initialized
    if _client is not None:
        return _client

    if not EMBEDDING_API_KEY or EMBEDDING_API_KEY == "your_deepseek_api_key_here":
        raise RuntimeError(
            "EMBEDDING_API_KEY 未配置。请在 .env 中设置 EMBEDDING_API_KEY "
            "（或 DEEPSEEK_API_KEY）。"
        )

    if not EMBEDDING_MODEL:
        raise RuntimeError(
            "EMBEDDING_MODEL 未配置。请在 .env 中设置 EMBEDDING_MODEL "
            "（如 text-embedding-3-small）。"
        )

    _client = AsyncOpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
    )
    _client_initialized = True
    return _client


def is_embedding_configured() -> bool:
    """检查 embedding 是否已配置（用于上层判断是否启用向量召回）"""
    return bool(EMBEDDING_API_KEY and EMBEDDING_MODEL)


def get_embedding_dimensions() -> int:
    """获取向量维度"""
    return EMBEDDING_DIMENSIONS


def get_embedding_model_id() -> str:
    """获取 embedding 模型标识（用于 semantic 元数据）"""
    return EMBEDDING_MODEL_ID


def get_embedding_provider_kind() -> str:
    """获取 embedding provider 类型"""
    return EMBEDDING_PROVIDER_KIND


# ─── 核心方法 ──────────────────────────────────────────

def _validate_embedding(vec: Any, expected_dims: int) -> list[float]:
    """校验单个 embedding 向量：是列表、是数字、维度正确"""
    if not isinstance(vec, list):
        raise ValueError(f"embedding 返回值不是列表: {type(vec)}")
    if len(vec) != expected_dims:
        raise ValueError(
            f"embedding 维度不匹配: 期望 {expected_dims}, 实际 {len(vec)}"
        )
    for i, v in enumerate(vec):
        if not isinstance(v, (int, float)):
            raise ValueError(f"embedding[{i}] 不是数字: {type(v)}")
    return [float(v) for v in vec]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    批量文本转向量。

    - 自动分批（MAX_BATCH_SIZE），避免单次请求过大
    - 逐条校验返回向量的类型和维度
    - 任何一条不满足直接抛错，由上层 catch 做降级
    """
    if not texts:
        return []

    client = _get_client()
    all_vectors: list[list[float]] = []

    # 分批请求
    for start in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[start:start + MAX_BATCH_SIZE]
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
            dimensions=EMBEDDING_DIMENSIONS,
            encoding_format="float",
        )

        # 按 index 排序确保顺序正确
        sorted_data = sorted(response.data, key=lambda x: x.index)
        for item in sorted_data:
            vec = _validate_embedding(item.embedding, EMBEDDING_DIMENSIONS)
            all_vectors.append(vec)

    return all_vectors


async def embed_query(text: str) -> list[float]:
    """
    单条文本转向量（用于搜索时 query 向量化）。

    - 校验返回向量的类型和维度
    - 失败直接抛错，由上层 catch 做降级
    """
    if not text or not text.strip():
        raise ValueError("embed_query: 文本不能为空")

    client = _get_client()
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text],
        dimensions=EMBEDDING_DIMENSIONS,
        encoding_format="float",
    )

    return _validate_embedding(response.data[0].embedding, EMBEDDING_DIMENSIONS)


def reset_client() -> None:
    """重置客户端实例（配置变更后调用，主要用于测试）"""
    global _client, _client_initialized
    _client = None
    _client_initialized = False
