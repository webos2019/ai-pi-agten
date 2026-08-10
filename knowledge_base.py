"""
KnowledgeBase — 知识库向量存储 + 文档分块 + 语义搜索

复用已有基础设施:
- embedding.py: embed_documents / embed_query (文本转向量)
- user_memory.py: cosine_similarity 算法
- duckdb_store.py: DuckDB 持久化 (kb_documents + kb_chunks 表)

隔离:
- namespace = kb:{session_id}，与 UserMemory 平行
- 每个工作区（公司）有独立的知识库

设计要点:
- 文档分块: 按字符数滑窗切割 (chunk_size=500, overlap=50)
- 向量化: 批量调用 embedding API
- 搜索: 纯 Python cosine 相似度遍历 top-K
- 持久化: DuckDB Write-Through (内存缓存 + 落盘)
- 降级: embedding 未配置时静默返回空结果
"""

from __future__ import annotations

import time
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from embedding import (
    embed_documents,
    embed_query,
    is_embedding_configured,
    get_embedding_model_id,
)
from duckdb_store import DuckDBPersistence


# ─── 常量 ──────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 500        # 每块最大字符数
DEFAULT_CHUNK_OVERLAP = 50      # 滑窗重叠字符数
MAX_SEARCH_RESULTS = 5          # 搜索返回上限
SCORE_THRESHOLD = 0.25          # 召回分数阈值
MAX_INJECT_RESULTS = 3          # 注入模型上下文的上限
MAX_INJECT_CHARS = 500          # 单条注入文本上限


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class KnowledgeChunk:
    """文档分块"""
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    embedding: list[float] | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "embedding": self.embedding,
            "created_at": self.created_at,
        }


@dataclass
class KnowledgeDocument:
    """文档元数据"""
    doc_id: str
    title: str
    source_type: str        # text / pdf / markdown / url
    source_path: str
    chunk_count: int = 0
    char_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dto(self) -> dict[str, Any]:
        return {
            "docId": self.doc_id,
            "title": self.title,
            "sourceType": self.source_type,
            "sourcePath": self.source_path,
            "chunkCount": self.chunk_count,
            "charCount": self.char_count,
            "createdAt": self.created_at,
        }


@dataclass
class KnowledgeSearchResult:
    """搜索结果"""
    chunk: KnowledgeChunk
    score: float
    doc_title: str = ""


# ─── 向量相似度 (复用 user_memory 的实现) ──────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度"""
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


# ─── KnowledgeBaseStore ────────────────────────────────

class KnowledgeBaseStore:
    """
    知识库存储 — 服务端单例。

    - 按 namespace（kb:{session_id}）隔离
    - 文档分块后批量向量化
    - 向量搜索: 遍历计算 cosine 相似度，返回 top-K
    - DuckDB Write-Through: 内存缓存 + 持久化双层
    - 服务重启后自动从 DuckDB 恢复（含向量）
    """

    def __init__(self, persistence: DuckDBPersistence | None = None):
        # namespace -> { chunk_id -> KnowledgeChunk }
        self._namespaces: dict[str, dict[str, KnowledgeChunk]] = {}
        # namespace -> { doc_id -> KnowledgeDocument }
        self._docs: dict[str, dict[str, KnowledgeDocument]] = {}
        self._persistence = persistence
        self._loaded_namespaces: set[str] = set()

    def _get_chunks_namespace(self, namespace: str) -> dict[str, KnowledgeChunk]:
        if namespace not in self._namespaces:
            self._namespaces[namespace] = {}
            self._load_namespace_from_db(namespace)
        return self._namespaces[namespace]

    def _get_docs_namespace(self, namespace: str) -> dict[str, KnowledgeDocument]:
        if namespace not in self._docs:
            self._docs[namespace] = {}
        return self._docs[namespace]

    def _load_namespace_from_db(self, namespace: str) -> None:
        """从 DuckDB 加载 namespace 下的所有分块和文档"""
        if namespace in self._loaded_namespaces:
            return
        self._loaded_namespaces.add(namespace)

        if not self._persistence or not self._persistence.is_enabled:
            return

        try:
            # 加载分块
            chunk_list = self._persistence.load_kb_chunks(namespace)
            ns = self._namespaces.setdefault(namespace, {})
            for stored in chunk_list:
                chunk = KnowledgeChunk(
                    chunk_id=stored["chunk_id"],
                    doc_id=stored["doc_id"],
                    chunk_index=stored["chunk_index"],
                    text=stored["text"],
                    embedding=list(stored["embedding"]) if stored.get("embedding") else None,
                    created_at=stored.get("created_at", time.time()),
                )
                ns[chunk.chunk_id] = chunk

            # 加载文档元数据
            doc_list = self._persistence.load_kb_documents(namespace)
            docs = self._docs.setdefault(namespace, {})
            for d in doc_list:
                doc = KnowledgeDocument(
                    doc_id=d["doc_id"],
                    title=d["title"],
                    source_type=d["source_type"],
                    source_path=d["source_path"],
                    chunk_count=d["chunk_count"],
                    char_count=d["char_count"],
                    created_at=d["created_at"],
                )
                docs[doc.doc_id] = doc

            if chunk_list:
                print(f"[knowledge-base] 从 DuckDB 恢复 {len(doc_list)} 个文档, {len(chunk_list)} 个分块 (namespace={namespace[:20]}...)")
        except Exception as e:
            print(f"[knowledge-base] 从 DuckDB 恢复失败: {e}")

    # ─── 文档分块 ─────────────────────────────────────

    @staticmethod
    def chunk_text(
        text: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """
        文本分块: 按字符数滑窗切割。

        - 尽量在句子边界（句号、换行）处切割
        - 滑窗重叠保证上下文连续性
        """
        if not text or not text.strip():
            return []

        # 按段落先粗分
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) <= chunk_size:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current:
                    chunks.append(current)
                # 如果单段就超长，按 chunk_size 硬切
                while len(para) > chunk_size:
                    chunks.append(para[:chunk_size])
                    para = para[chunk_size - overlap:]
                current = para

        if current:
            chunks.append(current)

        return chunks if chunks else [text[:chunk_size]]

    # ─── 添加文档 ─────────────────────────────────────

    async def add_document(
        self,
        namespace: str,
        title: str,
        source_type: str,
        text: str,
        source_path: str = "",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> KnowledgeDocument:
        """
        添加文档: 分块 → 向量化 → 存储。

        返回: KnowledgeDocument 元数据
        """
        if not text or not text.strip():
            raise ValueError("文档内容不能为空")

        # 1. 分块
        chunks_text = self.chunk_text(text, chunk_size, overlap)
        if not chunks_text:
            raise ValueError("分块后无有效内容")

        # 2. 检查 embedding 是否可用
        if not is_embedding_configured():
            # embedding 未配置: 只存文本，不向量化
            embeddings = [None] * len(chunks_text)
            print(f"[knowledge-base] embedding 未配置，仅存储文本 (chunks={len(chunks_text)})")
        else:
            # 3. 批量向量化
            try:
                embeddings = await embed_documents(chunks_text)
            except Exception as e:
                # 向量化失败: 降级为仅存储文本（仍可后续搜索时用关键词匹配）
                print(f"[knowledge-base] 向量化失败，降级为仅存储文本: {e}")
                embeddings = [None] * len(chunks_text)

        # 4. 生成文档元数据
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        total_chars = len(text)
        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=title or "未命名文档",
            source_type=source_type,
            source_path=source_path,
            chunk_count=len(chunks_text),
            char_count=total_chars,
        )

        # 5. 存储分块
        ns = self._get_chunks_namespace(namespace)
        docs = self._get_docs_namespace(namespace)

        for i, (chunk_text_val, emb) in enumerate(zip(chunks_text, embeddings)):
            chunk_id = f"chunk_{doc_id}_{i}"
            chunk = KnowledgeChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                chunk_index=i,
                text=chunk_text_val,
                embedding=emb,
            )
            ns[chunk_id] = chunk

            # 持久化
            if self._persistence and self._persistence.is_enabled:
                self._persistence.save_kb_chunk(namespace, chunk.to_dict())

        # 6. 存储文档元数据
        docs[doc_id] = doc
        if self._persistence and self._persistence.is_enabled:
            self._persistence.save_kb_document(namespace, doc.to_dto())

        print(f"[knowledge-base] 文档已添加: '{doc.title}' ({len(chunks_text)} 块, {total_chars} 字, namespace={namespace[:20]}...)")
        return doc

    # ─── 语义搜索 ─────────────────────────────────────

    async def search(
        self,
        namespace: str,
        query: str,
        limit: int = MAX_SEARCH_RESULTS,
    ) -> list[KnowledgeSearchResult]:
        """
        语义搜索: query → embed_query → cosine 相似度 top-K。

        - 只搜索有向量的分块
        - 按分数降序
        - score < 阈值的不返回
        """
        if not is_embedding_configured():
            return []

        if not query or not query.strip():
            return []

        try:
            query_embedding = await embed_query(query)
        except Exception as e:
            print(f"[knowledge-base] embed_query 失败: {e}")
            return []

        ns = self._get_chunks_namespace(namespace)
        docs = self._get_docs_namespace(namespace)

        results: list[KnowledgeSearchResult] = []
        for chunk in ns.values():
            if chunk.embedding is None:
                continue
            score = cosine_similarity(query_embedding, chunk.embedding)
            if score < SCORE_THRESHOLD:
                continue
            doc = docs.get(chunk.doc_id)
            doc_title = doc.title if doc else ""
            results.append(KnowledgeSearchResult(
                chunk=chunk,
                score=score,
                doc_title=doc_title,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # ─── 列出文档 ─────────────────────────────────────

    def list_documents(self, namespace: str) -> list[KnowledgeDocument]:
        """列出 namespace 下所有文档"""
        docs = self._get_docs_namespace(namespace)
        return sorted(docs.values(), key=lambda d: d.created_at, reverse=True)

    # ─── 删除文档 ─────────────────────────────────────

    def delete_document(self, namespace: str, doc_id: str) -> bool:
        """删除文档及其所有分块"""
        ns = self._get_chunks_namespace(namespace)
        docs = self._get_docs_namespace(namespace)

        if doc_id not in docs:
            return False

        # 删除分块
        to_remove = [cid for cid, chunk in ns.items() if chunk.doc_id == doc_id]
        for cid in to_remove:
            del ns[cid]

        # 删除文档元数据
        del docs[doc_id]

        # 持久化删除
        if self._persistence and self._persistence.is_enabled:
            self._persistence.delete_kb_document(namespace, doc_id)
            self._persistence.delete_kb_chunks(namespace, doc_id)

        print(f"[knowledge-base] 文档已删除: {doc_id} (namespace={namespace[:20]}...)")
        return True

    # ─── 统计 ─────────────────────────────────────────

    def get_stats(self, namespace: str) -> dict[str, int]:
        """获取知识库统计"""
        ns = self._get_chunks_namespace(namespace)
        docs = self._get_docs_namespace(namespace)
        return {
            "documents": len(docs),
            "chunks": len(ns),
        }


# ─── 全局单例 ──────────────────────────────────────────

from duckdb_store import get_persistence

kb_store = KnowledgeBaseStore(persistence=get_persistence())


# ─── Namespace 工具 ────────────────────────────────────

def get_kb_namespace(session_id: str) -> str:
    """
    根据 session_id 生成知识库 namespace。

    结构: kb:{session_id}
    与 UserMemory 的 namespace (user-memory:{session_id}) 平行。
    """
    return f"kb:{session_id}"
