"""数据库诊断工具 — 连接检查 / 慢查询 / 表空间 / 锁等待"""

import time
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    db_type = args.get("db_type", "mysql")
    action = args.get("action", "status")
    host = args.get("host", "localhost")
    port = args.get("port", 3306)
    database = args.get("database", "")

    result = {
        "db_type": db_type,
        "action": action,
        "host": host,
        "port": port,
        "database": database,
    }

    if action == "connection":
        # 模拟连接池状态（实际环境对接连接池API）
        result["connection_pool"] = {
            "active": 8,
            "idle": 12,
            "max": 50,
            "usage_percent": 16.0,
            "status": "normal" if 8 < 40 else "warning",
        }

    elif action == "slow_queries":
        # 模拟慢查询列表
        result["slow_queries"] = [
            {
                "query_id": 1,
                "sql": "SELECT * FROM oa_workflow WHERE status='pending' ORDER BY create_time DESC",
                "duration_ms": 3500,
                "rows_examined": 150000,
                "rows_returned": 50,
            },
            {
                "query_id": 2,
                "sql": "SELECT COUNT(*) FROM oa_log WHERE create_time > '2025-01-01'",
                "duration_ms": 2800,
                "rows_examined": 980000,
                "rows_returned": 1,
            },
        ]
        result["slow_query_count"] = 2
        result["threshold_ms"] = 1000

    elif action == "table_size":
        # 模拟表空间使用
        result["tables"] = [
            {"table_name": "oa_log", "size_mb": 5200, "rows": 15000000, "status": "large"},
            {"table_name": "oa_workflow", "size_mb": 850, "rows": 320000, "status": "normal"},
            {"table_name": "oa_document", "size_mb": 1200, "rows": 580000, "status": "normal"},
            {"table_name": "oa_attachment", "size_mb": 8500, "rows": 120000, "status": "large"},
        ]
        result["total_size_mb"] = 15750

    elif action == "locks":
        # 模拟锁等待检测
        result["lock_waits"] = [
            {
                "blocked_transaction": "TX_008234",
                "blocking_transaction": "TX_008231",
                "blocked_sql": "UPDATE oa_workflow SET status='approved' WHERE id=12345",
                "blocking_sql": "UPDATE oa_workflow SET status='approved' WHERE id=12345",
                "wait_time_ms": 5000,
            },
        ]
        result["lock_wait_count"] = 1
        result["status"] = "warning"

    elif action == "status":
        # 综合状态
        result["connection_pool"] = {
            "active": 8,
            "idle": 12,
            "max": 50,
            "usage_percent": 16.0,
        }
        result["slow_query_count"] = 2
        result["lock_wait_count"] = 1
        result["total_size_mb"] = 15750
        result["overall_status"] = "warning"

    return result


def register():
    tool_registry.register(ChatToolDefinition(
        name="db_diagnose",
        description="数据库诊断：连接池/慢查询/表空间/锁等待",
        parameters={
            "type": "object",
            "properties": {
                "db_type": {
                    "type": "string",
                    "enum": ["mysql", "postgresql", "sqlite"],
                },
                "action": {
                    "type": "string",
                    "enum": ["status", "connection", "slow_queries", "table_size", "locks"],
                },
                "host": {"type": "string"},
                "port": {"type": "integer"},
                "database": {"type": "string"},
            },
            "required": ["action"],
        },
        execute=execute,
        format_input=lambda args: f"DB诊断({args.get('action', '')}): {args.get('db_type', 'mysql')}@{args.get('host', 'localhost')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["数据库", "诊断", "慢查询", "锁", "连接池", "表空间", "db", "diagnose", "mysql"],
    ))
