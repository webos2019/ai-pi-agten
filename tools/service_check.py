"""服务状态检查工具 — HTTP健康检查 / 端口连通性 / SSL证书检查"""

import asyncio
import ssl
import socket
from datetime import datetime, timezone
from typing import Any

import httpx

from tool_registry import tool_registry, ChatToolDefinition


async def _http_check(url: str, timeout: float = 10.0) -> dict:
    """HTTP 健康检查"""
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            resp = await client.get(url)
            return {
                "url": url,
                "status_code": resp.status_code,
                "response_time_ms": round(resp.elapsed.total_seconds() * 1000, 1),
                "ok": 200 <= resp.status_code < 400,
            }
    except httpx.TimeoutException:
        return {"url": url, "status_code": -1, "response_time_ms": -1, "ok": False, "error": "timeout"}
    except httpx.ConnectError:
        return {"url": url, "status_code": -1, "response_time_ms": -1, "ok": False, "error": "connection_refused"}
    except Exception as e:
        return {"url": url, "status_code": -1, "response_time_ms": -1, "ok": False, "error": str(e)}


def _port_check(host: str, port: int, timeout: float = 5.0) -> dict:
    """端口连通性检查"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return {"host": host, "port": port, "reachable": result == 0}
    except Exception as e:
        return {"host": host, "port": port, "reachable": False, "error": str(e)}


def _ssl_check(hostname: str, port: int = 443) -> dict:
    """SSL 证书到期检查"""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter", "")
        if not_after:
            # 解析日期格式: "Sep 15 12:00:00 2025 GMT"
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            now = datetime.now(timezone.utc)
            days_left = (expiry - now).days
            return {
                "hostname": hostname,
                "issuer": cert.get("issuer", ""),
                "not_after": not_after,
                "days_left": days_left,
                "expiring_soon": days_left <= 30,
                "expired": days_left < 0,
            }
        return {"hostname": hostname, "error": "no certificate"}
    except Exception as e:
        return {"hostname": hostname, "error": str(e)}


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    check_type = args.get("check_type", "http")
    results: list[dict] = []

    if check_type == "http":
        urls = args.get("targets", [])
        if isinstance(urls, str):
            urls = [urls]
        tasks = [_http_check(u, args.get("timeout", 10.0)) for u in urls]
        results = await asyncio.gather(*tasks)

    elif check_type == "port":
        host = args.get("host", "localhost")
        ports = args.get("ports", [])
        if isinstance(ports, int):
            ports = [ports]
        results = [_port_check(host, p) for p in ports]

    elif check_type == "ssl":
        hostname = args.get("hostname", "")
        if hostname:
            results = [_ssl_check(hostname)]

    return {"check_type": check_type, "results": results}


def register():
    tool_registry.register(ChatToolDefinition(
        name="service_check",
        description="服务状态检查：HTTP健康检查/端口连通性/SSL证书到期",
        parameters={
            "type": "object",
            "properties": {
                "check_type": {
                    "type": "string",
                    "enum": ["http", "port", "ssl"],
                },
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "host": {"type": "string"},
                "ports": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "hostname": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["check_type"],
        },
        execute=execute,
        format_input=lambda args: f"服务检查({args.get('check_type', '')}): {args.get('targets') or args.get('host') or args.get('hostname', '')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["服务", "状态", "检查", "健康", "端口", "SSL", "证书", "HTTP", "连通", "service", "check"],
    ))
