"""
DuckDB 持久化层 — Write-Through Cache 模式

设计要点:
- DuckDB 嵌入式列式数据库，零服务端配置，单文件存储
- 原生 DOUBLE[] 列存向量（64位精度），VARCHAR[] 存 list，JSON 存元数据
- Write-Through: 内存缓存优先读，写入时同步落盘
- 降级策略: DuckDB 不可用时降级为纯内存模式（不影响业务）
- 线程安全: 单连接 + threading.Lock 保护写操作

架构:
  ┌──────────────────────────────────────┐
  │ UserMemoryStore / ThreadStore         │
  │  (内存缓存 + write-through)           │
  │  ┌────────────────────────────────┐  │
  │  │ 内存 dict (快速读取)            │  │
  │  └──────────┬─────────────────────┘  │
  │             │ write-through           │
  │  ┌──────────▼─────────────────────┐  │
  │  │ DuckDBPersistence              │  │
  │  │ - save_* / load_* / delete_*   │  │
  │  └──────────────────────────────┘  │
  └──────────────────────────────────────┘

表结构:
  user_memories      — 长期用户记忆 (含 DOUBLE[] 向量列)
  thread_messages    — 会话消息 (按 thread_id 分组)
  thread_states      — 会话状态 (summary + pinned_decisions)
  conversations      — 会话元数据
  session_registries — 会话注册表选中状态

配置 (.env):
  DUCKDB_PATH=data/pi_agent.duckdb   # 留空则不启用持久化
"""

from __future__ import annotations

import os
import json
import time
import threading
from typing import Any

import duckdb
from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ──────────────────────────────────────────────

DUCKDB_PATH = os.getenv("DUCKDB_PATH", "")

# ─── Schema DDL ────────────────────────────────────────

SCHEMA_SQL = """
-- 长期用户记忆表
CREATE TABLE IF NOT EXISTS user_memories (
    namespace VARCHAR NOT NULL,
    stable_key VARCHAR NOT NULL,
    text VARCHAR NOT NULL,
    tags VARCHAR[],
    polarity VARCHAR DEFAULT 'neutral',
    status VARCHAR DEFAULT 'active',
    confidence DOUBLE DEFAULT 0.7,
    source_conversation_id VARCHAR DEFAULT '',
    reason VARCHAR DEFAULT '',
    memory_type VARCHAR DEFAULT 'preference',
    subject VARCHAR DEFAULT '',
    facet VARCHAR DEFAULT '',
    semantic JSON,
    embedding DOUBLE[],
    created_at DOUBLE,
    updated_at DOUBLE,
    PRIMARY KEY (namespace, stable_key)
);

-- 会话消息表
CREATE TABLE IF NOT EXISTS thread_messages (
    thread_id VARCHAR NOT NULL,
    msg_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    text VARCHAR NOT NULL,
    created_at DOUBLE,
    seq INTEGER,
    PRIMARY KEY (thread_id, msg_id)
);

-- 会话状态表 (summary + pinned_decisions)
CREATE TABLE IF NOT EXISTS thread_states (
    thread_id VARCHAR PRIMARY KEY,
    summary VARCHAR DEFAULT '',
    pinned_decisions VARCHAR[],
    last_compacted_at DOUBLE DEFAULT 0,
    messages_count_at_last_compact INTEGER DEFAULT 0,
    updated_at DOUBLE
);

-- 会话元数据表
CREATE TABLE IF NOT EXISTS conversations (
    session_id VARCHAR NOT NULL,
    conversation_id VARCHAR NOT NULL,
    thread_id VARCHAR NOT NULL,
    title VARCHAR DEFAULT '新对话',
    last_active_at DOUBLE,
    has_messages BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (session_id, conversation_id)
);

-- 会话注册表选中状态
CREATE TABLE IF NOT EXISTS session_registries (
    session_id VARCHAR PRIMARY KEY,
    selected_conversation_id VARCHAR DEFAULT ''
);
"""


# ─── DuckDBPersistence ────────────────────────────────


class DuckDBPersistence:
    """
    DuckDB 持久化引擎 — 单连接 + 线程锁。

    - 嵌入式数据库，无需额外服务
    - 原生 DOUBLE[] / VARCHAR[] / JSON 类型
    - 所有写操作加锁，读操作不加锁（DuckDB MVCC 支持）
    - 任何异常只 log，不抛出（降级为纯内存模式）
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()
        self._initialized = False

        if not db_path:
            return

        try:
            # 确保目录存在
            db_dir = os.path.dirname(db_path)
            if db_dir and not os.path.isdir(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            self._conn = duckdb.connect(db_path)
            self._conn.execute(SCHEMA_SQL)
            self._initialized = True
            print(f"[duckdb] 持久化已启用: {db_path}")
        except Exception as e:
            print(f"[duckdb] 初始化失败，降级为纯内存模式: {e}")
            self._conn = None
            self._initialized = False

    @property
    def is_enabled(self) -> bool:
        """是否启用持久化"""
        return self._initialized and self._conn is not None

    def close(self) -> None:
        """关闭连接"""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ─── UserMemory 持久化 ─────────────────────────────

    def save_memory(self, namespace: str, memory_data: dict[str, Any]) -> None:
        """
        持久化一条 UserMemory。

        memory_data 格式 = UserMemory.to_stored_dict() + embedding 字段
        """
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO user_memories
                    (namespace, stable_key, text, tags, polarity, status,
                     confidence, source_conversation_id, reason, memory_type,
                     subject, facet, semantic, embedding, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        namespace,
                        memory_data.get("stableKey", ""),
                        memory_data.get("text", ""),
                        memory_data.get("tags", []),
                        memory_data.get("polarity", "neutral"),
                        memory_data.get("status", "active"),
                        memory_data.get("confidence", 0.7),
                        memory_data.get("sourceConversationId", ""),
                        memory_data.get("reason", ""),
                        memory_data.get("memoryType", "preference"),
                        memory_data.get("subject", ""),
                        memory_data.get("facet", ""),
                        json.dumps(memory_data.get("semantic", {})),
                        memory_data.get("embedding"),  # DOUBLE[] 或 None
                        memory_data.get("createdAt", time.time()),
                        memory_data.get("updatedAt", time.time()),
                    ],
                )
        except Exception as e:
            print(f"[duckdb] save_memory 失败: {e}")

    def load_memories(self, namespace: str) -> list[dict[str, Any]]:
        """加载 namespace 下所有记忆（含 embedding）"""
        if not self.is_enabled:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT stable_key, text, tags, polarity, status,
                       confidence, source_conversation_id, reason, memory_type,
                       subject, facet, semantic, embedding, created_at, updated_at
                FROM user_memories
                WHERE namespace = ?
                """,
                [namespace],
            ).fetchall()

            result = []
            for row in rows:
                semantic_raw = row[11]
                semantic = (
                    json.loads(semantic_raw)
                    if isinstance(semantic_raw, str)
                    else (semantic_raw if isinstance(semantic_raw, dict) else {})
                )
                result.append({
                    "stableKey": row[0],
                    "text": row[1],
                    "tags": list(row[2]) if row[2] else [],
                    "polarity": row[3],
                    "status": row[4],
                    "confidence": row[5],
                    "sourceConversationId": row[6],
                    "reason": row[7],
                    "memoryType": row[8],
                    "subject": row[9],
                    "facet": row[10],
                    "semantic": semantic,
                    "embedding": list(row[12]) if row[12] else None,
                    "createdAt": row[13],
                    "updatedAt": row[14],
                })
            return result
        except Exception as e:
            print(f"[duckdb] load_memories 失败: {e}")
            return []

    def delete_memory(self, namespace: str, key: str) -> None:
        """删除一条记忆"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM user_memories WHERE namespace = ? AND stable_key = ?",
                    [namespace, key],
                )
        except Exception as e:
            print(f"[duckdb] delete_memory 失败: {e}")

    def delete_namespace_memories(self, namespace: str) -> None:
        """删除 namespace 下所有记忆"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM user_memories WHERE namespace = ?",
                    [namespace],
                )
        except Exception as e:
            print(f"[duckdb] delete_namespace_memories 失败: {e}")

    # ─── ThreadState 持久化 ────────────────────────────

    def save_thread_state(
        self,
        thread_id: str,
        summary: str,
        pinned_decisions: list[str],
        last_compacted_at: float,
        messages_count_at_last_compact: int,
    ) -> None:
        """持久化 ThreadState 的 summary/pinned 部分"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO thread_states
                    (thread_id, summary, pinned_decisions,
                     last_compacted_at, messages_count_at_last_compact, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        thread_id,
                        summary,
                        pinned_decisions,
                        last_compacted_at,
                        messages_count_at_last_compact,
                        time.time(),
                    ],
                )
        except Exception as e:
            print(f"[duckdb] save_thread_state 失败: {e}")

    def load_thread_state(self, thread_id: str) -> dict[str, Any] | None:
        """加载 ThreadState 的 summary/pinned 部分"""
        if not self.is_enabled:
            return None

        try:
            rows = self._conn.execute(
                """
                SELECT summary, pinned_decisions,
                       last_compacted_at, messages_count_at_last_compact
                FROM thread_states
                WHERE thread_id = ?
                """,
                [thread_id],
            ).fetchall()

            if not rows:
                return None

            row = rows[0]
            return {
                "summary": row[0] or "",
                "pinned_decisions": list(row[1]) if row[1] else [],
                "last_compacted_at": row[2] or 0.0,
                "messages_count_at_last_compact": row[3] or 0,
            }
        except Exception as e:
            print(f"[duckdb] load_thread_state 失败: {e}")
            return None

    def delete_thread_state(self, thread_id: str) -> None:
        """删除 ThreadState 及其所有消息"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM thread_states WHERE thread_id = ?",
                    [thread_id],
                )
                self._conn.execute(
                    "DELETE FROM thread_messages WHERE thread_id = ?",
                    [thread_id],
                )
        except Exception as e:
            print(f"[duckdb] delete_thread_state 失败: {e}")

    # ─── ThreadMessage 持久化 ──────────────────────────

    def save_thread_messages(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """
        全量覆盖保存会话消息（先删后插）。

        messages 格式: [{id, role, text, created_at}, ...]
        """
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM thread_messages WHERE thread_id = ?",
                    [thread_id],
                )
                for seq, msg in enumerate(messages):
                    self._conn.execute(
                        """
                        INSERT INTO thread_messages
                        (thread_id, msg_id, role, text, created_at, seq)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [
                            thread_id,
                            msg.get("id", ""),
                            msg.get("role", "user"),
                            msg.get("text", ""),
                            msg.get("created_at", time.time()),
                            seq,
                        ],
                    )
        except Exception as e:
            print(f"[duckdb] save_thread_messages 失败: {e}")

    def load_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """加载会话消息（按 seq 排序）"""
        if not self.is_enabled:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT msg_id, role, text, created_at
                FROM thread_messages
                WHERE thread_id = ?
                ORDER BY seq
                """,
                [thread_id],
            ).fetchall()

            return [
                {
                    "id": row[0],
                    "role": row[1],
                    "text": row[2],
                    "created_at": row[3],
                }
                for row in rows
            ]
        except Exception as e:
            print(f"[duckdb] load_thread_messages 失败: {e}")
            return []

    # ─── Conversation 持久化 ───────────────────────────

    def save_conversation(
        self,
        session_id: str,
        conversation_id: str,
        thread_id: str,
        title: str,
        last_active_at: float,
        has_messages: bool,
    ) -> None:
        """持久化/更新一条会话元数据"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO conversations
                    (session_id, conversation_id, thread_id,
                     title, last_active_at, has_messages)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        session_id,
                        conversation_id,
                        thread_id,
                        title,
                        last_active_at,
                        has_messages,
                    ],
                )
        except Exception as e:
            print(f"[duckdb] save_conversation 失败: {e}")

    def load_conversations(self, session_id: str) -> list[dict[str, Any]]:
        """加载 session 下所有会话（按 last_active_at 倒序）"""
        if not self.is_enabled:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT conversation_id, thread_id, title,
                       last_active_at, has_messages
                FROM conversations
                WHERE session_id = ?
                ORDER BY last_active_at DESC
                """,
                [session_id],
            ).fetchall()

            return [
                {
                    "conversation_id": row[0],
                    "thread_id": row[1],
                    "title": row[2],
                    "last_active_at": row[3],
                    "has_messages": row[4],
                }
                for row in rows
            ]
        except Exception as e:
            print(f"[duckdb] load_conversations 失败: {e}")
            return []

    def delete_conversation(self, session_id: str, conversation_id: str) -> None:
        """删除一条会话元数据"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM conversations WHERE session_id = ? AND conversation_id = ?",
                    [session_id, conversation_id],
                )
        except Exception as e:
            print(f"[duckdb] delete_conversation 失败: {e}")

    # ─── SessionRegistry 持久化 ────────────────────────

    def save_session_registry(
        self,
        session_id: str,
        selected_conversation_id: str,
    ) -> None:
        """持久化会话注册表的选中状态"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO session_registries
                    (session_id, selected_conversation_id)
                    VALUES (?, ?)
                    """,
                    [session_id, selected_conversation_id],
                )
        except Exception as e:
            print(f"[duckdb] save_session_registry 失败: {e}")

    def load_session_registry(self, session_id: str) -> str | None:
        """加载会话注册表的选中状态"""
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
            print(f"[duckdb] load_session_registry 失败: {e}")
            return None

    # ─── 统计 / 维护 ───────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """返回各表行数统计"""
        if not self.is_enabled:
            return {}

        try:
            stats = {}
            for table in [
                "user_memories", "thread_messages", "thread_states",
                "conversations", "session_registries",
            ]:
                rows = self._conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()
                stats[table] = rows[0] if rows else 0
            return stats
        except Exception as e:
            print(f"[duckdb] get_stats 失败: {e}")
            return {}

    def vacuum(self) -> None:
        """压缩数据库文件（VACUUM）"""
        if not self.is_enabled:
            return

        try:
            with self._lock:
                self._conn.execute("VACUUM")
        except Exception as e:
            print(f"[duckdb] vacuum 失败: {e}")


# ─── 全局单例 ──────────────────────────────────────────

_persistence: DuckDBPersistence | None = None


def get_persistence() -> DuckDBPersistence:
    """获取全局 DuckDB 持久化单例"""
    global _persistence
    if _persistence is None:
        _persistence = DuckDBPersistence(DUCKDB_PATH)
    return _persistence


def reset_persistence() -> None:
    """重置单例（主要用于测试）"""
    global _persistence
    if _persistence is not None:
        _persistence.close()
    _persistence = None
