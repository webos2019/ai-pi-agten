"""持久化层 — 向后兼容入口

已从 DuckDB 迁移到 SQLite + WAL 模式:
- DuckDB 是 OLAP 列式库，不适合会话存储 (OLTP)
- SQLite WAL 模式并发更好，无文件锁冲突
- Python 内置 sqlite3，零外部依赖

所有现有代码 `from duckdb_store import DuckDBPersistence, get_persistence` 无需修改，
自动使用 SQLite 实现。

如需直接使用 SQLite 实现: from sqlite_store import SQLitePersistence
"""

# 重导出 SQLite 实现，保持向后兼容
from sqlite_store import (
    SQLitePersistence,
    DB_PATH,
    get_persistence,
    reset_persistence,
)

# 向后兼容别名
DuckDBPersistence = SQLitePersistence


__all__ = [
    "DuckDBPersistence",   # 向后兼容别名 → SQLitePersistence
    "SQLitePersistence",   # 新名称
    "DB_PATH",
    "get_persistence",
    "reset_persistence",
]
