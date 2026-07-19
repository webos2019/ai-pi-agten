"""聊天服务 — 会话归属解析 + 上下文构建 + 记忆写入"""

import json
from typing import Any, AsyncIterator

from chat_session import create_chat_session
from chat_orchestrator import orchestrate_chat
from stream import StreamWriter, create_ndjson_stream, create_id
from steer_queue import active_streams
from thread_state import (
    thread_store,
    session_store,
    compact_thread,
    TextCollectingWriter,
)
from user_memory import user_memory_store, get_memory_namespace
from memory_retrieval import (
    retrieve_relevant_user_memories,
    build_memory_context_messages,
    is_user_memory_context_eligible,
    MemoryRetrievalConfig,
)
from memory_extractor import extract_and_store_memories


def resolve_skill(explicit_skill: str | None, user_message: str) -> str:
    """根据用户消息自动检测技能（无显式技能时）"""
    if explicit_skill:
        return explicit_skill

    utility_keywords = ["计算", "时间", "日期", "换算", "convert", "datetime",
                        "calculator", "math", "unit", "天气", "weather", "city"]
    reader_keywords = ["文件", "读取", "目录", "read", "file", "directory", "location"]

    lower_msg = user_message.lower()
    utility_matches = sum(1 for k in utility_keywords if k in lower_msg)
    reader_matches = sum(1 for k in reader_keywords if k in lower_msg)

    if reader_matches > utility_matches or reader_matches > 0:
        return "reader-skill"
    return "utility-skill"


class ChatService:
    """聊天服务"""

    async def stream_chat(
        self,
        request_body: dict[str, Any],
        client_ip: str = "127.0.0.1",
    ) -> AsyncIterator[str]:
        """
        处理聊天请求，返回 NDJSON 流。

        集成多会话短期记忆:
        - 会话归属: createConversation (新建) 或 conversationId (已有)，互斥
        - 上下文隔离: 模型上下文只来自当前选中会话的 ThreadState
        - 写入隔离: 最终回复写入流开始时捕获的会话 (不随 UI 切换变动)
        - 流级错误不写入记忆 (安全降级)
        - 超 8 条触发 compaction
        """
        messages = request_body.get("messages", [])
        skill = request_body.get("skill")
        client_ip_req = request_body.get("clientIP") or client_ip

        # ── 多会话参数 ──
        session_id = request_body.get("sessionId", "")
        conversation_id = request_body.get("conversationId", "")
        create_conversation = request_body.get("createConversation", False)

        if not isinstance(messages, list):
            raise ValueError("messages 必须是数组")

        # 提取当前用户消息和结构化请求
        user_message = ""
        structured = None
        current_user_msg = None
        if messages:
            current_user_msg = messages[-1]
            user_message = current_user_msg.get("content", "")
            structured = current_user_msg.get("structured")

        resolved_skill = resolve_skill(skill, user_message)

        # ── 会话归属解析 (服务端校验) ──
        thread_state = None
        resolved_conversation_id = ""

        if session_id:
            registry = session_store.get_or_create(session_id)

            if create_conversation:
                # 新建会话 (首条消息触发)
                conv = registry.create(user_message[:40] if user_message else "新对话")
                thread_store.get_or_create(conv.thread_id)
                registry.select(conv.conversation_id)
                resolved_conversation_id = conv.conversation_id
                thread_state = thread_store.get(conv.thread_id)
            elif conversation_id:
                # 已有会话 — 服务端校验
                conv = registry.get(conversation_id)
                if conv:
                    resolved_conversation_id = conv.conversation_id
                    thread_state = thread_store.get_or_create(conv.thread_id)
                    registry.touch(conversation_id)

        # ── 构建模型上下文 ──
        if thread_state:
            context_messages = thread_state.build_model_context()

            # ── 长期记忆语义召回（普通聊天才触发）──
            # 借鉴掘金文章 AI Mind v0.4.6:
            #   只有 ordinary_chat 才触发，/tasklist Agent 路径不触发
            #   语义召回失败不影响聊天（降级为 0 条记忆注入）
            if session_id and is_user_memory_context_eligible(user_message, structured):
                try:
                    memory_namespace = get_memory_namespace(session_id)
                    memory_config = MemoryRetrievalConfig.create_default()
                    selected_memories = await retrieve_relevant_user_memories(
                        user_memory_store,
                        memory_namespace,
                        user_message,
                        memory_config,
                    )
                    memory_context = build_memory_context_messages(selected_memories)
                    context_messages.extend(memory_context)
                except Exception:
                    # 语义召回失败，降级为无记忆注入，聊天继续
                    pass

            if current_user_msg:
                context_messages.append(current_user_msg)
            session = create_chat_session(resolved_skill, context_messages)
        else:
            # 无会话归属，回退到前端 messages (兼容)
            session = create_chat_session(resolved_skill, messages)

        # 将结构化请求传入 context，供 Agent Runtime 检测
        agent_context: dict[str, Any] = {"clientIP": client_ip_req}
        if structured:
            agent_context["structured"] = structured

        # ── steer 队列注册 ──
        # 每个流关联一个 SteerQueue，供前端流式插话
        steer_queue_id = f"{resolved_conversation_id or 'anon'}:{create_id()}"
        steer_queue = active_streams.register(steer_queue_id)
        agent_context["steer_queue"] = steer_queue
        agent_context["steer_queue_id"] = steer_queue_id

        # ── 捕获流开始时的会话归属 (写入不串线) ──
        write_conversation_id = resolved_conversation_id
        write_thread_state = thread_state
        write_session_id = session_id

        async def on_start(writer: StreamWriter) -> None:
            collector = TextCollectingWriter(writer)
            await orchestrate_chat(session, collector, agent_context)

            # ── 回合完成后写入 ThreadState ──
            if write_thread_state and not collector.has_error():
                final_text = collector.get_collected_text()

                write_thread_state.append("user", user_message)

                if final_text.strip():
                    write_thread_state.append("assistant", final_text)

                if write_thread_state.should_compact():
                    await compact_thread(write_thread_state)

                # 持久化 ThreadState (append + compact 后统一写入 DuckDB)
                thread_store.persist_thread(write_thread_state.thread_id)

                if write_conversation_id and write_session_id:
                    reg = session_store.get(write_session_id)
                    if reg:
                        reg.touch(write_conversation_id)

            # ── 长期记忆提取（回合后，失败不影响聊天）──
            # 借鉴掘金文章 AI Mind v0.4.6:
            #   模型提取候选记忆 → 程序校验 → store.put(['text','tags'])
            #   只有普通聊天才提取，/tasklist Agent 路径不提取
            if (
                write_session_id
                and not collector.has_error()
                and is_user_memory_context_eligible(user_message, structured)
            ):
                final_text = collector.get_collected_text()
                if final_text.strip():
                    try:
                        memory_namespace = get_memory_namespace(write_session_id)
                        await extract_and_store_memories(
                            user_memory_store,
                            memory_namespace,
                            user_message,
                            final_text,
                            write_conversation_id,
                        )
                    except Exception:
                        # 记忆提取失败，静默跳过，不影响聊天
                        pass

        try:
            async for chunk_line in create_ndjson_stream(on_start):
                yield chunk_line
        finally:
            # 注销 steer 队列，拒绝所有未处理的 steer
            # 未处理的 steer 会在 /api/chat/steer 的 enqueue 中返回失败
            active_streams.unregister(steer_queue_id)


def create_chat_service() -> ChatService:
    """创建聊天服务实例"""
    return ChatService()
