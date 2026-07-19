"""
记忆提取器 — 模型驱动的候选记忆提取 + 程序校验

设计要点（借鉴掘金文章 AI Mind v0.4.6）:

写入链路:
  [写入] 模型提取候选记忆 → 程序校验 → store.put(value, ['text','tags'])
         ↓
       生成向量 → 存入向量存储

- 用 LLM 从对话轮中提取用户偏好/事实/风格记忆
- 输出结构化 JSON: text / tags / polarity / confidence / type / subject / facet
- 程序校验: confidence 阈值、文本长度、去重
- 失败降级: 提取失败不影响聊天，静默跳过
- 不提取: 普通闲聊、无偏好信息的对话
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from deepseek import chat_completion
from user_memory import (
    UserMemory,
    UserMemoryStore,
    STATUS_ACTIVE,
    POLARITY_PREFER,
    POLARITY_AVOID,
    POLARITY_NEUTRAL,
)
from embedding import is_embedding_configured


# ─── 常量 ──────────────────────────────────────────────

MAX_MEMORY_TEXT_CHARS = 300       # 单条记忆文本上限
MAX_TAGS_PER_MEMORY = 5           # 每条记忆最多标签数
MAX_CANDIDATES_PER_TURN = 5       # 每轮最多提取 5 条候选
MIN_EXTRACT_CONFIDENCE = 0.6      # 提取最低置信度（低于此值丢弃）
EXTRACTION_TIMEOUT_MS = 10000     # 提取超时（毫秒）


# ─── 提取结果 ──────────────────────────────────────────

@dataclass
class ExtractedMemoryCandidate:
    """模型提取的候选记忆（尚未校验）"""
    text: str
    tags: list[str] = field(default_factory=list)
    polarity: str = POLARITY_NEUTRAL
    confidence: float = 0.7
    memory_type: str = "preference"
    subject: str = ""
    facet: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExtractedMemoryCandidate":
        return cls(
            text=d.get("text", ""),
            tags=d.get("tags", [])[:MAX_TAGS_PER_MEMORY],
            polarity=d.get("polarity", POLARITY_NEUTRAL),
            confidence=float(d.get("confidence", 0.7)),
            memory_type=d.get("type", d.get("memory_type", "preference")),
            subject=d.get("subject", ""),
            facet=d.get("facet", ""),
            reason=d.get("reason", ""),
        )


# ─── LLM 提取 ─────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """你是一个用户记忆提取助手。你的任务是从对话中提取用户的长期偏好、事实和风格记忆。

只提取明确的、可跨会话复用的用户信息，例如:
- 饮食偏好（"我不吃香菜" → avoid）
- 技术偏好（"技术解释先用大白话" → prefer）
- 个人事实（"我用 Mac 开发" → neutral）
- 回答风格（"别讲太抽象" → prefer）

不要提取:
- 一次性的问题（"今天天气怎样"）
- 闲聊内容（"你好""谢谢"）
- 工具调用相关的临时参数
- 模糊不确定的表达

输出 JSON 格式:
{
  "memories": [
    {
      "text": "用户不吃香菜",
      "tags": ["饮食", "忌口"],
      "polarity": "avoid",
      "confidence": 0.9,
      "type": "preference",
      "subject": "饮食",
      "facet": "香菜",
      "reason": "用户明确表示不吃香菜"
    }
  ]
}

polarity 取值: "prefer"(喜欢/偏好) | "avoid"(不喜欢/忌口) | "neutral"(中性事实)
type 取值: "preference"(偏好) | "fact"(事实) | "style"(风格)
confidence: 0.0~1.0，表示这条记忆的明确程度
subject: 主题分类（如"饮食""技术""工作"）
facet: 具体维度（如"香菜""解释风格""操作系统"）

如果没有可提取的记忆，返回: {"memories": []}"""


async def extract_memories_from_turn(
    user_text: str,
    assistant_text: str,
    existing_memory_texts: list[str] | None = None,
) -> list[ExtractedMemoryCandidate]:
    """
    用 LLM 从一轮对话中提取候选用户记忆。

    - 输入: 用户消息 + 助手回复
    - 输出: 候选记忆列表（尚未校验和去重）
    - 失败时返回空列表（不影响聊天）
    """
    if not user_text or not user_text.strip():
        return []

    existing_hint = ""
    if existing_memory_texts:
        existing_hint = "\n\n已有记忆（避免重复提取）:\n" + "\n".join(
            f"- {t}" for t in existing_memory_texts[:20]
        )

    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户说: {user_text}\n\n"
                f"助手回复: {assistant_text[:1000] if assistant_text else '(无回复)'}"
                f"{existing_hint}"
            ),
        },
    ]

    try:
        response = await chat_completion(
            messages=messages,
            tools=[],
            temperature=0.1,
            max_tokens=1024,
        )
        content = response.choices[0].message.content or ""

        # 解析 JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            return []

        parsed = json.loads(content[start:end])
        raw_memories = parsed.get("memories", [])
        if not isinstance(raw_memories, list):
            return []

        candidates: list[ExtractedMemoryCandidate] = []
        for raw in raw_memories:
            if not isinstance(raw, dict):
                continue
            candidate = ExtractedMemoryCandidate.from_dict(raw)
            candidates.append(candidate)

        return candidates[:MAX_CANDIDATES_PER_TURN]

    except Exception:
        # 提取失败，静默返回空列表
        return []


# ─── 程序校验 ──────────────────────────────────────────

def validate_candidate(
    candidate: ExtractedMemoryCandidate,
    existing_texts: list[str],
) -> UserMemory | None:
    """
    程序校验单条候选记忆。

    - text 非空且不超过上限
    - confidence >= 阈值
    - polarity 合法
    - 去重: 与已有记忆文本完全相同则跳过
    """
    # 文本校验
    text = candidate.text.strip()
    if not text:
        return None
    if len(text) > MAX_MEMORY_TEXT_CHARS:
        text = text[:MAX_MEMORY_TEXT_CHARS]

    # 置信度校验
    if candidate.confidence < MIN_EXTRACT_CONFIDENCE:
        return None

    # polarity 校验
    polarity = candidate.polarity
    if polarity not in (POLARITY_PREFER, POLARITY_AVOID, POLARITY_NEUTRAL):
        polarity = POLARITY_NEUTRAL

    # 去重
    if text in existing_texts:
        return None

    return UserMemory(
        stable_key="",  # 由 store.put 分配
        text=text,
        tags=candidate.tags[:MAX_TAGS_PER_MEMORY],
        polarity=polarity,
        status=STATUS_ACTIVE,
        confidence=candidate.confidence,
        reason=candidate.reason,
        memory_type=candidate.memory_type,
        subject=candidate.subject,
        facet=candidate.facet,
    )


# ─── 主入口: extract_and_store_memories ────────────────

async def extract_and_store_memories(
    store: UserMemoryStore,
    namespace: str,
    user_text: str,
    assistant_text: str,
    source_conversation_id: str = "",
) -> int:
    """
    从一轮对话中提取记忆并存储。

    完整链路:
      LLM 提取候选 → 程序校验 → 去重 → store.put(['text','tags'])

    返回: 成功存储的记忆条数
    失败时返回 0（不影响聊天）
    """
    if not user_text or not user_text.strip():
        return 0

    # 获取已有记忆文本（用于去重）
    existing_memories = store.list_memories(namespace)
    existing_texts = [m.text for m in existing_memories]

    # LLM 提取候选
    candidates = await extract_memories_from_turn(
        user_text, assistant_text, existing_texts,
    )
    if not candidates:
        return 0

    # 程序校验 + 去重 + 存储
    stored_count = 0
    for candidate in candidates:
        validated = validate_candidate(candidate, existing_texts)
        if validated is None:
            continue

        # 分配 stable_key
        stable_key = f"mem-{int(time.time() * 1000)}-{stored_count}"
        validated.stable_key = stable_key
        validated.source_conversation_id = source_conversation_id

        # 存储（active → 自动向量化）
        await store.put(
            namespace=namespace,
            key=stable_key,
            memory=validated,
            index_fields=["text", "tags"] if is_embedding_configured() else False,
        )

        existing_texts.append(validated.text)
        stored_count += 1

    if stored_count > 0:
        print(
            f"[user-memory] extraction-success: "
            f"stored={stored_count}, candidates={len(candidates)}"
        )

    return stored_count
