"""技能注册表 — 技能元数据 (system prompt + 工具集合 + 输出策略)

借鉴 Pi (pi.dev) 的 skills 机制 (progressive disclosure):
- skill 定义为 skills/*.md 文件 (front-matter 声明元数据, 正文为 system_prompt)
- 启动时 discover() 只扫描目录建立 id→filepath 索引, 不加载 system_prompt 内容
- get(skill_id) 首次调用时才读文件解析并缓存, 真正按需加载
- 新增/修改 skill 只需增改文件, 无需改代码
"""

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any


SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

# skill 校验规则（对应 Pi skills.js 的 validateName / validateDescription）
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")


def validate_skill_name(name: str) -> list[str]:
    """
    校验 skill name（对应 Pi skills.js 的 validateName）。

    规则（违反产生 warning，不阻断加载，与 Pi 一致）:
    - 长度 ≤ 64
    - 只含小写 a-z / 0-9 / 连字符
    - 不以 - 开头或结尾
    - 无连续 --
    """
    errors: list[str] = []
    if len(name) > MAX_NAME_LENGTH:
        errors.append(f"name 超过 {MAX_NAME_LENGTH} 字符 ({len(name)})")
    if not _NAME_PATTERN.match(name):
        errors.append("name 含非法字符（只能小写 a-z / 0-9 / 连字符）")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name 不能以 - 开头或结尾")
    if "--" in name:
        errors.append("name 不能包含连续 --")
    return errors


def validate_skill_description(description: str) -> list[str]:
    """
    校验 skill description（对应 Pi skills.js 的 validateDescription）。

    规则:
    - 必填（缺失/空白 → 不加载，硬阻断，与 name 的 warning 不阻断不同）
    - 长度 ≤ 1024（超长 → warning，仍加载）
    """
    errors: list[str] = []
    if not description or description.strip() == "":
        errors.append("description 缺失（必填）")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"description 超过 {MAX_DESCRIPTION_LENGTH} 字符 ({len(description)})")
    return errors


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


def _parse_skill_file(filepath: str, fallback_id: str = "") -> SkillMeta | None:
    """
    解析 skill markdown 文件 (front-matter 元数据 + 正文 system_prompt)。

    格式:
        ---
        id: utility-skill
        name: 实用工具
        tool_names: ["calculator", "datetime"]
        default: true
        ---
        你是一个实用工具助手...
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    meta_text = parts[1].strip()
    system_prompt = parts[2].strip()

    meta: dict[str, Any] = {}
    for line in meta_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # list: ["a", "b"]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            meta[key] = [
                item.strip().strip('"\'')
                for item in inner.split(",")
                if item.strip()
            ]
        elif value.lower() in ("true", "false"):
            meta[key] = value.lower() == "true"
        elif len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            meta[key] = value[1:-1]
        else:
            meta[key] = value

    skill_id = meta.get("id") or fallback_id
    if not skill_id:
        return None

    # name 校验（对应 Pi validateName：违反只 warning，不阻断加载）
    for err in validate_skill_name(skill_id):
        print(f"[skill_registry] 警告: {skill_id}: {err} ({filepath})", file=sys.stderr)

    # description 校验（对应 Pi validateDescription）
    description = meta.get("description", "")
    for err in validate_skill_description(description):
        print(f"[skill_registry] 警告: {skill_id}: {err} ({filepath})", file=sys.stderr)
    # description 缺失 → 不加载（Pi 语义：硬阻断，与 name 的 warning 不阻断不同）
    if not description or not description.strip():
        return None

    return SkillMeta(
        id=skill_id,
        name=meta.get("name", ""),
        description=meta.get("description", ""),
        system_prompt=system_prompt,
        tool_names=meta.get("tool_names", []),
        output_policy=meta.get("output_policy", "concise-utility"),
        result_policy=meta.get("result_policy", "auto"),
        routing_hints=meta.get("routing_hints", []),
        default=meta.get("default", False),
        tags=meta.get("tags", []),
        fallback_policy=meta.get("fallback_policy", "direct-answer"),
    )


class SkillRegistry:
    """技能注册表 — 全局单例 (懒加载)"""

    def __init__(self):
        # 已加载的 skill (首次 get 时从文件解析并缓存)
        self._loaded: dict[str, SkillMeta] = {}
        # id → filepath 索引 (启动时 discover 建立, 不加载内容)
        self._index: dict[str, str] = {}

    def discover(self, skills_dir: str | None = None) -> "SkillRegistry":
        """
        扫描 skills/ 目录建立索引 (progressive disclosure: 不加载 system_prompt)。

        约定: 文件名 (去 .md) 即 skill_id, 如 utility-skill.md → "utility-skill"
        """
        d = skills_dir or SKILLS_DIR
        if not os.path.isdir(d):
            return self
        for f in sorted(os.listdir(d)):
            if f.endswith(".md"):
                skill_id = f[:-3]
                self._index[skill_id] = os.path.join(d, f)
        return self

    def register(self, meta: SkillMeta) -> "SkillRegistry":
        """直接注册 (兼容编程式注册, 立即可用)"""
        self._loaded[meta.id] = meta
        return self

    def get(self, skill_id: str) -> SkillMeta | None:
        """按需加载: 首次 get 时读文件解析并缓存, 后续命中缓存"""
        if skill_id in self._loaded:
            return self._loaded[skill_id]
        filepath = self._index.get(skill_id)
        if not filepath:
            return None
        meta = _parse_skill_file(filepath, fallback_id=skill_id)
        if not meta:
            return None
        self._loaded[skill_id] = meta
        return meta

    def has(self, skill_id: str) -> bool:
        return skill_id in self._loaded or skill_id in self._index

    def list_meta(self) -> list[SkillMeta]:
        """列出所有 skill (触发未加载的全部加载)"""
        for sid in list(self._index.keys()):
            if sid not in self._loaded:
                self.get(sid)
        return list(self._loaded.values())

    def get_default(self) -> SkillMeta | None:
        for s in self.list_meta():
            if s.default:
                return s
        return None

    def clear(self) -> None:
        self._loaded.clear()
        self._index.clear()


# 全局技能注册表实例
skill_registry = SkillRegistry()
