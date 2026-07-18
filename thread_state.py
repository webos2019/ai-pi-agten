"""
ThreadState — 短期记忆系统

多会话短期记忆容器:
- 每个浏览器会话拥有多个独立会话容器 (Conversation)
- 每个会话容器对应一个独立的 ThreadState (三层记忆)
- 会话注册表管理当前浏览器会话下的会话索引 (≤10)
- 空白草稿: 新建不立即持久化, 首条消息后才创建正式会话
- 服务端校验选中会话: 模型上下文/写入均以服务端确认为准
- 流式输出中禁止切换会话 (防串线)
- 最终回复写入流开始时的会话 (不随 UI 切换变动)

三层记忆结构:
- recent messages: 最近 4 轮对话原文 (≤8 条)
- summary: 早期对话有界摘要 (≤2500 字)
- pinned_decisions: 关键决策单独保留 (≤20 条)

设计原则:
- 只存纯文本 (id, role, text, created_at), 不存工具参数/资源内容/Agent 状态
- 只在回合完成后写一次, 流式过程中不写
- 超 8 条触发 compaction, 模型生成 summary + pinned_decisions
- 工具/Agent 只存最终用户可见文本, 执行过程不进记忆
- memory 失败不影响用户已收到的回答 (安全降级)
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Any

from stream import create_id, StreamWriter
from deepseek import chat_completion


# ─── 常量 ──────────────────────────────────────────────
MAX_RECENT_MESSAGES = 8          # 最近 4 轮 = 8 条
MAX_SUMMARY_CHARS = 2500         # 摘要上限
COMPACT_TOKEN_THRESHOLD = 4800   # token 估算阈值（约 60% 上下文窗口）
TOKEN_ESTIMATE_DIVISOR = 3.5    # 字符数 / 3.5 ≈ token 数（中英混合估算）
COMPACT_COOLDOWN_MESSAGES = 4   # 压缩冷却期：压缩后至少新增 4 条才再次触发
MAX_PINNED_DECISIONS = 20        # 关键决策上限
MAX_PINNED_DECISION_CHARS = 300  # 每条决策上限
MAX_ASSISTANT_TEXT_CHARS = 8000  # 单条助手回答上限
MAX_CONVERSATIONS = 10           # 会话注册表上限
TITLE_MAX_CHARS = 40             # 会话标题上限


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class ThreadMessage:
    """纯文本消息 — 只有 id/role/text/created_at，不含运行时状态"""
    id: str
    role: str          # "user" or "assistant"
    text: str
    created_at: float


@dataclass
class ThreadState:
    """当前会话的短期记忆状态"""
    thread_id: str
    messages: list[ThreadMessage] = field(default_factory=list)
    summary: str = ""
    pinned_decisions: list[str] = field(default_factory=list)
    last_compacted_at: float = 0.0
    messages_count_at_last_compact: int = 0  # 上次压缩后的消息数（用于冷却期）

    def append(self, role: str, text: str) -> bool:
        """
        追加一条纯文本消息。
        - 去重：与最近一条完全相同则跳过 (同一轮最多写一次)
        - 截断：助手回答超长做确定性截断
        """
        if not text or not text.strip():
            return False

        if self.messages:
            last = self.messages[-1]
            if last.role == role and last.text == text:
                return False

        if role == "assistant" and len(text) > MAX_ASSISTANT_TEXT_CHARS:
            text = text[:MAX_ASSISTANT_TEXT_CHARS] + "\n\n[...内容已截断]"

        self.messages.append(ThreadMessage(
            id=create_id(),
            role=role,
            text=text,
            created_at=time.time(),
        ))
        return True

    def should_compact(self) -> bool:
        """
        是否需要触发压缩（token 感知 + 冷却期）。

        借鉴 Pi (pi.dev) 的 compaction 设计：
        - token 感知：按字符估算总 token（messages + summary + pinned），
          超过阈值才触发，避免短消息对话频繁压缩、长消息对话迟迟不压
        - 冷却期：上次压缩后至少新增 COMPACT_COOLDOWN_MESSAGES 条才再次触发
        """
        if len(self.messages) <= MAX_RECENT_MESSAGES:
            return False
        # 冷却期：压缩后未新增足够消息则不触发
        if len(self.messages) - self.messages_count_at_last_compact < COMPACT_COOLDOWN_MESSAGES:
            return False
        # token 估算：字符数 / 3.5 ≈ token 数（中英混合）
        total_chars = sum(len(m.text) for m in self.messages)
        total_chars += len(self.summary)
        total_chars += sum(len(d) for d in self.pinned_decisions)
        estimated_tokens = int(total_chars / TOKEN_ESTIMATE_DIVISOR)
        return estimated_tokens >= COMPACT_TOKEN_THRESHOLD

    def build_model_context(self) -> list[dict[str, str]]:
        """
        构建模型上下文: summary + pinned_decisions + recent messages
        返回 OpenAI 消息格式 (不含 skill system prompt，由 ChatSession 注入)
        """
        context: list[dict[str, str]] = []

        if self.summary:
            context.append({
                "role": "system",
                "content": f"[对话背景摘要]\n{self.summary}",
            })

        if self.pinned_decisions:
            pinned_text = "\n".join(f"- {d}" for d in self.pinned_decisions)
            context.append({
                "role": "system",
                "content": f"[关键决策（后续回答需遵守）]\n{pinned_text}",
            })

        for msg in self.messages:
            context.append({"role": msg.role, "content": msg.text})

        return context

    def to_hydration_dto(self) -> dict[str, Any]:
        """返回安全的 DTO 供前端恢复。只返回纯文本，不返回运行时状态。"""
        return {
            "threadId": self.thread_id,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "text": m.text,
                    "createdAt": m.created_at,
                }
                for m in self.messages
            ],
            "summary": self.summary,
            "pinnedDecisions": self.pinned_decisions,
            "restored": len(self.messages) > 0,
        }


# ─── ThreadStore — 内存存储 ────────────────────────────

class ThreadStore:
    """线程内存存储 — 服务端单例，不持久化"""

    def __init__(self):
        self._threads: dict[str, ThreadState] = {}

    def create(self) -> ThreadState:
        thread_id = create_id()
        state = ThreadState(thread_id=thread_id)
        self._threads[thread_id] = state
        return state

    def get(self, thread_id: str) -> ThreadState | None:
        return self._threads.get(thread_id)

    def get_or_create(self, thread_id: str) -> ThreadState:
        if thread_id in self._threads:
            return self._threads[thread_id]
        state = ThreadState(thread_id=thread_id)
        self._threads[thread_id] = state
        return state

    def delete(self, thread_id: str) -> None:
        self._threads.pop(thread_id, None)


# 全局单例
thread_store = ThreadStore()


# ─── 多会话容器: Conversation + ConversationRegistry ──

@dataclass
class Conversation:
    """会话元数据 — 不直接存消息，通过 thread_id 指向 ThreadState"""
    conversation_id: str
    thread_id: str
    title: str = "新对话"
    last_active_at: float = field(default_factory=time.time)
    has_messages: bool = False  # 是否已有消息 (区分草稿)

    def to_dto(self) -> dict[str, Any]:
        return {
            "conversationId": self.conversation_id,
            "title": self.title,
            "lastActiveAt": self.last_active_at,
            "hasMessages": self.has_messages,
        }


@dataclass
class ConversationRegistry:
    """会话注册表 — 当前浏览器会话下的会话索引"""
    session_id: str
    conversations: list[Conversation] = field(default_factory=list)
    selected_conversation_id: str = ""

    def list_conversations(self) -> list[Conversation]:
        """按 last_active_at 倒序返回"""
        return sorted(self.conversations, key=lambda c: c.last_active_at, reverse=True)

    def get(self, conversation_id: str) -> Conversation | None:
        for c in self.conversations:
            if c.conversation_id == conversation_id:
                return c
        return None

    def create(self, title: str = "新对话") -> Conversation:
        """创建新会话并加入注册表"""
        conv = Conversation(
            conversation_id=create_id(),
            thread_id=create_id(),
            title=title[:TITLE_MAX_CHARS],
        )
        self.conversations.append(conv)
        self._trim()
        return conv

    def touch(self, conversation_id: str) -> None:
        """更新会话最后活跃时间"""
        conv = self.get(conversation_id)
        if conv:
            conv.last_active_at = time.time()
            conv.has_messages = True

    def select(self, conversation_id: str) -> bool:
        """切换选中会话 (服务端校验)"""
        if not self.get(conversation_id):
            return False
        self.selected_conversation_id = conversation_id
        return True

    def rename(self, conversation_id: str, title: str) -> bool:
        conv = self.get(conversation_id)
        if not conv:
            return False
        conv.title = title[:TITLE_MAX_CHARS] or "未命名对话"
        return True

    def delete(self, conversation_id: str) -> str | None:
        """删除会话，返回被删除的 thread_id (供清理 ThreadState)"""
        conv = self.get(conversation_id)
        if not conv:
            return None
        self.conversations.remove(conv)
        if self.selected_conversation_id == conversation_id:
            self.selected_conversation_id = (
                self.conversations[0].conversation_id if self.conversations else ""
            )
        return conv.thread_id

    def _trim(self) -> None:
        """超过上限裁剪最久未活跃的持久化会话"""
        if len(self.conversations) <= MAX_CONVERSATIONS:
            return
        sorted_convs = self.list_conversations()
        to_remove = sorted_convs[MAX_CONVERSATIONS:]
        for c in to_remove:
            self.conversations.remove(c)
            thread_store.delete(c.thread_id)

    def to_dto(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "selectedConversationId": self.selected_conversation_id,
            "conversations": [c.to_dto() for c in self.list_conversations()],
        }


class SessionStore:
    """浏览器会话存储 — 管理多个 ConversationRegistry"""

    def __init__(self):
        self._registries: dict[str, ConversationRegistry] = {}

    def get_or_create(self, session_id: str) -> ConversationRegistry:
        if session_id not in self._registries:
            self._registries[session_id] = ConversationRegistry(session_id=session_id)
        return self._registries[session_id]

    def get(self, session_id: str) -> ConversationRegistry | None:
        return self._registries.get(session_id)


# 全局单例
session_store = SessionStore()


# ─── 压缩: compact_thread ──────────────────────────────

async def compact_thread(state: ThreadState) -> None:
    """
    压缩早期对话: 用模型生成 summary + pinned_decisions。

    - 只信任模型输出的 summary 和 pinned_decisions (经 JSON 校验)
    - recent messages 和 last_compacted_at 由本地派生
    - 失败时不覆盖已有 ThreadState，不影响用户
    """
    if len(state.messages) <= MAX_RECENT_MESSAGES:
        return

    to_compress = state.messages[:-MAX_RECENT_MESSAGES]
    if not to_compress:
        return

    conversation_text = "\n\n".join(
        f"[{'用户' if m.role == 'user' else '助手'}] {m.text}"
        for m in to_compress
    )

    existing_summary = f"\n\n已有摘要:\n{state.summary}" if state.summary else ""
    existing_pinned = ""
    if state.pinned_decisions:
        existing_pinned = "\n\n已有关键决策:\n" + "\n".join(f"- {d}" for d in state.pinned_decisions)

    system_prompt = (
        "你是一个对话压缩助手。请基于【已有摘要】和【新增早期对话】，"
        "输出一个全新的整合摘要（不超过2500字），覆盖之前的摘要，"
        "保留所有关键信息和上下文，消除多次压缩产生的冗余累积。\n\n"
        "同时提取用户明确拍板的关键决策、架构边界、重要结论"
        "（每条不超过300字，最多20条）。\n\n"
        "只返回 JSON 格式:\n"
        '{"summary": "全新的整合摘要", "pinned_decisions": ["...", "..."]}\n\n'
        "注意: summary 是对已有摘要+新对话的整合重写，不是追加拼接；"
        "pinned_decisions 只包含用户明确的决策和结论，不要包含普通对话背景。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": conversation_text + existing_summary + existing_pinned},
    ]

    try:
        response = await chat_completion(messages=messages, tools=[])
        content = response.choices[0].message.content or ""

        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            return  # 解析失败，不覆盖

        parsed = json.loads(content[start:end])
        new_summary = parsed.get("summary", "")
        new_pinned = parsed.get("pinned_decisions", [])

        # 重写摘要：用模型输出的全新整合摘要覆盖（而非拼接，避免"摘要的摘要"信息衰减）
        if new_summary:
            state.summary = new_summary[:MAX_SUMMARY_CHARS]
        # 若模型未返回 summary，保留原 summary（降级，不丢失已有背景）

        # 合并关键决策（去重）
        existing_set = set(state.pinned_decisions)
        for p in new_pinned:
            p = str(p)[:MAX_PINNED_DECISION_CHARS]
            if p not in existing_set and len(state.pinned_decisions) < MAX_PINNED_DECISIONS:
                state.pinned_decisions.append(p)
                existing_set.add(p)

        state.messages = state.messages[-MAX_RECENT_MESSAGES:]
        state.last_compacted_at = time.time()
        state.messages_count_at_last_compact = len(state.messages)  # 冷却期基准

    except Exception:
        # 压缩失败，不覆盖已有 ThreadState，不影响用户
        pass


# ─── TextCollectingWriter ──────────────────────────────

class TextCollectingWriter(StreamWriter):
    """
    包装 StreamWriter，同时收集所有 text chunk。
    用于在回合完成后提取最终助手文本，写入 ThreadState。

    - 收集 type=text 的 chunk content
    - 跟踪是否出现过 type=error 的 chunk (有错则不写入记忆)
    """

    def __init__(self, inner: StreamWriter):
        self._inner = inner
        self._text_parts: list[str] = []
        self._has_error: bool = False

    def write_chunk(self, chunk: dict[str, Any]) -> None:
        self._inner.write_chunk(chunk)
        if chunk.get("type") == "text":
            content = chunk.get("content", "")
            if content:
                self._text_parts.append(content)
        elif chunk.get("type") == "error":
            self._has_error = True

    def get_chunks(self) -> list[dict[str, Any]]:
        return self._inner.get_chunks()

    def close(self) -> None:
        self._inner.close()

    def has_error(self) -> bool:
        """是否出现过流级错误 (type=error chunk)"""
        return self._has_error

    def get_collected_text(self) -> str:
        """获取本轮所有文本 chunk 合并后的纯文本"""
        return "".join(self._text_parts)
