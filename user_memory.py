"""
UserMemory — 长期用户记忆数据模型 + 内存向量存储

设计要点（借鉴掘金文章 AI Mind v0.4.6）:
- UserMemory: 跨会话可复用的长期用户记忆（如"用户不吃香菜"）
- 只有 text 和 tags 参与向量化，其余字段只做过滤元信息
- 只有 active 状态的记忆才进入语义搜索候选池
- 每条 active 记忆附带 semantic 元数据（模型 id / 索引版本），防止跨版本漂移
- 内存存储（与 ThreadStore 一致），纯 Python cosine 相似度，无额外依赖
- 按 session_id 划分 namespace，浏览器会话级隔离

三层记忆 vs 长期记忆:
- ThreadState (短期): 会话内 recent + summary + pinned，每轮都拼进上下文
- UserMemory (长期): 跨会话的用户偏好，按语义相关度召回，最多注入 3 条

持久化:
- DuckDB Write-Through: 内存缓存优先读，写入时同步落盘
- 服务重启后自动从 DuckDB 恢复（含向量）
- DuckDB 不可用时降级为纯内存模式
"""

from __future__ import annotations

import time
import math
from dataclasses import dataclass, field
from typing import Any

from embedding import (
    embed_query,
    get_embedding_model_id,
    get_embedding_provider_kind,
    is_embedding_configured,
)
from duckdb_store import DuckDBPersistence


# ─── 常量 ──────────────────────────────────────────────

# semantic 索引版本（模型升级或索引结构变化时递增，旧索引自动失效）
SEMANTIC_INDEX_VERSION = "user-memory-semantic.v1"

# 参与向量化的字段白名单
SEMANTIC_INDEX_FIELDS = ["text", "tags"]

# polarity 枚举
POLARITY_PREFER = "prefer"      # 用户喜欢/偏好
POLARITY_AVOID = "avoid"        # 用户不喜欢/忌口
POLARITY_NEUTRAL = "neutral"    # 中性事实

# status 枚举
STATUS_ACTIVE = "active"        # 活跃，参与语义搜索
STATUS_SUPPRESSED = "suppressed"  # 被抑制，不参与搜索
STATUS_INACTIVE = "inactive"   # 不活跃


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class SemanticMetadata:
    """语义索引元数据 — 持久化在文档里，召回时校验模型/版本一致性"""
    embedding_model_id: str = ""
    embedding_provider_kind: str = ""
    semantic_index_fields: list[str] = field(default_factory=list)
    semantic_indexed_at: str = ""
    semantic_index_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "embeddingModelId": self.embedding_model_id,
            "embeddingProviderKind": self.embedding_provider_kind,
            "semanticIndexFields": self.semantic_index_fields,
            "semanticIndexedAt": self.semantic_indexed_at,
            "semanticIndexVersion": self.semantic_index_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SemanticMetadata":
        return cls(
            embedding_model_id=d.get("embeddingModelId", ""),
            embedding_provider_kind=d.get("embeddingProviderKind", ""),
            semantic_index_fields=d.get("semanticIndexFields", []),
            semantic_indexed_at=d.get("semanticIndexedAt", ""),
            semantic_index_version=d.get("semanticIndexVersion", ""),
        )

    @classmethod
    def create_current(cls) -> "SemanticMetadata":
        """创建当前版本的 semantic 元数据"""
        return cls(
            embedding_model_id=get_embedding_model_id(),
            embedding_provider_kind=get_embedding_provider_kind(),
            semantic_index_fields=list(SEMANTIC_INDEX_FIELDS),
            semantic_indexed_at=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            semantic_index_version=SEMANTIC_INDEX_VERSION,
        )


@dataclass
class UserMemory:
    """
    单条长期用户记忆。

    - text: 记忆正文（如"用户不吃香菜"）
    - tags: 标签（如 ["饮食", "忌口"]），参与向量化
    - polarity: 偏好方向（prefer/avoid/neutral），用于冲突检测
    - status: 状态（active/suppressed/inactive），只有 active 才进搜索
    - confidence: 置信度 0.0~1.0，低于阈值的不召回
    - semantic: 索引元数据，校验模型/版本一致性
    - embedding: 向量（仅内存，不序列化）
    """
    stable_key: str
    text: str
    tags: list[str] = field(default_factory=list)
    polarity: str = POLARITY_NEUTRAL
    status: str = STATUS_ACTIVE
    confidence: float = 0.7
    source_conversation_id: str = ""
    reason: str = ""
    memory_type: str = "preference"  # preference / fact / style
    subject: str = ""                # 主题（如"饮食"），用于冲突去重
    facet: str = ""                  # 维度（如"香菜"），用于冲突去重
    semantic: SemanticMetadata = field(default_factory=SemanticMetadata)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    embedding: list[float] | None = None  # 仅内存，不序列化

    def to_stored_dict(self) -> dict[str, Any]:
        """完整存储格式（含 semantic 元数据，不含 embedding）"""
        return {
            "stableKey": self.stable_key,
            "text": self.text,
            "tags": self.tags,
            "polarity": self.polarity,
            "status": self.status,
            "confidence": self.confidence,
            "sourceConversationId": self.source_conversation_id,
            "reason": self.reason,
            "memoryType": self.memory_type,
            "subject": self.subject,
            "facet": self.facet,
            "semantic": self.semantic.to_dict(),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    def to_dto(self) -> dict[str, Any]:
        """返回给前端的 DTO"""
        return self.to_stored_dict()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UserMemory":
        """从存储格式恢复"""
        semantic = SemanticMetadata.from_dict(d.get("semantic", {}))
        return cls(
            stable_key=d.get("stableKey", ""),
            text=d.get("text", ""),
            tags=d.get("tags", []),
            polarity=d.get("polarity", POLARITY_NEUTRAL),
            status=d.get("status", STATUS_ACTIVE),
            confidence=d.get("confidence", 0.7),
            source_conversation_id=d.get("sourceConversationId", ""),
            reason=d.get("reason", ""),
            memory_type=d.get("memoryType", "preference"),
            subject=d.get("subject", ""),
            facet=d.get("facet", ""),
            semantic=semantic,
            created_at=d.get("createdAt", time.time()),
            updated_at=d.get("updatedAt", time.time()),
        )


# ─── 向量相似度（纯 Python，无 numpy 依赖） ────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    余弦相似度 — 两个向量方向越接近，分数越高。

    对中文语义相似度来说，余弦距离是社区验证过的默认选择。
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# ─── 搜索结果 ──────────────────────────────────────────

@dataclass
class MemorySearchResult:
    """向量搜索结果项"""
    memory: UserMemory
    score: float  # 余弦相似度 0.0~1.0


# ─── UserMemoryStore — 内存向量存储 ────────────────────

class UserMemoryStore:
    """
    长期用户记忆存储 — 服务端单例。

    - 按 namespace（session_id）隔离
    - active 记忆自动向量化，suppressed/inactive 不建索引
    - 向量搜索：遍历计算 cosine 相似度，返回 top-K
    - DuckDB Write-Through: 内存缓存 + 持久化双层
    - 服务重启后自动从 DuckDB 恢复（含向量）
    - DuckDB 不可用时降级为纯内存模式
    """

    def __init__(self, persistence: DuckDBPersistence | None = None):
        # namespace -> { stable_key -> UserMemory }
        self._namespaces: dict[str, dict[str, UserMemory]] = {}
        self._persistence = persistence
        self._loaded_namespaces: set[str] = set()

    def _get_namespace(self, namespace: str) -> dict[str, UserMemory]:
        if namespace not in self._namespaces:
            self._namespaces[namespace] = {}
            # 延迟加载: 首次访问 namespace 时从 DuckDB 恢复
            self._load_namespace_from_db(namespace)
        return self._namespaces[namespace]

    def _load_namespace_from_db(self, namespace: str) -> None:
        """从 DuckDB 加载 namespace 下的所有记忆到内存缓存"""
        if namespace in self._loaded_namespaces:
            return
        self._loaded_namespaces.add(namespace)

        if not self._persistence or not self._persistence.is_enabled:
            return

        try:
            stored_list = self._persistence.load_memories(namespace)
            ns = self._namespaces.setdefault(namespace, {})
            for stored in stored_list:
                memory = UserMemory.from_dict(stored)
                # 恢复向量
                emb = stored.get("embedding")
                if emb:
                    memory.embedding = list(emb)
                ns[memory.stable_key] = memory
            if stored_list:
                print(f"[user-memory] 从 DuckDB 恢复 {len(stored_list)} 条记忆 (namespace={namespace})")
        except Exception as e:
            print(f"[user-memory] 从 DuckDB 恢复失败: {e}")

    def _persist_memory(self, namespace: str, memory: UserMemory) -> None:
        """Write-Through: 将内存中的记忆同步写入 DuckDB"""
        if not self._persistence or not self._persistence.is_enabled:
            return

        stored = memory.to_stored_dict()
        # embedding 单独传递（to_stored_dict 不含 embedding）
        stored["embedding"] = memory.embedding
        self._persistence.save_memory(namespace, stored)

    async def put(
        self,
        namespace: str,
        key: str,
        memory: UserMemory,
        index_fields: list[str] | bool,
    ) -> UserMemory:
        """
        存储一条用户记忆。

        - index_fields=['text','tags'] → active，自动向量化
        - index_fields=False → suppressed/inactive，不建向量索引
        - 如果之前被索引过，新的不索引写入会清除旧向量
        """
        ns = self._get_namespace(namespace)
        memory.stable_key = key
        memory.updated_at = time.time()

        if index_fields and memory.status == STATUS_ACTIVE:
            # active → 生成向量
            if is_embedding_configured():
                try:
                    # 拼接 text + tags 作为向量化输入
                    index_text = memory.text
                    if memory.tags:
                        index_text = f"{memory.text} {' '.join(memory.tags)}"

                    vec = await embed_query(index_text)
                    memory.embedding = vec
                    memory.semantic = SemanticMetadata.create_current()
                except Exception:
                    # embedding 失败，仍然存储记忆但不建向量索引
                    memory.embedding = None
                    memory.semantic = SemanticMetadata()
            else:
                memory.embedding = None
                memory.semantic = SemanticMetadata()
        else:
            # suppressed/inactive → 清除向量
            memory.embedding = None
            memory.semantic = SemanticMetadata()

        ns[key] = memory
        self._persist_memory(namespace, memory)
        return memory

    async def search(
        self,
        namespace: str,
        query_embedding: list[float],
        limit: int = 8,
    ) -> list[MemorySearchResult]:
        """
        向量搜索 — 返回 cosine 相似度 top-K 的 active 记忆。

        - 只搜索有向量且 status=active 的记忆
        - 按分数降序排列
        """
        ns = self._get_namespace(namespace)
        results: list[MemorySearchResult] = []

        for memory in ns.values():
            if memory.status != STATUS_ACTIVE:
                continue
            if memory.embedding is None:
                continue
            if not memory.semantic.semantic_index_version:
                continue

            score = cosine_similarity(query_embedding, memory.embedding)
            results.append(MemorySearchResult(memory=memory, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def list_memories(self, namespace: str) -> list[UserMemory]:
        """列出 namespace 下所有记忆"""
        ns = self._get_namespace(namespace)
        return list(ns.values())

    def get(self, namespace: str, key: str) -> UserMemory | None:
        """获取单条记忆"""
        ns = self._get_namespace(namespace)
        return ns.get(key)

    def delete(self, namespace: str, key: str) -> bool:
        """删除一条记忆"""
        ns = self._get_namespace(namespace)
        if key in ns:
            del ns[key]
            if self._persistence and self._persistence.is_enabled:
                self._persistence.delete_memory(namespace, key)
            return True
        return False

    def update_status(
        self,
        namespace: str,
        key: str,
        status: str,
    ) -> UserMemory | None:
        """更新记忆状态（active/suppressed/inactive）"""
        ns = self._get_namespace(namespace)
        memory = ns.get(key)
        if not memory:
            return None

        memory.status = status
        memory.updated_at = time.time()

        # suppressed/inactive → 清除向量
        if status != STATUS_ACTIVE:
            memory.embedding = None
            memory.semantic = SemanticMetadata()

        self._persist_memory(namespace, memory)
        return memory

    def find_by_text(
        self,
        namespace: str,
        text: str,
    ) -> UserMemory | None:
        """精确文本查找（用于去重）"""
        ns = self._get_namespace(namespace)
        for memory in ns.values():
            if memory.text == text:
                return memory
        return None

    def find_by_subject_facet(
        self,
        namespace: str,
        subject: str,
        facet: str,
    ) -> list[UserMemory]:
        """按 subject + facet 查找（用于冲突检测）"""
        ns = self._get_namespace(namespace)
        return [
            m for m in ns.values()
            if m.subject == subject and m.facet == facet
        ]


# ─── 全局单例 ──────────────────────────────────────────

from duckdb_store import get_persistence

user_memory_store = UserMemoryStore(persistence=get_persistence())


# ─── Namespace 工具 ────────────────────────────────────

def get_memory_namespace(session_id: str) -> str:
    """
    根据 session_id 生成 memory namespace。

    结构: user-memory:{session_id}
    浏览器会话级隔离，跨会话可复用。
    """
    return f"user-memory:{session_id}"
