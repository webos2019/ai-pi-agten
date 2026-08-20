"""Token 用量持久化存储与统计

功能:
- 记录每次 LLM 对话的 token 消耗 (prompt / completion / total)
- 按日期维度聚合统计 (今日、历史趋势)
- 按 session_id 维度隔离 (多公司/工作区)
- 复用 sqlite_store 的 SQLitePersistence 引擎 (WAL 模式)

架构:
  chat_orchestrator → token_store.record_usage()
                        ↓
                    SQLitePersistence (token_usage_log 表)
                        ↓
  app.py /api/token-usage/* → token_store.get_today_stats() / get_history()
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlite_store import get_persistence


# ─── Schema DDL (追加到 SQLitePersistence 的 SCHEMA_SQL) ───

TOKEN_SCHEMA_SQL = """
-- Token 用量日志表 — 每次对话一条记录
CREATE TABLE IF NOT EXISTS token_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- YYYY-MM-DD (UTC+8)
    timestamp REAL NOT NULL,         -- Unix timestamp
    session_id TEXT DEFAULT '',      -- 浏览器会话 ID (多公司隔离)
    conversation_id TEXT DEFAULT '', -- 会话 ID
    model TEXT DEFAULT '',           -- 使用的模型名
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_token_date ON token_usage_log(date);
CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage_log(session_id, date);
CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage_log(timestamp);
"""


# ─── TokenStore ────────────────────────────────────────

class TokenStore:
    """Token 用量存储 — 线程安全，复用 SQLitePersistence

    用法:
        store = get_token_store()
        store.record_usage(prompt=1000, completion=500, total=1500)
        today = store.get_today_stats()
        history = store.get_history(days=7)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """确保 token_usage_log 表存在"""
        persistence = get_persistence()
        if not persistence.is_enabled:
            return
        try:
            persistence._conn.executescript(TOKEN_SCHEMA_SQL)
            persistence._conn.commit()
            print("[token_store] token_usage_log 表已就绪")
        except Exception as e:
            print(f"[token_store] 建表失败: {e}")

    @staticmethod
    def _today_date_str() -> str:
        """获取今天的日期字符串 (UTC+8)"""
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).strftime("%Y-%m-%d")

    @staticmethod
    def _date_str(ts: float) -> str:
        """将 Unix timestamp 转为日期字符串 (UTC+8)"""
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")

    def record_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        session_id: str = "",
        conversation_id: str = "",
        model: str = "",
    ) -> None:
        """记录一次对话的 token 用量

        Args:
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            total_tokens: 总 token 数
            session_id: 浏览器会话 ID (多公司隔离)
            conversation_id: 对话 ID
            model: 使用的模型名
        """
        persistence = get_persistence()
        if not persistence.is_enabled:
            return

        now = time.time()
        date_str = self._date_str(now)

        try:
            with self._lock:
                persistence._conn.execute(
                    """INSERT INTO token_usage_log
                    (date, timestamp, session_id, conversation_id, model,
                     prompt_tokens, completion_tokens, total_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [date_str, now, session_id, conversation_id, model,
                     prompt_tokens, completion_tokens, total_tokens],
                )
                persistence._conn.commit()
        except Exception as e:
            print(f"[token_store] record_usage 失败: {e}")

    def get_today_stats(self, session_id: str = "") -> dict[str, Any]:
        """获取今日 token 用量统计

        Args:
            session_id: 传入时只统计该会话，不传则统计全局

        Returns:
            {
                "date": "2026-08-13",
                "promptTokens": 12345,
                "completionTokens": 6789,
                "totalTokens": 19134,
                "requestCount": 5,
            }
        """
        persistence = get_persistence()
        if not persistence.is_enabled:
            return self._empty_stats()

        date_str = self._today_date_str()

        try:
            if session_id:
                row = persistence._conn.execute(
                    """SELECT
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log
                    WHERE date = ? AND session_id = ?""",
                    [date_str, session_id],
                ).fetchone()
            else:
                row = persistence._conn.execute(
                    """SELECT
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log
                    WHERE date = ?""",
                    [date_str],
                ).fetchone()

            return {
                "date": date_str,
                "promptTokens": row[0] if row else 0,
                "completionTokens": row[1] if row else 0,
                "totalTokens": row[2] if row else 0,
                "requestCount": row[3] if row else 0,
            }
        except Exception as e:
            print(f"[token_store] get_today_stats 失败: {e}")
            return self._empty_stats()

    def get_history(self, days: int = 7, session_id: str = "") -> list[dict[str, Any]]:
        """获取最近 N 天的 token 用量历史

        Args:
            days: 天数 (默认 7 天)
            session_id: 传入时只统计该会话

        Returns:
            [
                {"date": "2026-08-13", "promptTokens": ..., "completionTokens": ..., "totalTokens": ..., "requestCount": ...},
                ...
            ]
        """
        persistence = get_persistence()
        if not persistence.is_enabled:
            return []

        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        start_date = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        try:
            if session_id:
                rows = persistence._conn.execute(
                    """SELECT
                        date,
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log
                    WHERE date >= ? AND session_id = ?
                    GROUP BY date
                    ORDER BY date ASC""",
                    [start_date, session_id],
                ).fetchall()
            else:
                rows = persistence._conn.execute(
                    """SELECT
                        date,
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log
                    WHERE date >= ?
                    GROUP BY date
                    ORDER BY date ASC""",
                    [start_date],
                ).fetchall()

            # 填充没有数据的日期
            result: list[dict[str, Any]] = []
            date_map: dict[str, dict[str, Any]] = {}
            for row in rows:
                date_map[row[0]] = {
                    "date": row[0],
                    "promptTokens": row[1],
                    "completionTokens": row[2],
                    "totalTokens": row[3],
                    "requestCount": row[4],
                }

            for i in range(days):
                d = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
                if d in date_map:
                    result.append(date_map[d])
                else:
                    result.append({
                        "date": d,
                        "promptTokens": 0,
                        "completionTokens": 0,
                        "totalTokens": 0,
                        "requestCount": 0,
                    })

            return result
        except Exception as e:
            print(f"[token_store] get_history 失败: {e}")
            return []

    def get_recent_records(self, limit: int = 20, session_id: str = "") -> list[dict[str, Any]]:
        """获取最近的 token 用量明细记录

        Args:
            limit: 最多返回条数
            session_id: 传入时只返回该会话的记录

        Returns:
            [{"timestamp": ..., "sessionId": ..., "model": ..., "promptTokens": ..., ...}, ...]
        """
        persistence = get_persistence()
        if not persistence.is_enabled:
            return []

        try:
            if session_id:
                rows = persistence._conn.execute(
                    """SELECT timestamp, session_id, conversation_id, model,
                              prompt_tokens, completion_tokens, total_tokens
                    FROM token_usage_log
                    WHERE session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                    [session_id, limit],
                ).fetchall()
            else:
                rows = persistence._conn.execute(
                    """SELECT timestamp, session_id, conversation_id, model,
                              prompt_tokens, completion_tokens, total_tokens
                    FROM token_usage_log
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                    [limit],
                ).fetchall()

            return [
                {
                    "timestamp": row[0],
                    "sessionId": row[1],
                    "conversationId": row[2],
                    "model": row[3],
                    "promptTokens": row[4],
                    "completionTokens": row[5],
                    "totalTokens": row[6],
                }
                for row in rows
            ]
        except Exception as e:
            print(f"[token_store] get_recent_records 失败: {e}")
            return []

    def get_total_stats(self, session_id: str = "") -> dict[str, Any]:
        """获取全部历史的 token 用量汇总

        Returns:
            {"totalTokens": ..., "promptTokens": ..., "completionTokens": ..., "requestCount": ...}
        """
        persistence = get_persistence()
        if not persistence.is_enabled:
            return self._empty_total()

        try:
            if session_id:
                row = persistence._conn.execute(
                    """SELECT
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log
                    WHERE session_id = ?""",
                    [session_id],
                ).fetchone()
            else:
                row = persistence._conn.execute(
                    """SELECT
                        COALESCE(SUM(prompt_tokens), 0),
                        COALESCE(SUM(completion_tokens), 0),
                        COALESCE(SUM(total_tokens), 0),
                        COUNT(*)
                    FROM token_usage_log""",
                ).fetchone()

            return {
                "promptTokens": row[0] if row else 0,
                "completionTokens": row[1] if row else 0,
                "totalTokens": row[2] if row else 0,
                "requestCount": row[3] if row else 0,
            }
        except Exception as e:
            print(f"[token_store] get_total_stats 失败: {e}")
            return self._empty_total()

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        tz = timezone(timedelta(hours=8))
        return {
            "date": datetime.now(tz).strftime("%Y-%m-%d"),
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "requestCount": 0,
        }

    @staticmethod
    def _empty_total() -> dict[str, Any]:
        return {
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "requestCount": 0,
        }


# ─── 全局单例 ──────────────────────────────────────────

_token_store: TokenStore | None = None
_token_store_lock = threading.Lock()


def get_token_store() -> TokenStore:
    """获取全局 TokenStore 实例"""
    global _token_store
    if _token_store is None:
        with _token_store_lock:
            if _token_store is None:
                _token_store = TokenStore()
    return _token_store
