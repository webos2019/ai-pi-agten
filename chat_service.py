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
from memory_extractor import extract_and_store_memories_batch
from knowledge_extractor import extract_and_store_knowledge
from thread_state import MEMORY_EXTRACTION_BATCH_SIZE
from knowledge_base import kb_store, get_kb_namespace


def resolve_skill(explicit_skill: str | None, user_message: str) -> str:
    """根据用户消息自动检测技能（无显式技能时）

    路由优先级:
    1. 显式技能 > 一切
    2. OA 运维关键词 → oa-ops-skill
    3. 股票关键词 → stock-skill
    4. 文件下载/存储关键词 → file-skill
    5. 搜索问答关键词（知识性问题）→ search-skill
    6. 网络研究关键词 → web-skill
    7. 文件读取关键词 → reader-skill
    8. 默认 → utility-skill (已包含 web_search + web_fetch + file_download)
    """
    if explicit_skill:
        return explicit_skill

    lower_msg = user_message.lower()

    # OA 运维
    ops_keywords = ["OA", "告警", "宕机", "打不开", "502", "500", "超时",
                    "排查", "故障", "巡检", "变更", "发版", "回滚"]
    if any(kw.lower() in lower_msg for kw in ops_keywords):
        return "oa-ops-skill"

    # 股票
    stock_keywords = ["股票", "行情", "A股", "创业板", "ETF", "涨跌", "股价",
                      "市值", "涨停", "跌停", "主力", "走势", "大盘", "科创板", "stock"]
    if any(kw.lower() in lower_msg for kw in stock_keywords):
        return "stock-skill"

    # 文件下载/存储 (在 reader-skill 之前匹配，避免"文件"被路由到 reader)
    file_keywords = ["下载", "保存文件", "存储文件", "下载图片", "下载PDF",
                     "下载文档", "转存文件", "保存到本地", "下载到本地",
                     "download", "保存图片", "保存音频", "保存视频",
                     "保存这个", "保存这", "存下来", "保存网", "存到"]
    if any(kw.lower() in lower_msg for kw in file_keywords):
        return "file-skill"

    # 搜索问答（知识性问题 → 专用搜索技能）
    # 匹配模式: "什么是X" "介绍一下X" "X是什么" "X的作者" "X讲了什么" 等
    # 包含语音场景常见表达: "帮我查查" "搜一下" "找找" 等
    search_patterns = [
        "什么是", "是什么", "介绍一下", "介绍下", "告诉我",
        "详解", "百科", "概念", "原理", "历史", "人物",
        "书籍", "作者", "内容简介", "讲了什么", "怎么样",
        "解释一下", "解释下", "科普", "由来", "典故",
        "是谁", "在哪", "什么时候", "为什么",
        # 语音场景常见搜索表达
        "帮我查", "查查", "查一下", "搜一下", "搜搜",
        "找一下", "找找", "帮我搜", "帮我找",
    ]
    if any(kw in user_message for kw in search_patterns):
        return "search-skill"

    # 网络研究（显式搜索意图）
    web_keywords = ["搜索", "网络", "网页", "GitHub", "YouTube", "视频",
                    "PDF", "论文", "研究", "查找资料"]
    if any(kw.lower() in lower_msg for kw in web_keywords):
        return "web-skill"

    # 文件读取
    reader_keywords = ["文件", "读取", "目录", "read", "file", "directory", "location"]
    if any(kw in lower_msg for kw in reader_keywords):
        return "reader-skill"

    # 默认技能 (utility-skill 已包含 web_search + web_fetch + file_download，可处理知识性问题)
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
            memory_context_messages: list[dict[str, str]] = []
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
                    memory_context_messages = build_memory_context_messages(selected_memories)
                except Exception:
                    # 语义召回失败，降级为无记忆注入，聊天继续
                    pass

            # 将记忆上下文插入到历史消息之后、当前用户消息之前
            # P1 优化: 这样 [summary + pinned + 历史消息] 形成稳定前缀，
            # 可被 DeepSeek Context Cache 命中，记忆上下文的变化不会打断缓存。
            if memory_context_messages:
                for mem_msg in memory_context_messages:
                    context_messages.append(mem_msg)

            # ── 知识库语义召回（文档片段注入）──
            # 与 UserMemory 平行工作：UserMemory 召回用户偏好，KB 召回文档知识
            kb_context_messages: list[dict[str, str]] = []
            if session_id and is_user_memory_context_eligible(user_message, structured):
                try:
                    kb_namespace = get_kb_namespace(session_id)
                    kb_results = await kb_store.search(kb_namespace, user_message, limit=3)
                    if kb_results:
                        kb_text_parts = []
                        for r in kb_results:
                            snippet = r.chunk.text[:500]
                            kb_text_parts.append(
                                f"[{r.doc_title} - 片段{r.chunk_index + 1}]\n{snippet}"
                            )
                        kb_text = "\n\n".join(kb_text_parts)
                        kb_context_messages.append({
                            "role": "system",
                            "content": f"[知识库参考]\n{kb_text}",
                        })
                except Exception:
                    # 知识库召回失败，降级为无注入
                    pass

            if kb_context_messages:
                for kb_msg in kb_context_messages:
                    context_messages.append(kb_msg)

            if current_user_msg:
                context_messages.append(current_user_msg)
            session = create_chat_session(resolved_skill, context_messages, session_id)
        else:
            # 无会话归属，回退到前端 messages (兼容)
            session = create_chat_session(resolved_skill, messages, session_id)

        # 将结构化请求传入 context，供 Agent Runtime 检测
        agent_context: dict[str, Any] = {
            "clientIP": client_ip_req,
            "session_id": session_id,
            "conversation_id": resolved_conversation_id,
        }
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

            # ── 长期记忆提取（P2: 攒 N 轮批量提取，失败不影响聊天）──
            # P2 优化: 不再每轮都调 LLM 提取，而是攒满 MEMORY_EXTRACTION_BATCH_SIZE 轮后
            # 一次性批量提取，减少 66% 的 LLM 调用次数。
            if (
                write_session_id
                and not collector.has_error()
                and is_user_memory_context_eligible(user_message, structured)
            ):
                final_text = collector.get_collected_text()
                if final_text.strip():
                    # 攒入缓冲区
                    write_thread_state.pending_extraction_pairs.append(
                        (user_message, final_text)
                    )
                    write_thread_state.extraction_turn_count += 1

                    # 攒满 N 轮才批量提取
                    if write_thread_state.extraction_turn_count >= MEMORY_EXTRACTION_BATCH_SIZE:
                        try:
                            memory_namespace = get_memory_namespace(write_session_id)
                            await extract_and_store_memories_batch(
                                user_memory_store,
                                memory_namespace,
                                write_thread_state.pending_extraction_pairs,
                                write_conversation_id,
                            )
                        except Exception:
                            # 记忆提取失败，静默跳过，不影响聊天
                            pass
                        # 清空缓冲区
                        write_thread_state.pending_extraction_pairs.clear()
                        write_thread_state.extraction_turn_count = 0

            # ── 知识库自动投喂（对话结束后提取知识写入 KB）──
            # 与记忆提取平行工作：记忆提取用户偏好，知识提取提取事实性知识
            # 失败不影响聊天（静默降级）
            if (
                write_session_id
                and not collector.has_error()
                and is_user_memory_context_eligible(user_message, structured)
            ):
                final_text = collector.get_collected_text()
                if final_text.strip():
                    try:
                        await extract_and_store_knowledge(
                            write_session_id,
                            user_message,
                            final_text,
                        )
                    except Exception:
                        # 知识提取失败，静默跳过，不影响聊天
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
