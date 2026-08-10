"""
知识提取器 — 从 Q&A 对话中提取知识并写入知识库

设计要点（与 memory_extractor.py 平行）:

写入链路:
  [写入] LLM 提取候选知识 → 去重检查 → kb_store.add_document()
         ↓
       分块 → 向量化 → 存入知识库

- 用 LLM 从用户提问 + 助手回复中提取有价值的、可复用的知识
- 输出结构化 JSON: title / content / tags / confidence
- 程序校验: 内容长度、置信度
- 去重: 提取前搜索知识库，跳过高相似度的已有知识
- 失败降级: 提取失败不影响聊天，静默跳过
- 不提取: 闲聊、简单计算、天气查询等无知识价值的对话
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from deepseek import chat_completion
from knowledge_base import kb_store, get_kb_namespace
from embedding import is_embedding_configured


# ─── 常量 ──────────────────────────────────────────────

MAX_KNOWLEDGE_TITLE_CHARS = 100      # 标题上限
MAX_KNOWLEDGE_CONTENT_CHARS = 2000   # 内容上限
MAX_KNOWLEDGE_PER_TURN = 3           # 每轮最多提取 3 条知识
MIN_KNOWLEDGE_CONTENT_CHARS = 50     # 内容最少字符数（太短不值得存）
MIN_EXTRACT_CONFIDENCE = 0.6         # 提取最低置信度
DEDUP_SCORE_THRESHOLD = 0.85         # 去重相似度阈值
MAX_ASSISTANT_TEXT_CHARS = 4000      # 助手回复截断字符数（提取时）


# ─── 提取结果 ──────────────────────────────────────────

@dataclass
class ExtractedKnowledge:
    """模型提取的候选知识（尚未校验）"""
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExtractedKnowledge":
        return cls(
            title=d.get("title", ""),
            content=d.get("content", ""),
            tags=d.get("tags", [])[:5],
            confidence=float(d.get("confidence", 0.8)),
        )


# ─── LLM 提取 ─────────────────────────────────────────

KNOWLEDGE_EXTRACTION_SYSTEM_PROMPT = """你是一个知识提取助手。你的任务是从用户和助手的对话中提取有价值的、可复用的知识。

只提取包含实质性知识的对话，例如:
- 技术概念解释（如"什么是GIL""Python的装饰器原理"）
- 操作指南和教程（如"如何配置Nginx""Docker部署步骤"）
- 事实性知识（如"长城是世界文化遗产""光速约为30万公里/秒"）
- 经验总结和最佳实践
- 问题排查和解决方案

不要提取:
- 闲聊内容（"你好""谢谢"）
- 一次性查询（"今天天气怎样""现在几点"）
- 简单计算（"1+1等于几"）
- 用户个人偏好（这由记忆系统处理，不是知识库的内容）
- 模糊不确定的表达
- 纯观点性内容
- 重复用户提问本身

知识内容要求:
- content 应该是完整的、自包含的知识描述，不依赖对话上下文也能理解
- content 应该比助手的原始回复更精炼，去除寒暄和过渡语
- title 应简明扼要，概括知识主题

输出 JSON 格式:
{
  "knowledge": [
    {
      "title": "Python GIL 全局解释器锁",
      "content": "GIL（Global Interpreter Lock）是CPython解释器中的互斥锁，它确保同一时刻只有一个线程执行Python字节码。这意味着CPython的多线程程序无法利用多核CPU实现真正的并行计算。对于I/O密集型任务，多线程仍然有效；对于CPU密集型任务，建议使用多进程（multiprocessing）来绕过GIL的限制。",
      "tags": ["Python", "并发", "GIL"],
      "confidence": 0.95
    }
  ]
}

如果没有可提取的知识，返回: {"knowledge": []}"""


async def extract_knowledge_from_turn(
    user_text: str,
    assistant_text: str,
) -> list[ExtractedKnowledge]:
    """
    用 LLM 从一轮对话中提取候选知识。

    - 输入: 用户消息 + 助手回复
    - 输出: 候选知识列表（尚未校验和去重）
    - 失败时返回空列表（不影响聊天）
    """
    if not user_text or not user_text.strip():
        return []
    if not assistant_text or not assistant_text.strip():
        return []

    messages = [
        {"role": "system", "content": KNOWLEDGE_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户提问: {user_text}\n\n"
                f"助手回复: {assistant_text[:MAX_ASSISTANT_TEXT_CHARS]}"
            ),
        },
    ]

    try:
        response = await chat_completion(
            messages=messages,
            tools=[],
            temperature=0.1,
            max_tokens=2048,
        )
        content = response.choices[0].message.content or ""

        # 解析 JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            return []

        parsed = json.loads(content[start:end])
        raw_knowledge = parsed.get("knowledge", [])
        if not isinstance(raw_knowledge, list):
            return []

        results: list[ExtractedKnowledge] = []
        for raw in raw_knowledge:
            if not isinstance(raw, dict):
                continue
            item = ExtractedKnowledge.from_dict(raw)
            results.append(item)

        return results[:MAX_KNOWLEDGE_PER_TURN]

    except Exception:
        # 提取失败，静默返回空列表
        return []


# ─── 程序校验 ──────────────────────────────────────────

def validate_knowledge(
    candidate: ExtractedKnowledge,
) -> ExtractedKnowledge | None:
    """
    程序校验单条候选知识。

    - title 非空且不超过上限
    - content 非空、不少于下限、不超过上限
    - confidence >= 阈值
    """
    title = candidate.title.strip()
    if not title:
        return None
    if len(title) > MAX_KNOWLEDGE_TITLE_CHARS:
        title = title[:MAX_KNOWLEDGE_TITLE_CHARS]

    content = candidate.content.strip()
    if not content:
        return None
    if len(content) < MIN_KNOWLEDGE_CONTENT_CHARS:
        return None
    if len(content) > MAX_KNOWLEDGE_CONTENT_CHARS:
        content = content[:MAX_KNOWLEDGE_CONTENT_CHARS]

    if candidate.confidence < MIN_EXTRACT_CONFIDENCE:
        return None

    return ExtractedKnowledge(
        title=title,
        content=content,
        tags=candidate.tags[:5],
        confidence=candidate.confidence,
    )


# ─── 去重检查 ──────────────────────────────────────────

async def is_duplicate_knowledge(
    namespace: str,
    title: str,
    content: str,
) -> bool:
    """
    检查知识库中是否已存在高度相似的知识。

    - 用标题+内容前 200 字作为查询
    - 搜索结果中 score >= DEDUP_SCORE_THRESHOLD 则判定为重复
    - embedding 未配置时跳过去重（返回 False）
    """
    if not is_embedding_configured():
        return False

    try:
        query = f"{title} {content[:200]}"
        results = await kb_store.search(namespace, query, limit=3)
        for r in results:
            if r.score >= DEDUP_SCORE_THRESHOLD:
                return True
    except Exception:
        pass

    return False


# ─── 主入口: extract_and_store_knowledge ───────────────

async def extract_and_store_knowledge(
    session_id: str,
    user_text: str,
    assistant_text: str,
) -> int:
    """
    从一轮对话中提取知识并存储到知识库。

    完整链路:
      LLM 提取候选 → 程序校验 → 去重 → kb_store.add_document()

    返回: 成功存储的知识条数
    失败时返回 0（不影响聊天）
    """
    if not user_text or not user_text.strip():
        return 0
    if not assistant_text or not assistant_text.strip():
        return 0

    namespace = get_kb_namespace(session_id)

    # LLM 提取候选
    candidates = await extract_knowledge_from_turn(user_text, assistant_text)
    if not candidates:
        return 0

    # 程序校验 + 去重 + 存储
    stored_count = 0
    for candidate in candidates:
        validated = validate_knowledge(candidate)
        if validated is None:
            continue

        # 去重检查
        is_dup = await is_duplicate_knowledge(
            namespace, validated.title, validated.content,
        )
        if is_dup:
            continue

        # 存储到知识库
        try:
            await kb_store.add_document(
                namespace=namespace,
                title=validated.title,
                source_type="chat",
                text=validated.content,
                source_path="",
            )
            stored_count += 1
        except Exception as e:
            print(f"[knowledge-extractor] 存储失败: {e}")
            continue

    if stored_count > 0:
        print(
            f"[knowledge-extractor] extraction-success: "
            f"stored={stored_count}, candidates={len(candidates)}"
        )

    return stored_count
