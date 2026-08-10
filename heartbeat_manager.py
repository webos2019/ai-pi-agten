"""Heartbeat Manager — 心跳与定时调度

借鉴 Prime Agent 的三种调度面:
- /heartbeat (用户) — 一个可见的周期性指令
- rlm_heartbeat (Agent) — 多个编程式管理的内部心跳
- schedule (CLI) — 通用一次性或 cron 任务

用法:
    mgr = get_heartbeat_manager()
    hb = await mgr.create_heartbeat("session-1", "检查部署状态", interval="10m")
    instruction = await mgr.fire_if_due("session-1")  # 到期则返回指令
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class Heartbeat:
    """心跳定义"""
    heartbeat_id: str
    session_id: str
    instruction: str           # 心跳触发时注入的指令
    interval_seconds: float     # 间隔（秒）
    label: str = "default"
    delivery_mode: str = "steer"  # steer / follow_up
    status: str = "active"       # active / paused
    last_fired_at: float = 0.0
    fire_count: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class ScheduledJob:
    """定时任务"""
    job_id: str
    agent_id: str
    prompt: str
    schedule_type: str     # "once" or "cron"
    fire_at: float          # 一次性：触发时间戳
    cron_expr: str = ""     # cron 表达式
    status: str = "pending"  # pending / claimed / done / cancelled / missed
    created_at: float = field(default_factory=time.time)
    fired_at: float = 0.0


# ─── Heartbeat Manager ────────────────────────────────

class HeartbeatManager:
    """心跳与调度管理器

    管理:
    - 每个会话的心跳（可多个）
    - 一次性/周期性定时任务
    - 到期检查和触发
    """

    def __init__(self):
        self._heartbeats: dict[str, list[Heartbeat]] = {}  # session_id -> [Heartbeat]
        self._schedules: dict[str, ScheduledJob] = {}
        self._storage_dir = os.path.join("data", "schedules")

    # ── 心跳管理 ──

    async def create_heartbeat(
        self,
        session_id: str,
        instruction: str,
        interval: str = "10m",
        label: str = "default",
        delivery_mode: str = "steer",
    ) -> Heartbeat:
        """创建心跳 — /heartbeat every 10m Check deployment

        Args:
            session_id: 会话 ID
            instruction: 心跳触发时注入的指令
            interval: 间隔，如 "10m", "30s", "1h"
            label: 标签（用于区分多个心跳）
            delivery_mode: "steer" 或 "follow_up"
        """
        from stream import create_id
        seconds = self._parse_interval(interval)
        hb = Heartbeat(
            heartbeat_id=create_id(),
            session_id=session_id,
            instruction=instruction,
            interval_seconds=seconds,
            label=label,
            delivery_mode=delivery_mode,
        )
        self._heartbeats.setdefault(session_id, []).append(hb)
        self._persist_heartbeats(session_id)
        return hb

    async def fire_if_due(self, session_id: str) -> list[dict[str, Any]]:
        """检查心跳是否到期，返回要注入的指令列表

        一个会话可能有多个心跳，返回所有到期的心跳
        """
        hbs = self._heartbeats.get(session_id, [])
        now = time.time()
        fired = []
        for hb in hbs:
            if hb.status != "active":
                continue
            if now - hb.last_fired_at >= hb.interval_seconds:
                hb.last_fired_at = now
                hb.fire_count += 1
                fired.append({
                    "heartbeat_id": hb.heartbeat_id,
                    "instruction": hb.instruction,
                    "delivery_mode": hb.delivery_mode,
                    "label": hb.label,
                })
        if fired:
            self._persist_heartbeats(session_id)
        return fired

    async def pause_heartbeat(self, session_id: str, label: str = "default"):
        """暂停心跳"""
        for hb in self._heartbeats.get(session_id, []):
            if hb.label == label:
                hb.status = "paused"
        self._persist_heartbeats(session_id)

    async def resume_heartbeat(self, session_id: str, label: str = "default"):
        """恢复心跳"""
        for hb in self._heartbeats.get(session_id, []):
            if hb.label == label:
                hb.status = "active"
        self._persist_heartbeats(session_id)

    async def clear_heartbeat(self, session_id: str, label: str = "default"):
        """清除心跳"""
        hbs = self._heartbeats.get(session_id, [])
        self._heartbeats[session_id] = [hb for hb in hbs if hb.label != label]
        self._persist_heartbeats(session_id)

    def get_heartbeats(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话的心跳列表"""
        return [
            {
                "heartbeat_id": hb.heartbeat_id,
                "instruction": hb.instruction,
                "interval": f"{hb.interval_seconds}s",
                "label": hb.label,
                "status": hb.status,
                "fire_count": hb.fire_count,
                "last_fired_at": hb.last_fired_at,
            }
            for hb in self._heartbeats.get(session_id, [])
        ]

    # ── 定时任务 ──

    async def add_schedule(
        self,
        agent_id: str,
        prompt: str,
        fire_at: float | None = None,
        in_minutes: float | None = None,
        cron_expr: str = "",
    ) -> ScheduledJob:
        """添加定时任务

        Args:
            agent_id: 目标 agent ID
            prompt: 触发时注入的 prompt
            fire_at: 触发时间戳（一次性）
            in_minutes: N 分钟后触发（一次性）
            cron_expr: cron 表达式（周期性）
        """
        from stream import create_id
        if in_minutes is not None:
            fire_at = time.time() + in_minutes * 60
        job = ScheduledJob(
            job_id=create_id(),
            agent_id=agent_id,
            prompt=prompt,
            schedule_type="cron" if cron_expr else "once",
            fire_at=fire_at or time.time(),
            cron_expr=cron_expr,
        )
        self._schedules[job.job_id] = job
        self._persist_schedules()
        return job

    async def check_schedules(self) -> list[ScheduledJob]:
        """检查到期的定时任务

        Due ticks are claimed before delivery so a crash
        does not replay an uncertain prompt.
        """
        now = time.time()
        due = []
        for job in self._schedules.values():
            if job.status == "pending" and job.fire_at <= now:
                job.status = "claimed"
                job.fired_at = now
                due.append(job)
        if due:
            self._persist_schedules()
        return due

    async def cancel_schedule(self, job_id: str) -> bool:
        """取消定时任务"""
        job = self._schedules.get(job_id)
        if job and job.status == "pending":
            job.status = "cancelled"
            self._persist_schedules()
            return True
        return False

    def list_schedules(self) -> list[dict[str, Any]]:
        """列出所有定时任务"""
        return [
            {
                "job_id": j.job_id,
                "agent_id": j.agent_id,
                "prompt": j.prompt[:100],
                "type": j.schedule_type,
                "fire_at": j.fire_at,
                "status": j.status,
                "cron": j.cron_expr,
            }
            for j in self._schedules.values()
        ]

    # ── 持久化 ──

    def _persist_heartbeats(self, session_id: str):
        os.makedirs(self._storage_dir, exist_ok=True)
        path = os.path.join(self._storage_dir, f"heartbeats_{session_id}.json")
        hbs = self._heartbeats.get(session_id, [])
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{
                "heartbeat_id": hb.heartbeat_id,
                "instruction": hb.instruction,
                "interval_seconds": hb.interval_seconds,
                "label": hb.label,
                "delivery_mode": hb.delivery_mode,
                "status": hb.status,
                "last_fired_at": hb.last_fired_at,
                "fire_count": hb.fire_count,
            } for hb in hbs], f, ensure_ascii=False, indent=2)

    def _persist_schedules(self):
        os.makedirs(self._storage_dir, exist_ok=True)
        path = os.path.join(self._storage_dir, "schedules.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{
                "job_id": j.job_id,
                "agent_id": j.agent_id,
                "prompt": j.prompt,
                "schedule_type": j.schedule_type,
                "fire_at": j.fire_at,
                "cron_expr": j.cron_expr,
                "status": j.status,
                "fired_at": j.fired_at,
            } for j in self._schedules.values()], f, ensure_ascii=False, indent=2)

    # ── 工具 ──

    def _parse_interval(self, interval: str) -> float:
        """解析间隔: '10m' -> 600, '30s' -> 30, '1h' -> 3600"""
        if not interval:
            return 600  # 默认 10 分钟
        unit = interval[-1].lower()
        try:
            val = float(interval[:-1])
        except ValueError:
            return 600
        return {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60) * val


# ─── 全局实例 ──────────────────────────────────────────

_heartbeat_manager: HeartbeatManager | None = None


def get_heartbeat_manager() -> HeartbeatManager:
    global _heartbeat_manager
    if _heartbeat_manager is None:
        _heartbeat_manager = HeartbeatManager()
    return _heartbeat_manager
