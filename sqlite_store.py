"""SQLite 持久化层 — Write-Through Cache 模式

替代 DuckDB 的持久化方案:
- SQLite + WAL 模式: 并发读 + 单写，无文件锁冲突
- Python 内置 sqlite3 模块，零外部依赖
- OLTP 场景正确选型 (DuckDB 是 OLAP 列式库，不适合会话存储)
- 数组类型用 JSON 序列化存储 (SQLite 无原生数组类型)

架构:
  ┌──────────────────────────────────────┐
  │ UserMemoryStore / ThreadStore         │
  │  (内存缓存 + write-through)           │
  │  ┌────────────────────────────────┐  │
  │  │ 内存 dict (快速读取)            │  │
  │  └──────────┬─────────────────────┘  │
  │             │ write-through           │
  │  ┌──────────▼─────────────────────┐  │
  │  │ SQLitePersistence              │  │
  │  │ - save_* / load_* / delete_*   │  │
  │  └──────────────────────────────┘  │
  │  └──────────────────────────────┘  │
  └──────────────────────────────────────┘

配置 (.env):
  DB_PATH=data/pi_agent.db   # 留空则不启用持久化
  DUCKDB_PATH=...            # 向后兼容 (优先使用 DB_PATH)
"""

from __future__ import annotations

import os
import json
import time
import sqlite3
import threading
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ──────────────────────────────────────────────

# 优先 DB_PATH，向后兼容 DUCKDB_PATH
DB_PATH = os.getenv("DB_PATH", "") or os.getenv("DUCKDB_PATH", "").replace(".duckdb", ".db")


# ─── Schema DDL ────────────────────────────────────────

SCHEMA_SQL = """
-- 长期用户记忆表
CREATE TABLE IF NOT EXISTS user_memories (
    namespace TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    text TEXT NOT NULL,
    tags TEXT,              -- JSON array
    polarity TEXT DEFAULT 'neutral',
    status TEXT DEFAULT 'active',
    confidence REAL DEFAULT 0.7,
    source_conversation_id TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    memory_type TEXT DEFAULT 'preference',
    subject TEXT DEFAULT '',
    facet TEXT DEFAULT '',
    semantic TEXT,          -- JSON
    embedding TEXT,         -- JSON array of floats
    created_at REAL,
    updated_at REAL,
    PRIMARY KEY (namespace, stable_key)
);

-- 会话消息表
CREATE TABLE IF NOT EXISTS thread_messages (
    thread_id TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL,
    seq INTEGER,
    PRIMARY KEY (thread_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_thread_messages_seq ON thread_messages(thread_id, seq);

-- 会话状态表 (summary + pinned_decisions)
CREATE TABLE IF NOT EXISTS thread_states (
    thread_id TEXT PRIMARY KEY,
    summary TEXT DEFAULT '',
    pinned_decisions TEXT,  -- JSON array
    last_compacted_at REAL DEFAULT 0,
    messages_count_at_last_compact INTEGER DEFAULT 0,
    updated_at REAL
);

-- 会话元数据表
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    title TEXT DEFAULT '新对话',
    last_active_at REAL,
    has_messages INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_conversations_active ON conversations(session_id, last_active_at DESC);

-- 会话注册表选中状态
CREATE TABLE IF NOT EXISTS session_registries (
    session_id TEXT PRIMARY KEY,
    selected_conversation_id TEXT DEFAULT ''
);

-- 知识库文档元数据
CREATE TABLE IF NOT EXISTS kb_documents (
    namespace TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    source_type TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    chunk_count INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0,
    created_at REAL,
    PRIMARY KEY (namespace, doc_id)
);

-- 知识库文档分块（含向量）
CREATE TABLE IF NOT EXISTS kb_chunks (
    namespace TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    chunk_index INTEGER,
    text TEXT NOT NULL,
    embedding TEXT,         -- JSON array of floats
    created_at REAL,
    PRIMARY KEY (namespace, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(namespace, doc_id);
"""


# ─── SQLitePersistence ────────────────────────────────

class SQLitePersistence:
    """
    SQLite 持久化引擎 — WAL 模式 + 线程安全连接池

    优势 (vs DuckDB):
    - WAL 模式: 并发读不阻塞写，写不阻塞读
    - 无文件锁冲突: uvicorn reload 安全
    - Python 内置: 零外部依赖
    - OLTP 正确选型: 适合会话存储 (DuckDB 是 OLAP 列式库)
    - 生态丰富: 备份/工具/文档充足
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._initialized = False

        if not db_path:
            return

        try:
            db_dir = os.path.dirname(db_path)
            if db_dir and not os.path.isdir(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            # check_same_thread=False: 允许跨线程使用 (配合 _lock)
            self._conn = sqlite3.connect(
                db_path,
                check_same_thread=False,
                timeout=10.0,  # 10 秒等待锁
            )

            # 启用 WAL 模式 (关键: 并发读不阻塞写)
            self._conn.execute("PRAGMA journal_mode=WAL")
            # 正常同步级别 (平衡安全性和性能)
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # 外键约束
            self._conn.execute("PRAGMA foreign_keys=ON")
            # 临时内存表 (性能)
            self._conn.execute("PRAGMA temp_store=MEMORY")

            # 创建表结构
            self._conn.executescript(SCHEMA_SQL)
            self._initialized = True
            print(f"[sqlite] 持久化已启用 (WAL): {db_path}")
        except Exception as e:
            print(f"[sqlite] 初始化失败，降级为纯内存模式: {e}")
            self._conn = None
            self._initialized = False

    @property
    def is_enabled(self) -> bool:
        return self._initialized and self._conn is not None

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ── 工具方法 ──

    @staticmethod
    def _dumps_list(lst: list | None) -> str | None:
        """序列化列表为 JSON 字符串"""
        if lst is None:
            return None
        return json.dumps(lst, ensure_ascii=False)

    @staticmethod
    def _loads_list(s: str | None) -> list | None:
        """反序列化 JSON 字符串为列表"""
        if not s:
            return None
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return None

    # ─── UserMemory 持久化 ─────────────────────────────

    def save_memory(self, namespace: str, memory_data: dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO user_memories
                    (namespace, stable_key, text, tags, polarity, status,
                     confidence, source_conversation_id, reason, memory_type,
                     subject, facet, semantic, embedding, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        namespace,
                        memory_data.get("stableKey", ""),
                        memory_data.get("text", ""),
                        self._dumps_list(memory_data.get("tags", [])),
                        memory_data.get("polarity", "neutral"),
                        memory_data.get("status", "active"),
                        memory_data.get("confidence", 0.7),
                        memory_data.get("sourceConversationId", ""),
                        memory_data.get("reason", ""),
                        memory_data.get("memoryType", "preference"),
                        memory_data.get("subject", ""),
                        memory_data.get("facet", ""),
                        json.dumps(memory_data.get("semantic", {}), ensure_ascii=False),
                        self._dumps_list(memory_data.get("embedding")),
                        memory_data.get("createdAt", time.time()),
                        memory_data.get("updatedAt", time.time()),
                    ],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_memory 失败: {e}")

    def load_memories(self, namespace: str) -> list[dict[str, Any]]:
        if not self.is_enabled:
            return []
        try:
            rows = self._conn.execute(
                """SELECT stable_key, text, tags, polarity, status,
                          confidence, source_conversation_id, reason, memory_type,
                          subject, facet, semantic, embedding, created_at, updated_at
                   FROM user_memories WHERE namespace = ?""",
                [namespace],
            ).fetchall()
            result = []
            for row in rows:
                semantic_raw = row[11]
                semantic = self._loads_list(semantic_raw) or {}
                if isinstance(semantic, list):
                    semantic = {}
                result.append({
                    "stableKey": row[0],
                    "text": row[1],
                    "tags": self._loads_list(row[2]) or [],
                    "polarity": row[3],
                    "status": row[4],
                    "confidence": row[5],
                    "sourceConversationId": row[6],
                    "reason": row[7],
                    "memoryType": row[8],
                    "subject": row[9],
                    "facet": row[10],
                    "semantic": semantic,
                    "embedding": self._loads_list(row[12]),
                    "createdAt": row[13],
                    "updatedAt": row[14],
                })
            return result
        except Exception as e:
            print(f"[sqlite] load_memories 失败: {e}")
            return []

    def delete_memory(self, namespace: str, key: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM user_memories WHERE namespace = ? AND stable_key = ?",
                    [namespace, key],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_memory 失败: {e}")

    def delete_namespace_memories(self, namespace: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute("DELETE FROM user_memories WHERE namespace = ?", [namespace])
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_namespace_memories 失败: {e}")

    # ─── ThreadState 持久化 ────────────────────────────

    def save_thread_state(
        self, thread_id: str, summary: str, pinned_decisions: list[str],
        last_compacted_at: float, messages_count_at_last_compact: int,
    ) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO thread_states
                    (thread_id, summary, pinned_decisions,
                     last_compacted_at, messages_count_at_last_compact, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    [thread_id, summary, self._dumps_list(pinned_decisions),
                     last_compacted_at, messages_count_at_last_compact, time.time()],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_thread_state 失败: {e}")

    def load_thread_state(self, thread_id: str) -> dict[str, Any] | None:
        if not self.is_enabled:
            return None
        try:
            rows = self._conn.execute(
                """SELECT summary, pinned_decisions, last_compacted_at, messages_count_at_last_compact
                   FROM thread_states WHERE thread_id = ?""",
                [thread_id],
            ).fetchall()
            if not rows:
                return None
            row = rows[0]
            return {
                "summary": row[0] or "",
                "pinned_decisions": self._loads_list(row[1]) or [],
                "last_compacted_at": row[2] or 0.0,
                "messages_count_at_last_compact": row[3] or 0,
            }
        except Exception as e:
            print(f"[sqlite] load_thread_state 失败: {e}")
            return None

    def delete_thread_state(self, thread_id: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute("DELETE FROM thread_states WHERE thread_id = ?", [thread_id])
                self._conn.execute("DELETE FROM thread_messages WHERE thread_id = ?", [thread_id])
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_thread_state 失败: {e}")

    # ─── ThreadMessage 持久化 ──────────────────────────

    def save_thread_messages(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute("DELETE FROM thread_messages WHERE thread_id = ?", [thread_id])
                for seq, msg in enumerate(messages):
                    self._conn.execute(
                        """INSERT INTO thread_messages
                        (thread_id, msg_id, role, text, created_at, seq)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        [thread_id, msg.get("id", ""), msg.get("role", "user"),
                         msg.get("text", ""), msg.get("created_at", time.time()), seq],
                    )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_thread_messages 失败: {e}")

    def load_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        if not self.is_enabled:
            return []
        try:
            rows = self._conn.execute(
                """SELECT msg_id, role, text, created_at
                   FROM thread_messages WHERE thread_id = ? ORDER BY seq""",
                [thread_id],
            ).fetchall()
            return [{"id": r[0], "role": r[1], "text": r[2], "created_at": r[3]} for r in rows]
        except Exception as e:
            print(f"[sqlite] load_thread_messages 失败: {e}")
            return []

    # ─── Conversation 持久化 ───────────────────────────

    def save_conversation(
        self, session_id: str, conversation_id: str, thread_id: str,
        title: str, last_active_at: float, has_messages: bool,
    ) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO conversations
                    (session_id, conversation_id, thread_id, title, last_active_at, has_messages)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    [session_id, conversation_id, thread_id, title, last_active_at, 1 if has_messages else 0],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_conversation 失败: {e}")

    def load_conversations(self, session_id: str) -> list[dict[str, Any]]:
        if not self.is_enabled:
            return []
        try:
            rows = self._conn.execute(
                """SELECT conversation_id, thread_id, title, last_active_at, has_messages
                   FROM conversations WHERE session_id = ? ORDER BY last_active_at DESC""",
                [session_id],
            ).fetchall()
            return [
                {"conversation_id": r[0], "thread_id": r[1], "title": r[2],
                 "last_active_at": r[3], "has_messages": bool(r[4])}
                for r in rows
            ]
        except Exception as e:
            print(f"[sqlite] load_conversations 失败: {e}")
            return []

    def delete_conversation(self, session_id: str, conversation_id: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM conversations WHERE session_id = ? AND conversation_id = ?",
                    [session_id, conversation_id],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_conversation 失败: {e}")

    # ─── SessionRegistry 持久化 ────────────────────────

    def save_session_registry(self, session_id: str, selected_conversation_id: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO session_registries
                    (session_id, selected_conversation_id) VALUES (?, ?)""",
                    [session_id, selected_conversation_id],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_session_registry 失败: {e}")

    def load_session_registry(self, session_id: str) -> str | None:
        if not self.is_enabled:
            return None
        try:
            rows = self._conn.execute(
                "SELECT selected_conversation_id FROM session_registries WHERE session_id = ?",
                [session_id],
            ).fetchall()
            if not rows:
                return None
            return rows[0][0] or ""
        except Exception as e:
            print(f"[sqlite] load_session_registry 失败: {e}")
            return None

    # ─── 统计 / 维护 ───────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        if not self.is_enabled:
            return {}
        try:
            stats = {}
            for table in ["user_memories", "thread_messages", "thread_states",
                          "conversations", "session_registries", "kb_documents", "kb_chunks"]:
                rows = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                stats[table] = rows[0] if rows else 0
            return stats
        except Exception as e:
            print(f"[sqlite] get_stats 失败: {e}")
            return {}

    def vacuum(self) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute("VACUUM")
        except Exception as e:
            print(f"[sqlite] vacuum 失败: {e}")

    # ─── KnowledgeBase 持久化 ─────────────────────────

    def save_kb_document(self, namespace: str, doc_data: dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO kb_documents
                    (namespace, doc_id, title, source_type, source_path,
                     chunk_count, char_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [namespace, doc_data.get("doc_id", ""), doc_data.get("title", ""),
                     doc_data.get("source_type", ""), doc_data.get("source_path", ""),
                     doc_data.get("chunk_count", 0), doc_data.get("char_count", 0),
                     doc_data.get("created_at", time.time())],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_kb_document 失败: {e}")

    def load_kb_documents(self, namespace: str) -> list[dict[str, Any]]:
        if not self.is_enabled:
            return []
        try:
            rows = self._conn.execute(
                """SELECT doc_id, title, source_type, source_path,
                          chunk_count, char_count, created_at
                   FROM kb_documents WHERE namespace = ? ORDER BY created_at DESC""",
                [namespace],
            ).fetchall()
            return [
                {"doc_id": r[0], "title": r[1], "source_type": r[2], "source_path": r[3],
                 "chunk_count": r[4], "char_count": r[5], "created_at": r[6]}
                for r in rows
            ]
        except Exception as e:
            print(f"[sqlite] load_kb_documents 失败: {e}")
            return []

    def delete_kb_document(self, namespace: str, doc_id: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM kb_documents WHERE namespace = ? AND doc_id = ?",
                    [namespace, doc_id],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_kb_document 失败: {e}")

    def save_kb_chunk(self, namespace: str, chunk_data: dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO kb_chunks
                    (namespace, doc_id, chunk_id, chunk_index, text, embedding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [namespace, chunk_data.get("doc_id", ""), chunk_data.get("chunk_id", ""),
                     chunk_data.get("chunk_index", 0), chunk_data.get("text", ""),
                     self._dumps_list(chunk_data.get("embedding")),
                     chunk_data.get("created_at", time.time())],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] save_kb_chunk 失败: {e}")

    def load_kb_chunks(self, namespace: str) -> list[dict[str, Any]]:
        if not self.is_enabled:
            return []
        try:
            rows = self._conn.execute(
                """SELECT doc_id, chunk_id, chunk_index, text, embedding, created_at
                   FROM kb_chunks WHERE namespace = ?""",
                [namespace],
            ).fetchall()
            return [
                {"doc_id": r[0], "chunk_id": r[1], "chunk_index": r[2], "text": r[3],
                 "embedding": self._loads_list(r[4]), "created_at": r[5]}
                for r in rows
            ]
        except Exception as e:
            print(f"[sqlite] load_kb_chunks 失败: {e}")
            return []

    def delete_kb_chunks(self, namespace: str, doc_id: str) -> None:
        if not self.is_enabled:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM kb_chunks WHERE namespace = ? AND doc_id = ?",
                    [namespace, doc_id],
                )
                self._conn.commit()
        except Exception as e:
            print(f"[sqlite] delete_kb_chunks 失败: {e}")

    def get_kb_stats(self, namespace: str) -> dict[str, int]:
        if not self.is_enabled:
            return {}
        try:
            doc_count = self._conn.execute(
                "SELECT COUNT(*) FROM kb_documents WHERE namespace = ?", [namespace],
            ).fetchone()
            chunk_count = self._conn.execute(
                "SELECT COUNT(*) FROM kb_chunks WHERE namespace = ?", [namespace],
            ).fetchone()
            return {"documents": doc_count[0] if doc_count else 0,
                    "chunks": chunk_count[0] if chunk_count else 0}
        except Exception as e:
            print(f"[sqlite] get_kb_stats 失败: {e}")
            return {}


# ─── 全局单例 ──────────────────────────────────────────

_persistence: SQLitePersistence | None = None


def get_persistence() -> SQLitePersistence:
    global _persistence
    if _persistence is None:
        _persistence = SQLitePersistence(DB_PATH)
    return _persistence


def reset_persistence() -> None:
    global _persistence
    if _persistence is not None:
        _persistence.close()
    _persistence = None
