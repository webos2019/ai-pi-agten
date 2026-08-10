"""Continual Harness — 持续化自我改进框架

借鉴 Prime Agent 的 Continual Harness 设计:
- /refine 审查当前轨迹 → 提取有证据的教训 → 更新 harness 状态
- harness 状态包括：补充提示、记忆、技能描述更新、子代理规格
- 快照支持回滚
- 永不重写底层系统提示（只追加补充指令）

四类可更新状态:
1. supplemental_prompts: 追加到系统提示末尾的指令
2. memories: 结构化跨会话记忆（category + content + evidence）
3. skill_updates: 技能描述的微调
4. subagent_specs: 子代理规格的微调（工具集、prompt 等）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ─── 常量 ──────────────────────────────────────────────

MAX_SUPPLEMENTAL_PROMPTS = 20
MAX_HARNESS_MEMORIES = 100
MAX_SNAPSHOTS = 20
HARNESS_STORAGE_DIR = os.path.join("data", "harness")


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class HarnessSnapshot:
    """harness 快照 — 用于回滚"""
    snapshot_id: str
    timestamp: float
    supplemental_prompts: list[str]
    memories: list[dict[str, Any]]
    skill_updates: dict[str, str]
    subagent_specs: dict[str, dict[str, Any]]
    refine_reason: str
    token_count: int = 0


@dataclass
class HarnessMemory:
    """结构化框架记忆"""
    content: str
    category: str          # tool_usage / reasoning / context / error_handling / pattern
    evidence: str          # 来自轨迹的证据引用
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""   # 产生该记忆的会话

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "category": self.category,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
        }


# ─── Continual Harness ────────────────────────────────

class ContinualHarness:
    """持续化框架状态管理器

    用法:
        harness = get_harness()
        harness.set_base_prompt("你是 Pi Agent...")
        effective_prompt = harness.get_effective_system_prompt()
        # /refine 时:
        harness.refine(transcript, llm_result)
        # 回滚:
        harness.rollback(snapshot_id)
    """

    def __init__(self, storage_dir: str = HARNESS_STORAGE_DIR):
        # 不可变的基础系统提示（永不重写）
        self.base_system_prompt: str = ""

        # 可更新的补充状态
        self.supplemental_prompts: list[str] = []
        self.memories: list[HarnessMemory] = []
        self.skill_updates: dict[str, str] = {}
        self.subagent_specs: dict[str, dict[str, Any]] = {}

        # 快照历史
        self._snapshots: list[HarnessSnapshot] = []

        # 存储
        self._storage_dir = storage_dir
        self._loaded = False

    # ── 初始化 ──

    def set_base_prompt(self, prompt: str):
        """设置基础系统提示（只设置一次，不可变）"""
        if not self.base_system_prompt:
            self.base_system_prompt = prompt

    def load(self):
        """从磁盘加载持久化状态"""
        if self._loaded:
            return
        os.makedirs(self._storage_dir, exist_ok=True)
        path = os.path.join(self._storage_dir, "harness_state.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.supplemental_prompts = data.get("supplemental_prompts", [])
                self.memories = [
                    HarnessMemory(**m) if isinstance(m, dict) else m
                    for m in data.get("memories", [])
                ]
                self.skill_updates = data.get("skill_updates", {})
                self.subagent_specs = data.get("subagent_specs", {})
            except (json.JSONDecodeError, TypeError):
                pass  # 降级为空状态
        self._loaded = True

    def _persist(self):
        """持久化到磁盘"""
        os.makedirs(self._storage_dir, exist_ok=True)
        path = os.path.join(self._storage_dir, "harness_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "supplemental_prompts": self.supplemental_prompts,
                "memories": [m.to_dict() for m in self.memories],
                "skill_updates": self.skill_updates,
                "subagent_specs": self.subagent_specs,
            }, f, ensure_ascii=False, indent=2)

    # ── 系统提示 ──

    def get_effective_system_prompt(self) -> str:
        """获取有效系统提示 = 基础提示 + 补充提示 + 框架记忆"""
        self.load()
        parts = [self.base_system_prompt]

        # 补充提示
        if self.supplemental_prompts:
            parts.append("\n\n## 补充指令（由 /refine 自动生成）")
            for p in self.supplemental_prompts:
                parts.append(f"- {p}")

        # 框架记忆（最近 5 条）
        recent_memories = self.memories[-5:] if self.memories else []
        if recent_memories:
            parts.append("\n\n## 框架记忆（历史教训）")
            for m in recent_memories:
                parts.append(f"- [{m.category}] {m.content}")

        return "\n".join(parts)

    # ── /refine 核心方法 ──

    def refine(
        self,
        transcript: list[dict[str, str]],
        llm_refine_result: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any]:
        """执行一次 /refine 更新

        Args:
            transcript: 当前会话轨迹 [{"role": "user/assistant", "content": "..."}]
            llm_refine_result: LLM 分析后的结构化改进建议
                {
                    "reason": "改进原因",
                    "lessons": [
                        {"category": "tool_usage", "content": "...", "evidence": "..."}
                    ],
                    "supplemental_prompts": ["补充指令1", "补充指令2"],
                    "skill_updates": {"skill_name": "更新后的描述"}
                }
            session_id: 产生此更新的会话 ID

        Returns:
            更新摘要
        """
        self.load()

        # 保存快照（回滚用）
        reason = llm_refine_result.get("reason", "manual refine")
        self._save_snapshot(reason)

        # 统计
        added_prompts = 0
        added_memories = 0
        added_skill_updates = 0

        # 应用补充提示
        for prompt in llm_refine_result.get("supplemental_prompts", []):
            if prompt not in self.supplemental_prompts and len(self.supplemental_prompts) < MAX_SUPPLEMENTAL_PROMPTS:
                self.supplemental_prompts.append(prompt)
                added_prompts += 1

        # 应用记忆教训
        for lesson in llm_refine_result.get("lessons", []):
            content = lesson.get("content", "")
            if not content:
                continue
            if len(self.memories) >= MAX_HARNESS_MEMORIES:
                self.memories.pop(0)  # FIFO 淘汰
            self.memories.append(HarnessMemory(
                content=content,
                category=lesson.get("category", "general"),
                evidence=lesson.get("evidence", ""),
                session_id=session_id,
            ))
            added_memories += 1

        # 应用技能描述更新
        for skill_name, new_desc in llm_refine_result.get("skill_updates", {}).items():
            self.skill_updates[skill_name] = new_desc
            added_skill_updates += 1

        # 应用子代理规格更新
        for agent_name, spec in llm_refine_result.get("subagent_specs", {}).items():
            self.subagent_specs[agent_name] = spec

        # 持久化
        self._persist()

        return {
            "summary": (
                f"✅ Harness 已更新:\n"
                f"  - 补充提示: +{added_prompts} (总计 {len(self.supplemental_prompts)})\n"
                f"  - 记忆教训: +{added_memories} (总计 {len(self.memories)})\n"
                f"  - 技能更新: +{added_skill_updates} (总计 {len(self.skill_updates)})\n"
                f"  - 快照 ID: {self._snapshots[-1].snapshot_id}"
            ),
            "added_prompts": added_prompts,
            "added_memories": added_memories,
            "added_skill_updates": added_skill_updates,
            "snapshot_id": self._snapshots[-1].snapshot_id if self._snapshots else "",
        }

    # ── 快照与回滚 ──

    def _save_snapshot(self, reason: str):
        """保存当前状态快照"""
        from stream import create_id
        snap = HarnessSnapshot(
            snapshot_id=create_id(),
            timestamp=time.time(),
            supplemental_prompts=self.supplemental_prompts.copy(),
            memories=[m.to_dict() for m in self.memories],
            skill_updates=self.skill_updates.copy(),
            subagent_specs=self.subagent_specs.copy(),
            refine_reason=reason,
        )
        self._snapshots.append(snap)
        if len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

    def rollback(self, snapshot_id: str | None = None) -> dict[str, Any]:
        """回滚到指定快照

        Args:
            snapshot_id: 快照 ID，None 则回滚到最近一个

        Returns:
            {"success": bool, "message": str}
        """
        if not self._snapshots:
            return {"success": False, "message": "无快照可回滚"}

        if snapshot_id:
            target = next((s for s in self._snapshots if s.snapshot_id == snapshot_id), None)
        else:
            target = self._snapshots[-1]

        if not target:
            return {"success": False, "message": f"快照 {snapshot_id} 不存在"}

        self.supplemental_prompts = target.supplemental_prompts.copy()
        self.memories = [HarnessMemory(**m) if isinstance(m, dict) else m for m in target.memories]
        self.skill_updates = target.skill_updates.copy()
        self.subagent_specs = target.subagent_specs.copy()
        self._persist()

        return {"success": True, "message": f"已回滚到快照 {target.snapshot_id} ({target.refine_reason})"}

    def list_snapshots(self) -> list[dict[str, Any]]:
        """列出所有快照"""
        return [
            {
                "snapshot_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "reason": s.refine_reason,
                "prompts_count": len(s.supplemental_prompts),
                "memories_count": len(s.memories),
            }
            for s in reversed(self._snapshots)
        ]

    # ── 查询 ──

    def get_status(self) -> dict[str, Any]:
        """获取 harness 状态摘要"""
        self.load()
        return {
            "base_prompt_set": bool(self.base_system_prompt),
            "supplemental_prompts_count": len(self.supplemental_prompts),
            "memories_count": len(self.memories),
            "skill_updates_count": len(self.skill_updates),
            "subagent_specs_count": len(self.subagent_specs),
            "snapshots_count": len(self._snapshots),
            "recent_memories": [
                {"category": m.category, "content": m.content[:100]}
                for m in self.memories[-5:]
            ],
        }


# ─── 全局实例 ──────────────────────────────────────────

_harness: ContinualHarness | None = None


def get_harness() -> ContinualHarness:
    """获取全局 harness 实例"""
    global _harness
    if _harness is None:
        _harness = ContinualHarness()
        _harness.load()
    return _harness
