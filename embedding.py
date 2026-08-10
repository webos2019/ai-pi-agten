"""
Embedding 客户端 — 三种模式，全部向后兼容

模式切换 (.env):
  EMBEDDING_MODE=hash    → 离线哈希向量（零下载、零网络、纯计算）★推荐离线使用
  EMBEDDING_MODE=local   → 本地 sentence-transformers 模型（需下载 ~100MB）
  EMBEDDING_MODE=api     → OpenAI 兼容 API（需联网 + API Key）

hash 模式原理:
  - 使用 sklearn HashingVectorizer 做字符级 n-gram 哈希
  - 中文友好: 逐字切分 + 双字组，捕捉语义
  - 固定维度 (512)，L2 归一化，兼容 cosine 相似度
  - 无状态: 不需要 fit，不需要训练，不需要下载模型
  - 质量: 不如深度学习模型，但远好于关键词匹配

配置项 (.env):
  EMBEDDING_MODE              — hash / local / api
  EMBEDDING_DIMENSIONS         — 向量维度 (hash 模式默认 512)
  EMBEDDING_LOCAL_MODEL        — local 模式的模型名
  EMBEDDING_API_KEY / _BASE / _MODEL — api 模式配置
"""

from __future__ import annotations

import os
import asyncio
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ──────────────────────────────────────────────

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "hash")  # hash / local / api
EMBEDDING_LOCAL_MODEL = os.getenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5")

# API 模式配置
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "") or os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "512"))

# embedding 模型标识，用于 semantic 元数据校验（防止跨版本语义漂移）
if EMBEDDING_MODE == "hash":
    EMBEDDING_MODEL_ID = f"hash-char-ngram-{EMBEDDING_DIMENSIONS}"
    EMBEDDING_PROVIDER_KIND = "local-hashing"
elif EMBEDDING_MODE == "local":
    EMBEDDING_MODEL_ID = EMBEDDING_LOCAL_MODEL
    EMBEDDING_PROVIDER_KIND = "local-sentence-transformers"
else:
    EMBEDDING_MODEL_ID = EMBEDDING_MODEL or "unknown-embedding-model"
    EMBEDDING_PROVIDER_KIND = "openai-compatible"

MAX_BATCH_SIZE = 64


# ═════════════════════════════════════════════════════
#  hash 模式: 离线哈希向量 (零下载、零网络)
# ═════════════════════════════════════════════════════

_hash_vectorizer = None


def _get_hash_vectorizer():
    """延迟初始化 HashingVectorizer"""
    global _hash_vectorizer
    if _hash_vectorizer is not None:
        return _hash_vectorizer

    from sklearn.feature_extraction.text import HashingVectorizer

    _hash_vectorizer = HashingVectorizer(
        n_features=EMBEDDING_DIMENSIONS,
        ngram_range=(1, 2),         # 单字 + 双字组
        analyzer="char",             # 字符级 (中文友好)
        norm="l2",                   # L2 归一化 (cosine 相似度需要)
        alternate_sign=False,        # 无符号哈希 (避免抵消)
    )
    print(f"[embedding] 哈希向量器已就绪 (维度={EMBEDDING_DIMENSIONS}, 字符n-gram)")
    return _hash_vectorizer


def _hash_encode(texts: list[str]) -> list[list[float]]:
    """哈希向量编码 (同步，在线程池中调用)"""
    vec = _get_hash_vectorizer()
    sparse = vec.transform(texts)
    dense = sparse.toarray()
    return [[float(x) for x in row] for row in dense]


# ═════════════════════════════════════════════════════
#  local 模式: sentence-transformers (需下载模型)
# ═════════════════════════════════════════════════════

_local_model = None
_local_dims: int | None = None


def _get_local_model():
    """延迟加载本地 sentence-transformers 模型"""
    global _local_model, _local_dims
    if _local_model is not None:
        return _local_model

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "sentence-transformers 未安装。请运行: pip install sentence-transformers"
        )

    print(f"[embedding] 正在加载本地模型: {EMBEDDING_LOCAL_MODEL} ...")
    _local_model = SentenceTransformer(EMBEDDING_LOCAL_MODEL)

    test_vec = _local_model.encode("维度检测")
    _local_dims = len(test_vec)
    print(f"[embedding] 本地模型已加载: {EMBEDDING_LOCAL_MODEL} (维度={_local_dims})")

    return _local_model


def _get_local_dims() -> int:
    """获取本地模型的向量维度（自动检测）"""
    global _local_dims
    if _local_dims is None:
        _get_local_model()
    return _local_dims or 512


def _local_encode(texts: list[str]) -> list[list[float]]:
    """本地模型同步编码"""
    model = _get_local_model()
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return [[float(x) for x in vec] for vec in vectors]


# ═════════════════════════════════════════════════════
#  API 模式: OpenAI 兼容客户端
# ═════════════════════════════════════════════════════

_api_client = None


def _get_api_client():
    """延迟创建 AsyncOpenAI 客户端单例"""
    global _api_client
    if _api_client is not None:
        return _api_client

    from openai import AsyncOpenAI

    if not EMBEDDING_API_KEY or EMBEDDING_API_KEY == "your_deepseek_api_key_here":
        raise RuntimeError("EMBEDDING_API_KEY 未配置。")

    if not EMBEDDING_MODEL:
        raise RuntimeError("EMBEDDING_MODEL 未配置。")

    _api_client = AsyncOpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
    )
    return _api_client


# ═════════════════════════════════════════════════════
#  公共接口 (三种模式统一入口)
# ═════════════════════════════════════════════════════

def is_embedding_configured() -> bool:
    """检查 embedding 是否已配置"""
    if EMBEDDING_MODE in ("hash", "local"):
        return True
    return bool(EMBEDDING_API_KEY and EMBEDDING_MODEL)


def get_embedding_dimensions() -> int:
    """获取向量维度"""
    if EMBEDDING_MODE == "hash":
        return EMBEDDING_DIMENSIONS
    if EMBEDDING_MODE == "local":
        return _get_local_dims()
    return EMBEDDING_DIMENSIONS


def get_embedding_model_id() -> str:
    """获取 embedding 模型标识（用于 semantic 元数据校验）"""
    return EMBEDDING_MODEL_ID


def get_embedding_provider_kind() -> str:
    """获取 embedding provider 类型"""
    return EMBEDDING_PROVIDER_KIND


# ─── 核心方法 ──────────────────────────────────────────

def _validate_embedding(vec: Any, expected_dims: int) -> list[float]:
    """校验单个 embedding 向量"""
    if not isinstance(vec, (list, type(None))):
        try:
            vec = list(vec)
        except Exception:
            raise ValueError(f"embedding 返回值类型异常: {type(vec)}")
    if not isinstance(vec, list):
        raise ValueError(f"embedding 返回值不是列表: {type(vec)}")
    if len(vec) != expected_dims:
        raise ValueError(f"embedding 维度不匹配: 期望 {expected_dims}, 实际 {len(vec)}")
    return [float(v) for v in vec]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    批量文本转向量。

    hash 模式:  sklearn HashingVectorizer (线程池异步)
    local 模式: sentence-transformers CPU (线程池异步)
    api 模式:   OpenAI 兼容 API 分批请求
    """
    if not texts:
        return []

    loop = asyncio.get_event_loop()

    if EMBEDDING_MODE == "hash":
        all_vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[start:start + MAX_BATCH_SIZE]
            vectors = await loop.run_in_executor(None, _hash_encode, batch)
            all_vectors.extend(vectors)
        return all_vectors

    if EMBEDDING_MODE == "local":
        all_vectors = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[start:start + MAX_BATCH_SIZE]
            vectors = await loop.run_in_executor(None, _local_encode, batch)
            all_vectors.extend(vectors)
        return all_vectors

    # API 模式
    client = _get_api_client()
    all_vectors = []
    for start in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[start:start + MAX_BATCH_SIZE]
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
            dimensions=EMBEDDING_DIMENSIONS,
            encoding_format="float",
        )
        sorted_data = sorted(response.data, key=lambda x: x.index)
        for item in sorted_data:
            vec = _validate_embedding(item.embedding, EMBEDDING_DIMENSIONS)
            all_vectors.append(vec)
    return all_vectors


async def embed_query(text: str) -> list[float]:
    """
    单条文本转向量（搜索时 query 向量化）。
    """
    if not text or not text.strip():
        raise ValueError("embed_query: 文本不能为空")

    loop = asyncio.get_event_loop()

    if EMBEDDING_MODE == "hash":
        vectors = await loop.run_in_executor(None, _hash_encode, [text])
        return vectors[0]

    if EMBEDDING_MODE == "local":
        vectors = await loop.run_in_executor(None, _local_encode, [text])
        return vectors[0]

    # API 模式
    client = _get_api_client()
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text],
        dimensions=EMBEDDING_DIMENSIONS,
        encoding_format="float",
    )
    return _validate_embedding(response.data[0].embedding, EMBEDDING_DIMENSIONS)


def reset_client() -> None:
    """重置所有客户端实例"""
    global _api_client, _local_model, _local_dims, _hash_vectorizer
    _api_client = None
    _local_model = None
    _local_dims = None
    _hash_vectorizer = None
