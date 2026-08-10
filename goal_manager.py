"""Goal Manager — 持久化目标

借鉴 Prime Agent 的 /goal 设计:
- 目标是跨轮次的持久化对象
- harness 在每个普通轮次后继续提示目标
- 只有 goal.complete() 才标记成功完成
- 支持 token 预算限制
- 目标状态: active / paused / completed / errored / budget_exceeded

用法:
    mgr = get_goal_manager()
    mgr.create("session-1", "完成代码迁移", budget=200000)
    if mgr.should_continue("session-1"):
        # 继续推进目标
        mgr.add_progress("session-1", "迁移了 3 个文件", tokens_used=500)
    mgr.complete("session-1")
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class Goal:
    """持久化目标"""
    goal_id: str
    session_id: str
    objective: str               # 目标描述
    status: str = "active"       # active / paused / completed / errored / budget_exceeded
    created_at: float = field(default_factory=time.time)
    token_usage: int = 0         # 已用 token
    token_budget: int = 0        # 预算（0=无限制）
    elapsed_seconds: float = 0.0
    continuation_count: int = 0  # 连续推进次数
    progress_log: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


# ─── Goal Manager ─────────────────────────────────────

class GoalManager:
    """目标管理器

    每个会话最多一个活跃目标
    """

    def __init__(self):
        self._goals: dict[str, Goal] = {}  # session_id -> Goal
        self._storage_dir = os.path.join("data", "goals")

    # ── 核心操作 ──

    def create(
        self,
        session_id: str,
        objective: str,
        budget: int = 0,
    ) -> dict[str, Any]:
        """创建持久化目标

        Args:
            session_id: 会话 ID
            objective: 目标描述
            budget: token 预算（0=无限制）

        Returns:
            目标状态
        """
        from stream import create_id

        # 如果已有活跃目标，先暂停
        existing = self._goals.get(session_id)
        if existing and existing.status == "active":
            existing.status = "paused"

        goal = Goal(
            goal_id=create_id(),
            session_id=session_id,
            objective=objective,
            token_budget=budget,
        )
        self._goals[session_id] = goal
        self._persist(session_id)
        return self._goal_to_dict(goal)

    def get_status(self, session_id: str) -> dict[str, Any]:
        """获取目标状态"""
        goal = self._goals.get(session_id)
        if not goal:
            return {"status": "no_goal"}
        return {"status": goal.status, "goal": self._goal_to_dict(goal)}

    def complete(self, session_id: str) -> dict[str, Any]:
        """标记目标完成"""
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "completed"
            goal.updated_at = time.time()
            self._persist(session_id)
            return {"status": "completed", "goal": self._goal_to_dict(goal)}
        return {"status": "no_goal"}

    def pause(self, session_id: str) -> dict[str, Any]:
        """暂停目标"""
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "paused"
            goal.updated_at = time.time()
            self._persist(session_id)
            return {"status": "paused"}
        return {"status": "no_goal"}

    def resume(self, session_id: str) -> dict[str, Any]:
        """恢复目标"""
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "active"
            goal.updated_at = time.time()
            self._persist(session_id)
            return {"status": "active"}
        return {"status": "no_goal"}

    def clear(self, session_id: str) -> dict[str, Any]:
        """清除目标"""
        self._goals.pop(session_id, None)
        path = os.path.join(self._storage_dir, f"goal_{session_id}.json")
        if os.path.exists(path):
            os.remove(path)
        return {"status": "cleared"}

    def error(self, session_id: str, error_msg: str) -> dict[str, Any]:
        """标记目标出错"""
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "errored"
            goal.progress_log.append({"type": "error", "message": error_msg, "timestamp": time.time()})
            goal.updated_at = time.time()
            self._persist(session_id)
            return {"status": "errored"}
        return {"status": "no_goal"}

    # ── 推进逻辑 ──

    def should_continue(self, session_id: str) -> bool:
        """是否应该在普通轮次后继续提示目标

        检查:
        - 目标存在且 active
        - 未超过 token 预算
        - 未超过最大续推次数（防止无限循环）
        """
        goal = self._goals.get(session_id)
        if not goal or goal.status != "active":
            return False

        # token 预算检查
        if goal.token_budget > 0 and goal.token_usage >= goal.token_budget:
            goal.status = "budget_exceeded"
            self._persist(session_id)
            return False

        # 最大续推次数（防止无限循环）
        if goal.continuation_count >= 100:
            goal.status = "errored"
            self._persist(session_id)
            return False

        return True

    def add_progress(
        self,
        session_id: str,
        progress: str,
        tokens_used: int = 0,
    ):
        """记录目标推进进度"""
        goal = self._goals.get(session_id)
        if goal:
            goal.progress_log.append({
                "type": "progress",
                "message": progress,
                "tokens": tokens_used,
                "timestamp": time.time(),
            })
            goal.token_usage += tokens_used
            goal.continuation_count += 1
            goal.elapsed_seconds = time.time() - goal.created_at
            goal.updated_at = time.time()
            self._persist(session_id)

    def get_continuation_prompt(self, session_id: str) -> str | None:
        """获取续推 prompt — 在普通轮次后注入

        Returns:
            续推 prompt 或 None（无活跃目标时）
        """
        goal = self._goals.get(session_id)
        if not goal or goal.status != "active":
            return None
        return (
            f"[目标续推] 当前目标: {goal.objective}\n"
            f"已推进 {goal.continuation_count} 次，已用 {goal.token_usage} token"
            + (f" / {goal.token_budget} 预算" if goal.token_budget else "")
            + "\n请继续推进目标。"
        )

    # ── 统一入口 (供 RLM kernel 调用) ──

    async def handle(self, session_id: str, action: str = "get", **kwargs) -> dict[str, Any]:
        """统一入口 — 供 RLM kernel 的 goal skill 调用"""
        if action == "get":
            return self.get_status(session_id)
        elif action == "complete":
            return self.complete(session_id)
        elif action == "create":
            return self.create(session_id, kwargs.get("objective", ""), kwargs.get("budget", 0))
        elif action == "pause":
            return self.pause(session_id)
        elif action == "resume":
            return self.resume(session_id)
        elif action == "clear":
            return self.clear(session_id)
        return {"error": f"unknown action: {action}"}

    # ── 持久化 ──

    def _persist(self, session_id: str):
        os.makedirs(self._storage_dir, exist_ok=True)
        goal = self._goals.get(session_id)
        if not goal:
            return
        path = os.path.join(self._storage_dir, f"goal_{session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._goal_to_dict(goal), f, ensure_ascii=False, indent=2)

    def load(self, session_id: str):
        """从磁盘加载目标"""
        path = os.path.join(self._storage_dir, f"goal_{session_id}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                goal = Goal(
                    goal_id=data["goal_id"],
                    session_id=data["session_id"],
                    objective=data["objective"],
                    status=data["status"],
                    created_at=data["created_at"],
                    token_usage=data["token_usage"],
                    token_budget=data["token_budget"],
                    elapsed_seconds=data["elapsed_seconds"],
                    continuation_count=data["continuation_count"],
                    progress_log=data.get("progress_log", []),
                    updated_at=data.get("updated_at", time.time()),
                )
                self._goals[session_id] = goal
            except (json.JSONDecodeError, KeyError):
                pass

    def _goal_to_dict(self, goal: Goal) -> dict[str, Any]:
        return {
            "goal_id": goal.goal_id,
            "session_id": goal.session_id,
            "objective": goal.objective,
            "status": goal.status,
            "tokenUsage": goal.token_usage,
            "tokenBudget": goal.token_budget,
            "elapsedSeconds": round(goal.elapsed_seconds, 1),
            "continuationCount": goal.continuation_count,
            "progressLog": goal.progress_log[-10:],  # 最近 10 条
            "createdAt": goal.created_at,
            "updatedAt": goal.updated_at,
        }


# ─── 全局实例 ──────────────────────────────────────────

_goal_manager: GoalManager | None = None


def get_goal_manager() -> GoalManager:
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager()
    return _goal_manager
