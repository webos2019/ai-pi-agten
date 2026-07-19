"""
Pi Agent — FastAPI 主应用
基于 DeepSeek (OpenAI 兼容) 的 LLM Agent 网站
"""

import os
import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 导入工具注册（触发自动注册）
import tools  # noqa: F401
from chat_service import create_chat_service
from thread_state import thread_store, session_store
from steer_queue import active_streams
from user_memory import (
    user_memory_store,
    get_memory_namespace,
    UserMemory,
    STATUS_ACTIVE,
    STATUS_SUPPRESSED,
    POLARITY_NEUTRAL,
    POLARITY_PREFER,
    POLARITY_AVOID,
)
from embedding import is_embedding_configured, get_embedding_model_id

app = FastAPI(title="Pi Agent", version="0.1.0")

# 静态文件 - React 构建产物优先，旧版静态文件作为回退
dist_dir = os.path.join(os.path.dirname(__file__), "static", "dist")
static_dir = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(dist_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="react-assets")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

chat_service = create_chat_service()


def get_client_ip(request: Request) -> str:
    """获取客户端 IP（支持反代头）"""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    ip = request.headers.get("x-client-ip")
    if ip:
        return ip
    return "127.0.0.1"


@app.get("/")
async def index():
    """主页 - 优先返回 React 构建产物，回退到旧版"""
    dist_index = os.path.join(dist_dir, "index.html")
    if os.path.isfile(dist_index):
        return FileResponse(dist_index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return JSONResponse({"error": "index.html not found"}, status_code=404)


@app.get("/assets/{filepath:path}")
async def react_assets(filepath: str):
    """React 构建资源"""
    file_path = os.path.join(dist_dir, "assets", filepath)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/chat")
async def chat(request: Request):
    """聊天 API - NDJSON 流式响应"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) == 0:
        return JSONResponse({"error": "messages 必须为非空数组"}, status_code=400)

    # 验证消息
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return JSONResponse({"error": f"messages[{i}] 必须是对象"}, status_code=400)
        role = msg.get("role")
        if role not in ("user", "assistant", "system"):
            return JSONResponse({"error": f"messages[{i}].role 无效"}, status_code=400)
        if not isinstance(msg.get("content", ""), str):
            return JSONResponse({"error": f"messages[{i}].content 必须为字符串"}, status_code=400)

    client_ip = get_client_ip(request)

    async def stream_generator():
        try:
            async for chunk_line in chat_service.stream_chat(body, client_ip):
                yield chunk_line.encode("utf-8")
        except Exception as e:
            error_chunk = json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)
            yield (error_chunk + "\n").encode("utf-8")

    return StreamingResponse(
        stream_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ─── 流式插话 (steer) API ─────────────────────────────

@app.post("/api/chat/steer")
async def steer_chat(request: Request):
    """流式插话 — 向正在进行的 Agent 流发送转向指令

    借鉴 pi.dev 的 steer 命令：
    - Agent 流式输出期间，客户端可通过此端点中途插话
    - Agent 在下一个步骤边界消费 steer，注入后续模型 prompt
    - 流结束后 steer 请求返回 409

    请求体:
      { "steerQueueId": "xxx", "steerText": "调整方向..." }
    响应:
      成功: { "ok": true, "queued": true, "steerId": "xxx", "queueSize": N }
      失败: { "ok": false, "error": "..." } (HTTP 409)
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    steer_queue_id = body.get("steerQueueId", "")
    steer_text = body.get("steerText", "")

    if not steer_queue_id:
        return JSONResponse({"ok": False, "error": "steerQueueId 必填"}, status_code=400)
    if not steer_text or not steer_text.strip():
        return JSONResponse({"ok": False, "error": "steerText 不能为空"}, status_code=400)

    success, msg, entry = active_streams.enqueue(steer_queue_id, steer_text)

    if not success:
        return JSONResponse(
            {"ok": False, "queued": False, "error": msg},
            status_code=409,
        )

    # 查询队列状态
    queue = active_streams.get(steer_queue_id)
    queue_size = queue.pending_count() if queue else 0

    return {
        "ok": True,
        "queued": True,
        "steerId": entry.id if entry else "",
        "queueSize": queue_size,
        "message": msg,
    }


@app.get("/api/chat/steer/{steer_queue_id}")
async def get_steer_queue_status(steer_queue_id: str):
    """查询 steer 队列状态"""
    queue = active_streams.get(steer_queue_id)
    if not queue:
        return JSONResponse({"error": "未找到活跃流"}, status_code=404)
    return queue.to_dto()


# ─── 会话 API (多会话短期记忆容器) ──────────────────────

@app.get("/api/conversations")
async def list_conversations(session_id: str = ""):
    """获取会话注册表 (当前浏览器会话)"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    registry = session_store.get_or_create(session_id)
    return registry.to_dto()


@app.post("/api/conversations")
async def create_conversation(request: Request):
    """创建新会话 (正式持久化, 加入注册表)"""
    body = await request.json()
    session_id = body.get("sessionId", "")
    title = body.get("title", "新对话")
    if not session_id:
        return JSONResponse({"error": "sessionId 必填"}, status_code=400)
    registry = session_store.get_or_create(session_id)
    conv = registry.create(title)
    thread_store.get_or_create(conv.thread_id)
    registry.select(conv.conversation_id)
    return {**conv.to_dto(), "threadId": conv.thread_id}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, session_id: str = ""):
    """获取会话详情 + ThreadState hydration (刷新恢复)"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    registry = session_store.get(session_id)
    if not registry:
        return {"conversationId": conversation_id, "messages": [], "summary": "", "pinnedDecisions": [], "restored": False}
    conv = registry.get(conversation_id)
    if not conv:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    state = thread_store.get(conv.thread_id)
    if not state:
        return {"conversationId": conversation_id, "messages": [], "summary": "", "pinnedDecisions": [], "restored": False}
    dto = state.to_hydration_dto()
    dto["conversationId"] = conversation_id
    dto["title"] = conv.title
    return dto


@app.patch("/api/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, request: Request):
    """切换选中会话 / 重命名 / touch 活跃时间"""
    body = await request.json()
    session_id = body.get("sessionId", "")
    if not session_id:
        return JSONResponse({"error": "sessionId 必填"}, status_code=400)
    registry = session_store.get_or_create(session_id)
    if "title" in body:
        registry.rename(conversation_id, body["title"])
    if body.get("select", False):
        if not registry.select(conversation_id):
            return JSONResponse({"error": "会话不存在"}, status_code=404)
        registry.touch(conversation_id)
    if body.get("touch", False):
        registry.touch(conversation_id)
    return registry.to_dto()


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, session_id: str = ""):
    """删除会话"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    registry = session_store.get(session_id)
    if not registry:
        return {"ok": True}
    thread_id = registry.delete(conversation_id)
    if thread_id:
        thread_store.delete(thread_id)
    return {"ok": True, "selectedConversationId": registry.selected_conversation_id}


@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "embeddingConfigured": is_embedding_configured(),
        "embeddingModel": get_embedding_model_id(),
    }


# ─── 长期用户记忆 API ─────────────────────────────────

@app.get("/api/memories")
async def list_memories(session_id: str = ""):
    """获取当前浏览器会话的所有长期记忆"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    namespace = get_memory_namespace(session_id)
    memories = user_memory_store.list_memories(namespace)
    return {
        "sessionId": session_id,
        "memories": [m.to_dto() for m in memories],
        "count": len(memories),
    }


@app.post("/api/memories")
async def create_memory(request: Request):
    """手动添加一条长期记忆

    请求体:
      {
        "sessionId": "xxx",
        "text": "用户不吃香菜",
        "tags": ["饮食", "忌口"],
        "polarity": "avoid",  // prefer | avoid | neutral
        "confidence": 0.9,
        "type": "preference",
        "subject": "饮食",
        "facet": "香菜",
        "reason": "用户明确表示"
      }
    """
    body = await request.json()
    session_id = body.get("sessionId", "")
    if not session_id:
        return JSONResponse({"error": "sessionId 必填"}, status_code=400)

    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text 必填"}, status_code=400)

    polarity = body.get("polarity", POLARITY_NEUTRAL)
    if polarity not in (POLARITY_PREFER, POLARITY_AVOID, POLARITY_NEUTRAL):
        polarity = POLARITY_NEUTRAL

    namespace = get_memory_namespace(session_id)
    memory = UserMemory(
        stable_key="",
        text=text,
        tags=body.get("tags", [])[:5],
        polarity=polarity,
        status=STATUS_ACTIVE,
        confidence=float(body.get("confidence", 0.8)),
        reason=body.get("reason", ""),
        memory_type=body.get("type", body.get("memoryType", "preference")),
        subject=body.get("subject", ""),
        facet=body.get("facet", ""),
    )

    stable_key = f"mem-manual-{int(time.time() * 1000)}"

    stored = await user_memory_store.put(
        namespace=namespace,
        key=stable_key,
        memory=memory,
        index_fields=["text", "tags"] if is_embedding_configured() else False,
    )
    return stored.to_dto()


@app.delete("/api/memories/{memory_key}")
async def delete_memory(memory_key: str, session_id: str = ""):
    """删除一条长期记忆"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    namespace = get_memory_namespace(session_id)
    deleted = user_memory_store.delete(namespace, memory_key)
    return {"ok": deleted}


@app.patch("/api/memories/{memory_key}")
async def update_memory(memory_key: str, request: Request):
    """更新记忆状态（抑制/恢复）

    请求体:
      { "sessionId": "xxx", "status": "suppressed" }
      status: active | suppressed | inactive
    """
    body = await request.json()
    session_id = body.get("sessionId", "")
    if not session_id:
        return JSONResponse({"error": "sessionId 必填"}, status_code=400)

    status = body.get("status", "")
    if status not in (STATUS_ACTIVE, STATUS_SUPPRESSED, "inactive"):
        return JSONResponse({"error": "status 必须为 active/suppressed/inactive"}, status_code=400)

    namespace = get_memory_namespace(session_id)
    memory = user_memory_store.update_status(namespace, memory_key, status)
    if not memory:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)
    return memory.to_dto()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
