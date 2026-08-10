"""系统监控工具 — CPU/内存/磁盘/系统负载/网络连接数"""

import os
import platform
import socket
import time
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


def _get_cpu_usage() -> dict:
    """获取 CPU 使用率"""
    try:
        # Windows
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage", "/value"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split("\n"):
                if "LoadPercentage" in line:
                    val = line.split("=")[1].strip()
                    return {"cpu_usage_percent": int(val) if val else -1}
            return {"cpu_usage_percent": -1, "error": "parse_failed"}
        else:
            # Linux: /proc/stat
            with open("/proc/stat", "r") as f:
                line1 = f.readline()
            time.sleep(0.1)
            with open("/proc/stat", "r") as f:
                line2 = f.readline()
            parts1 = list(map(int, line1.split()[1:]))
            parts2 = list(map(int, line2.split()[1:]))
            idle1 = parts1[3]
            idle2 = parts2[3]
            total1 = sum(parts1)
            total2 = sum(parts2)
            usage = 100 * (1 - (idle2 - idle1) / max(total2 - total1, 1))
            return {"cpu_usage_percent": round(usage, 1)}
    except Exception as e:
        return {"cpu_usage_percent": -1, "error": str(e)}


def _get_memory_usage() -> dict:
    """获取内存使用率"""
    try:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/value"],
                capture_output=True, text=True, timeout=10
            )
            free = total = 0
            for line in result.stdout.strip().split("\n"):
                if "FreePhysicalMemory" in line:
                    free = int(line.split("=")[1].strip())
                if "TotalVisibleMemorySize" in line:
                    total = int(line.split("=")[1].strip())
            if total > 0:
                used = total - free
                return {
                    "total_mb": round(total / 1024, 1),
                    "used_mb": round(used / 1024, 1),
                    "free_mb": round(free / 1024, 1),
                    "usage_percent": round(100 * used / total, 1),
                }
            return {"error": "parse_failed"}
        else:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemFree:", "MemAvailable:", "Buffers:", "Cached:"):
                    info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            used = total - avail
            return {
                "total_mb": round(total / 1024, 1),
                "used_mb": round(used / 1024, 1),
                "free_mb": round(avail / 1024, 1),
                "usage_percent": round(100 * used / max(total, 1), 1),
            }
    except Exception as e:
        return {"error": str(e)}


def _get_disk_usage(path: str = "/") -> dict:
    """获取磁盘使用率"""
    try:
        if platform.system() == "Windows":
            path = path if path != "/" else "C:"
        stat = os.statvfs(path) if platform.system() != "Windows" else None
        if stat:
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
        else:
            import shutil
            total, used, free = shutil.disk_usage(path)
        used = total - free
        return {
            "path": path,
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "usage_percent": round(100 * used / max(total, 1), 1),
        }
    except Exception as e:
        return {"path": path, "error": str(e)}


def _get_system_load() -> dict:
    """获取系统负载"""
    try:
        if platform.system() == "Windows":
            return {"note": "Windows does not support load average"}
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
        return {
            "load_1min": float(parts[0]),
            "load_5min": float(parts[1]),
            "load_15min": float(parts[2]),
            "running_processes": parts[3],
        }
    except Exception as e:
        return {"error": str(e)}


def _get_network_connections() -> dict:
    """获取网络连接数"""
    try:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["netstat", "-an"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split("\n")
            established = sum(1 for l in lines if "ESTABLISHED" in l)
            time_wait = sum(1 for l in lines if "TIME_WAIT" in l)
            return {"established": established, "time_wait": time_wait, "total": len(lines)}
        else:
            with open("/proc/net/tcp", "r") as f:
                lines = f.readlines()[1:]  # skip header
            states = {}
            for line in lines:
                state = line.split()[3]
                states[state] = states.get(state, 0) + 1
            # 01=ESTABLISHED, 06=TIME_WAIT
            return {
                "established": states.get("01", 0),
                "time_wait": states.get("06", 0),
                "total": len(lines),
            }
    except Exception as e:
        return {"error": str(e)}


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    check_items = args.get("check_items", ["cpu", "memory", "disk", "load", "network"])
    disk_path = args.get("disk_path", "/" if platform.system() != "Windows" else "C:")

    result = {"hostname": socket.gethostname(), "platform": platform.system()}

    if "cpu" in check_items:
        result["cpu"] = _get_cpu_usage()
    if "memory" in check_items:
        result["memory"] = _get_memory_usage()
    if "disk" in check_items:
        result["disk"] = _get_disk_usage(disk_path)
    if "load" in check_items:
        result["load"] = _get_system_load()
    if "network" in check_items:
        result["network"] = _get_network_connections()

    return result


def register():
    tool_registry.register(ChatToolDefinition(
        name="system_monitor",
        description="检查系统资源：CPU/内存/磁盘/负载/网络",
        parameters={
            "type": "object",
            "properties": {
                "check_items": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["cpu", "memory", "disk", "load", "network"]},
                },
                "disk_path": {"type": "string"},
            },
        },
        execute=execute,
        format_input=lambda args: f"系统监控: {args.get('check_items', 'all')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["系统", "监控", "CPU", "内存", "磁盘", "负载", "网络", "monitor", "resource"],
    ))
