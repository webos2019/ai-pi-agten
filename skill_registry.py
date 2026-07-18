"""技能注册表 — 技能元数据 (system prompt + 工具集合 + 输出策略)"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillMeta:
    """技能元数据"""
    id: str
    name: str
    description: str
    system_prompt: str
    tool_names: list[str]
    output_policy: str = "concise-utility"  # "concise-utility" | "detailed-explanation" | "creative"
    result_policy: str = "auto"  # "tool-first" | "summary-first" | "auto"
    routing_hints: list[str] = field(default_factory=list)
    default: bool = False
    tags: list[str] = field(default_factory=list)
    fallback_policy: str = "direct-answer"  # "direct-answer" | "skip-capability" | "retry"


class SkillRegistry:
    """技能注册表 — 全局单例"""

    def __init__(self):
        self._skills: dict[str, SkillMeta] = {}

    def register(self, meta: SkillMeta) -> "SkillRegistry":
        if meta.id in self._skills:
            raise ValueError(f'Skill "{meta.id}" 已经注册')
        self._skills[meta.id] = meta
        return self

    def get(self, skill_id: str) -> SkillMeta | None:
        return self._skills.get(skill_id)

    def has(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def list_meta(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def get_default(self) -> SkillMeta | None:
        for s in self._skills.values():
            if s.default:
                return s
        return None

    def clear(self) -> None:
        self._skills.clear()


# 全局技能注册表实例
skill_registry = SkillRegistry()
