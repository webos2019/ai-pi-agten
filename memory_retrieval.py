"""
记忆召回流水线 — 向量搜索 + 6 层过滤 + 预算控制 + 失败降级

设计要点（借鉴掘金文章 AI Mind v0.4.6）:

整条链路:
  [召回] 用户输入 → 裁剪到 800 字符 → embed_query → store.search(top-8)
         ↓
       6 层过滤 → 最多 3 条注入模型

核心决策:
- 不做 LLM query rewrite（多一次调用就多一次延迟和成本，且可能引入歧义）
- 超长截断取前 400 + 后 400（用户可能把关键信息放在末尾）
- topK=8（browser-session 级内存通常只有几十条，8 条够用且不浪费）
- score 阈值 0.32（基于中文场景校准，宁可少召回也不乱注入）
- 最终注入：最多 3 条、单条 300 字、总计 900 字

失败降级:
- 1500ms 超时（Promise 竞速）
- 异常兜底：embedding 挂了、搜索出错 → 返回空数组
- 上层二次兜底：chat_service 也有独立的 try/catch
- 日志脱敏：绝不记录 raw query 文本和 raw memory 文本
"""

from __future__ import annotations

import re
import asyncio
import time
from dataclasses import dataclass
from typing import Any

from user_memory import (
    UserMemory,
    UserMemoryStore,
    MemorySearchResult,
    STATUS_ACTIVE,
    POLARITY_PREFER,
    POLARITY_AVOID,
)
from embedding import (
    embed_query,
    is_embedding_configured,
    get_embedding_model_id,
    get_embedding_provider_kind,
)


# ─── 配置常量 ──────────────────────────────────────────

MAX_QUERY_CHARS = 800          # query 最大字符数
QUERY_HEAD_CHARS = 400         # 超长时保留前 400 字符
QUERY_TAIL_CHARS = 400         # 超长时保留后 400 字符

TOP_K = 8                      # 向量搜索候选数
SCORE_THRESHOLD = 0.32         # score 阈值（低于此分数不召回）
MIN_CONFIDENCE = 0.7           # 最低置信度

MAX_SELECTED = 3               # 最终注入上限
MAX_SINGLE_TEXT_CHARS = 300    # 单条记忆文本上限
MAX_TOTAL_CHARS = 900          # 总字符上限

SEMANTIC_TIMEOUT_MS = 1500     # 语义召回超时（毫秒）

# 当前索引版本（用于校验 semantic 元数据）
CURRENT_SEMANTIC_INDEX_VERSION = "user-memory-semantic.v1"


# ─── 配置对象 ──────────────────────────────────────────

@dataclass
class MemoryRetrievalConfig:
    """记忆召回配置"""
    top_k: int = TOP_K
    score_threshold: float = SCORE_THRESHOLD
    min_confidence: float = MIN_CONFIDENCE
    max_selected: int = MAX_SELECTED
    max_single_text_chars: int = MAX_SINGLE_TEXT_CHARS
    max_total_chars: int = MAX_TOTAL_CHARS
    timeout_ms: int = SEMANTIC_TIMEOUT_MS
    semantic_index_version: str = CURRENT_SEMANTIC_INDEX_VERSION
    semantic_embedding_model_id: str = ""
    semantic_embedding_provider_kind: str = ""

    @classmethod
    def create_default(cls) -> "MemoryRetrievalConfig":
        return cls(
            semantic_embedding_model_id=get_embedding_model_id(),
            semantic_embedding_provider_kind=get_embedding_provider_kind(),
        )


# ─── Query 规范化 ──────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """折叠多余空白：trim + 多个空白符合并为一个"""
    return re.sub(r"\s+", " ", text).strip()


def normalize_semantic_query(
    latest_user_text: str,
    config: MemoryRetrievalConfig | None = None,
) -> str:
    """
    规范化搜索 query — 只做确定性处理，不做 LLM 改写。

    - trim + 折叠多余空白
    - 超过 800 字符：保留前 400 + 后 400（用户可能把关键信息放在末尾）
    """
    if not latest_user_text:
        return ""

    normalized = normalize_whitespace(latest_user_text)

    if len(normalized) <= MAX_QUERY_CHARS:
        return normalized

    # 超长截断：前 400 + 后 400
    head = normalized[:QUERY_HEAD_CHARS]
    tail = normalized[-QUERY_TAIL_CHARS:]
    result = f"{head}{tail}"
    return result[:MAX_QUERY_CHARS]


# ─── 超时控制 ──────────────────────────────────────────

class SemanticTimeoutError(Exception):
    """语义召回超时"""
    pass


async def with_semantic_timeout(
    coro: Any,
    timeout_ms: int,
) -> Any:
    """
    用 asyncio 竞速实现超时控制。

    超时后 reject，不会无限等待。
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        raise SemanticTimeoutError("USER_MEMORY_SEMANTIC_TIMEOUT")


# ─── 6 层过滤 ──────────────────────────────────────────

@dataclass
class SemanticCandidate:
    """通过过滤的候选记忆"""
    memory: UserMemory
    score: float
    stable_key: str
    text: str
    tags: list[str]
    polarity: str
    memory_type: str


def _is_user_memory_semantic_eligible(
    memory: UserMemory,
    config: MemoryRetrievalConfig,
) -> bool:
    """
    第 1 层：基本资格检查
    - status 必须为 active
    - confidence >= 阈值
    - semantic 元数据的模型 id 和索引版本必须匹配
    """
    if memory.status != STATUS_ACTIVE:
        return False
    if memory.confidence < config.min_confidence:
        return False
    if not memory.semantic.semantic_index_version:
        return False
    if memory.semantic.semantic_index_version != config.semantic_index_version:
        return False
    # 检查 embedding 模型是否匹配（空则跳过校验）
    if config.semantic_embedding_model_id:
        if memory.semantic.embedding_model_id != config.semantic_embedding_model_id:
            return False
    return True


def _is_score_valid(score: float) -> bool:
    """
    第 2 层：score 合法性
    - 不能缺失、NaN、负数、大于 1
    """
    if score is None:
        return False
    if isinstance(score, float) and (score != score):  # NaN check
        return False
    if score < 0.0 or score > 1.0:
        return False
    return True


def _is_score_above_threshold(score: float, config: MemoryRetrievalConfig) -> bool:
    """
    第 3 层：score 阈值
    - score >= 0.32（基于中文场景校准）
    """
    return score >= config.score_threshold


# 冲突检测用的否定词 / 肯定词
NEGATION_WORDS = ["不吃", "不要", "别吃", "不喜欢", "讨厌", "拒绝", "避免", "忌口", "不要用", "别用"]
AFFIRMATION_WORDS = ["想吃", "可以吃", "喜欢", "想要", "需要", "给我", "来点", "推荐"]


def _has_polarity_conflict(
    memory: UserMemory,
    user_text: str,
) -> bool:
    """
    第 4 层：冲突检测
    - 用户当前输入和记忆 polarity 冲突时，不注入
    - 记忆是 prefer（喜欢），但用户当前说否定词 → 冲突
    - 记忆是 avoid（忌口），但用户当前说肯定词 → 冲突
    - 当前用户输入永远优先于长期记忆
    """
    if not user_text:
        return False

    lower_text = user_text.lower()

    if memory.polarity == POLARITY_PREFER:
        # 记忆是"喜欢"，用户当前是否定 → 冲突
        for word in NEGATION_WORDS:
            if word in lower_text:
                return True
    elif memory.polarity == POLARITY_AVOID:
        # 记忆是"忌口"，用户当前是肯定 → 冲突
        for word in AFFIRMATION_WORDS:
            if word in lower_text:
                return True

    return False


def _dedup_by_stable_key(
    candidates: list[SemanticCandidate],
) -> list[SemanticCandidate]:
    """
    第 5 层：stableKey 去重
    - 同 key 只保留 score 更高或 updatedAt 更新的
    """
    best: dict[str, SemanticCandidate] = {}
    for c in candidates:
        existing = best.get(c.stable_key)
        if existing is None:
            best[c.stable_key] = c
        else:
            # score 更高者优先；score 相同则 updatedAt 更新者优先
            if c.score > existing.score:
                best[c.stable_key] = c
            elif c.score == existing.score and c.memory.updated_at > existing.memory.updated_at:
                best[c.stable_key] = c
    return list(best.values())


def _resolve_conflicts(
    candidates: list[SemanticCandidate],
) -> list[SemanticCandidate]:
    """
    第 6 层：冲突记忆处理
    - type + subject + facet 相同的冲突记忆，保留更新的
    """
    best: dict[str, SemanticCandidate] = {}
    for c in candidates:
        conflict_key = f"{c.memory_type}:{c.memory.subject}:{c.memory.facet}"
        existing = best.get(conflict_key)
        if existing is None:
            best[conflict_key] = c
        else:
            # 保留 updatedAt 更新的
            if c.memory.updated_at > existing.memory.updated_at:
                best[conflict_key] = c
            elif c.memory.updated_at == existing.memory.updated_at and c.score > existing.score:
                best[conflict_key] = c
    return list(best.values())


def to_vector_semantic_candidates(
    search_items: list[MemorySearchResult],
    latest_user_text: str,
    config: MemoryRetrievalConfig,
) -> list[SemanticCandidate]:
    """
    对向量搜索结果执行 6 层过滤:

    | 层 | 过滤条件 | 滤掉什么 |
    |---|---------|---------|
    | 1 | 基本资格 | status≠active、confidence<阈值、semantic 元数据不匹配 |
    | 2 | score 合法性 | score 缺失、NaN、负数、大于 1 |
    | 3 | score 阈值 | score < 0.32 |
    | 4 | 冲突检测 | 用户当前输入和记忆 polarity 冲突 |
    | 5 | stableKey 去重 | 同 key 只保留 score 更高或 updatedAt 更新的 |
    | 6 | 冲突处理 | type+subject+facet 相同的冲突记忆，保留更新的 |
    """
    candidates: list[SemanticCandidate] = []

    for item in search_items:
        memory = item.memory
        score = item.score

        # 第 1 层：基本资格
        if not _is_user_memory_semantic_eligible(memory, config):
            continue

        # 第 2 层：score 合法性
        if not _is_score_valid(score):
            continue

        # 第 3 层：score 阈值
        if not _is_score_above_threshold(score, config):
            continue

        # 第 4 层：冲突检测
        if _has_polarity_conflict(memory, latest_user_text):
            continue

        candidates.append(SemanticCandidate(
            memory=memory,
            score=score,
            stable_key=memory.stable_key,
            text=memory.text,
            tags=memory.tags,
            polarity=memory.polarity,
            memory_type=memory.memory_type,
        ))

    # 第 5 层：stableKey 去重
    candidates = _dedup_by_stable_key(candidates)

    # 第 6 层：冲突记忆处理
    candidates = _resolve_conflicts(candidates)

    # 按 score 降序
    candidates.sort(key=lambda c: c.score, reverse=True)

    return candidates


# ─── 预算控制 ──────────────────────────────────────────

def clip_text(text: str, max_chars: int) -> str:
    """截断文本到指定长度"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


@dataclass
class SelectedUserMemory:
    """最终选中的记忆"""
    stable_key: str
    text: str
    tags: list[str]
    score: float
    memory_type: str


def select_from_semantic_candidates(
    candidates: list[SemanticCandidate],
    config: MemoryRetrievalConfig,
) -> list[SelectedUserMemory]:
    """
    最终选择 — 预算控制:
    - 最多 3 条
    - 单条 ≤ 300 字符
    - 总计 ≤ 900 字符

    防止过多长期记忆喧宾夺主，当前会话的短期上下文才是回答主要依据。
    """
    selected: list[SelectedUserMemory] = []
    total_chars = 0

    for candidate in candidates:
        if len(selected) >= config.max_selected:
            break
        if total_chars >= config.max_total_chars:
            break

        text = clip_text(candidate.text, config.max_single_text_chars)
        if not text:
            continue

        if total_chars + len(text) > config.max_total_chars:
            continue

        selected.append(SelectedUserMemory(
            stable_key=candidate.stable_key,
            text=text,
            tags=candidate.tags,
            score=candidate.score,
            memory_type=candidate.memory_type,
        ))
        total_chars += len(text)

    return selected


# ─── 主入口：retrieve_relevant_user_memories ───────────

async def vector_semantic_search(
    store: UserMemoryStore,
    namespace: str,
    query: str,
    limit: int,
) -> list[MemorySearchResult]:
    """
    向量搜索: query → embed_query → store.search(top-K)

    PostgresStore 内部自动完成三件事:
    - embed.embedQuery(query) 把 query 转成向量
    - 在向量存储里做 ANN 搜索
    - 返回最相似的 K 条，每条带 score
    """
    query_embedding = await embed_query(query)
    return await store.search(namespace, query_embedding, limit=limit)


def _log_retrieval_event(
    event_name: str,
    degradation_kind: str | None = None,
    provider_kind: str = "",
    search_mode: str = "vector",
    error_name: str = "",
    duration_ms: int | None = None,
    candidate_count: int = 0,
    selected_count: int = 0,
) -> None:
    """
    脱敏日志 — 只记录事件名、provider 类型、搜索模式、错误类别和耗时。

    绝不记录: raw query 文本、raw UserMemory 文本、embedding 向量、provider 原始响应。
    """
    log_data: dict[str, Any] = {
        "event": event_name,
        "providerKind": provider_kind,
        "searchMode": search_mode,
    }
    if degradation_kind:
        log_data["degradationKind"] = degradation_kind
    if error_name:
        log_data["errorName"] = error_name
    if duration_ms is not None:
        log_data["durationMs"] = duration_ms
    if candidate_count:
        log_data["candidateCount"] = candidate_count
    if selected_count:
        log_data["selectedCount"] = selected_count

    # 使用 print 而非 logging，与项目现有风格一致
    print(f"[user-memory] {log_data}")


async def retrieve_relevant_user_memories(
    store: UserMemoryStore,
    namespace: str,
    latest_user_text: str,
    config: MemoryRetrievalConfig | None = None,
) -> list[SelectedUserMemory]:
    """
    记忆召回主入口 — 完整流水线。

    整条链路:
      query 规范化 → 向量搜索(top-8) → 6 层过滤 → 预算控制(≤3 条)

    失败降级:
    - 1500ms 超时
    - 异常兜底（embedding 挂了、搜索出错）
    - 日志脱敏
    - 任何环节出错，返回空数组，聊天继续
    """
    cfg = config or MemoryRetrievalConfig.create_default()

    # embedding 未配置 → 直接返回空（不报错，静默降级）
    if not is_embedding_configured():
        return []

    # query 规范化
    normalized_query = normalize_semantic_query(latest_user_text, cfg)
    if not normalized_query:
        return []

    start_time = time.time()

    try:
        # 向量搜索（带超时）
        search_items = await with_semantic_timeout(
            vector_semantic_search(store, namespace, normalized_query, cfg.top_k),
            cfg.timeout_ms,
        )

        # 6 层过滤
        candidates = to_vector_semantic_candidates(
            search_items, latest_user_text, cfg,
        )

        # 预算控制
        selected = select_from_semantic_candidates(candidates, cfg)

        duration_ms = int((time.time() - start_time) * 1000)
        _log_retrieval_event(
            "semantic-retrieval-success",
            provider_kind=cfg.semantic_embedding_provider_kind,
            duration_ms=duration_ms,
            candidate_count=len(candidates),
            selected_count=len(selected),
        )

        return selected

    except Exception as error:
        duration_ms = int((time.time() - start_time) * 1000)
        degradation_kind = (
            "timeout" if isinstance(error, SemanticTimeoutError)
            else "failure"
        )
        _log_retrieval_event(
            "semantic-retrieval-degraded",
            degradation_kind=degradation_kind,
            provider_kind=cfg.semantic_embedding_provider_kind,
            error_name=type(error).__name__,
            duration_ms=duration_ms,
        )
        # 降级为 0 条，聊天继续
        return []


# ─── 上下文构建 ────────────────────────────────────────

def build_memory_context_messages(
    selected: list[SelectedUserMemory],
) -> list[dict[str, str]]:
    """
    将选中的记忆构建为模型上下文消息。

    作为补充上下文注入，不是权威答案。
    格式: system 消息，列出用户长期偏好。
    """
    if not selected:
        return []

    lines: list[str] = ["以下是关于该用户的长期偏好记忆，回答时请参考（当前对话内容优先于记忆）:"]

    for i, mem in enumerate(selected, 1):
        tags_str = f" [{', '.join(mem.tags)}]" if mem.tags else ""
        lines.append(f"  {i}. {mem.text}{tags_str}")

    return [{
        "role": "system",
        "content": "\n".join(lines),
    }]


# ─── 资格检查 ──────────────────────────────────────────

def is_user_memory_context_eligible(
    user_message: str,
    structured: dict[str, Any] | None,
) -> bool:
    """
    判断当前请求是否应该触发语义召回。

    排除路径:
    - /tasklist Agent 路径（有 structured 且包含 tasklist 命令）
    - 空消息
    - hydration / sidebar 加载（不走 chat_service.stream_chat）

    只有 ordinary_chat 和 tool_assisted_ordinary_chat 才触发。
    """
    if not user_message or not user_message.strip():
        return False

    # 检测 /tasklist 命令 → Agent 路径，不触发记忆召回
    if "/tasklist" in user_message.lower():
        return False

    # 检测结构化请求中的 tasklist 命令
    if structured:
        raw_text = structured.get("rawText", "")
        if "/tasklist" in raw_text.lower():
            return False

        segments = structured.get("segments", [])
        for seg in segments:
            if seg.get("type") == "chip" and seg.get("chipType") == "skill":
                if "tasklist" in seg.get("label", "").lower():
                    return False

    return True
