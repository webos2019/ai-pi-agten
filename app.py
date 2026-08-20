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
from deepseek import get_config as get_llm_config, update_config as update_llm_config, get_presets as get_llm_presets
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
from knowledge_base import kb_store, get_kb_namespace

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


@app.get("/imag.html")
async def imag_html():
    """独立生图页面"""
    path = os.path.join(os.path.dirname(__file__), "imag.html")
    if os.path.isfile(path):
        return FileResponse(path, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "imag.html not found"}, status_code=404)


@app.get("/assets/{filepath:path}")
async def react_assets(filepath: str):
    """React 构建资源"""
    file_path = os.path.join(dist_dir, "assets", filepath)
    if os.path.isfile(file_path):
        return FileResponse(file_path, headers={
            "Cache-Control": "no-cache, must-revalidate",
        })
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
async def list_conversations(session_id: str = "", include_hidden: bool = False):
    """获取会话注册表 (当前浏览器会话)"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    registry = session_store.get_or_create(session_id)
    return registry.to_dto(include_hidden=include_hidden)


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
        # 503: 服务端 ThreadState 不可用 → 前端进入只读降级模式
        return JSONResponse(
            {"error": "ThreadState 不可用", "conversationId": conversation_id},
            status_code=503,
        )
    dto = state.to_hydration_dto()
    dto["conversationId"] = conversation_id
    dto["title"] = conv.title
    return dto


@app.patch("/api/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, request: Request):
    """切换选中会话 / 重命名 / touch 活跃时间 / 隐藏-取消隐藏"""
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
    if "hidden" in body:
        registry.set_hidden(conversation_id, bool(body["hidden"]))
    include_hidden = body.get("includeHidden", False)
    return registry.to_dto(include_hidden=include_hidden)


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


# ─── 股票分析 API ─────────────────────────────────────

@app.post("/api/stock-analysis")
async def stock_analysis(request: Request):
    """股票技术分析 + ESG 有效性评判 API

    请求体: { "code": "000725", "limit": 120 }
    响应: 完整分析报告 JSON (7因子 + ESG + 基本面)
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"error": "请提供股票代码"}, status_code=400)

    limit = body.get("limit", 120)

    # 直接调用 stock_analysis 工具
    from tools.stock_analysis import execute as stock_execute

    try:
        report = await stock_execute({"code": code, "limit": limit}, {})
        return report
    except Exception as e:
        return JSONResponse({"error": f"分析失败: {e}"}, status_code=500)


@app.get("/api/stock-analysis")
async def stock_analysis_get(code: str, limit: int = 120):
    """GET 方式调用股票分析 (便于直接 URL 测试)"""
    if not code:
        return JSONResponse({"error": "请提供股票代码"}, status_code=400)

    from tools.stock_analysis import execute as stock_execute

    try:
        report = await stock_execute({"code": code, "limit": limit}, {})
        return report
    except Exception as e:
        return JSONResponse({"error": f"分析失败: {e}"}, status_code=500)


# ─── 缠论分析 API ─────────────────────────────────────

@app.post("/api/chanlun-analysis")
async def chanlun_analysis(request: Request):
    """缠论技术面分析 API

    请求体: { "code": "000725", "limit": 120 }
    响应: 完整缠论分析 JSON (分型/笔/线段/中枢/背驰/买卖点)
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"error": "请提供股票代码"}, status_code=400)

    limit = body.get("limit", 120)

    from tools.chanlun_analysis import analyze_chanlun

    try:
        report = await analyze_chanlun(code, limit)
        return report
    except Exception as e:
        return JSONResponse({"error": f"缠论分析失败: {e}"}, status_code=500)


@app.get("/api/chanlun-analysis")
async def chanlun_analysis_get(code: str, limit: int = 120):
    """GET 方式调用缠论分析 (便于直接 URL 测试)"""
    if not code:
        return JSONResponse({"error": "请提供股票代码"}, status_code=400)

    from tools.chanlun_analysis import analyze_chanlun

    try:
        report = await analyze_chanlun(code, limit)
        return report
    except Exception as e:
        return JSONResponse({"error": f"缠论分析失败: {e}"}, status_code=500)


@app.get("/api/health")
async def health():
    """健康检查"""
    llm_cfg = get_llm_config()
    return {
        "status": "ok",
        "model": llm_cfg["model"],
        "provider": llm_cfg["provider"],
        "hasKey": llm_cfg["hasKey"],
        "embeddingConfigured": is_embedding_configured(),
        "embeddingModel": get_embedding_model_id(),
    }


@app.post("/api/restart")
async def restart_server():
    """重启后端服务 — 触发进程退出，由守护脚本自动重启"""
    import threading, os, signal

    def _delayed_restart():
        import time
        time.sleep(1)  # 等响应返回客户端
        # 发送 SIGTERM 给自己，守护脚本会自动重启
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_delayed_restart, daemon=True).start()
    return {"status": "ok", "message": "服务将在 1 秒后重启，请稍候..."}


# ─── 股票搜索 API ─────────────────────────────────────

@app.get("/api/stock-search")
async def stock_search(keyword: str):
    """股票搜索 API — 中文/拼音模糊匹配股票代码

    查询参数: keyword=茅台
    响应: { "keyword": "...", "count": N, "results": [...] }
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return JSONResponse({"error": "keyword 不能为空"}, status_code=400)

    from tools.stock_quote import execute_search

    try:
        result = await execute_search({"keyword": keyword}, {})
        return result
    except Exception as e:
        return JSONResponse({"error": f"搜索失败: {e}"}, status_code=500)


# ─── LLM 设置 API ─────────────────────────────────────

@app.get("/api/settings/llm")
async def get_llm_settings(session_id: str = ""):
    """获取当前 LLM 配置 + 预设提供商列表。

    传入 session_id 时返回 per-session 配置（多公司隔离）。
    """
    return {
        "config": get_llm_config(session_id),
        "presets": get_llm_presets(),
    }


@app.put("/api/settings/llm")
async def update_llm_settings(request: Request):
    """更新 LLM 配置
    
    请求体: { "provider": "https://...", "model": "deepseek-chat", "apiKey": "sk-...", "sessionId": "sess_xxx" }
    apiKey 传 "***" 表示不修改
    sessionId 传入时更新 per-session 配置（多公司隔离）
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)
    
    provider = body.get("provider", "")
    model = body.get("model", "")
    api_key = body.get("apiKey", "")
    session_id = body.get("sessionId", "")
    
    if not provider and not model and api_key == "***":
        return JSONResponse({"error": "至少需要提供 provider/model/apiKey 之一"}, status_code=400)
    
    updated = update_llm_config(provider, model, api_key, session_id)
    return {"ok": True, "config": updated}


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


def _get_image_providers():
    """检测当前可用的生图提供商"""
    providers = []

    # 1. Agnes AI (免费额度)
    agnes_key = os.getenv("AGNES_API_KEY", "")
    if agnes_key:
        providers.append({
            "id": "agnes",
            "label": "Agnes AI",
            "model": os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash"),
            "available": True,
            "requiresKey": True,
            "desc": "Agnes AI 免费生图 (agnes-image-2.1-flash)",
        })

    # 2. Pollinations (完全免费，无需 Key)
    providers.append({
        "id": "pollinations",
        "label": "Pollinations",
        "model": "flux",
        "available": True,
        "requiresKey": False,
        "desc": "Pollinations Flux 免费生图 (无需 API Key)",
    })

    # 3. 商汤 SenseTime
    sensetime_key = os.getenv("SENSETIME_API_KEY", "")
    if sensetime_key:
        providers.append({
            "id": "sensetime",
            "label": "商汤 SenseMirage",
            "model": os.getenv("SENSETIME_IMAGE_MODEL", "SenseMirage-Text-Image-Expert"),
            "available": True,
            "requiresKey": True,
            "desc": "商汤 SenseTime 生图 (SenseMirage)",
        })

    return providers


def _get_default_image_provider():
    """获取默认生图提供商 (优先级: Agnes > Pollinations > 商汤)"""
    agnes_key = os.getenv("AGNES_API_KEY", "")
    if agnes_key:
        return "agnes"
    return "pollinations"


@app.get("/api/text2image/status")
async def text2image_status():
    """返回可用生图提供商状态"""
    providers = _get_image_providers()
    default = _get_default_image_provider()
    return {"providers": providers, "default": default}


@app.post("/api/text2image")
async def text_to_image(request: Request):
    """文字生图 API — 多提供商支持 (Agnes AI / Pollinations / 商汤)

    请求体: { "prompt": "xxx", "size": "1024x1024", "n": 1, "provider": "auto" }
    响应: { "prompt": "xxx", "images": [{"url": "...", "type": "url"}], "model": "..." }
    """
    import httpx
    import urllib.parse
    import asyncio

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt 不能为空"}, status_code=400)

    size = body.get("size", "1024x1024")
    n = body.get("n", 1)
    provider = body.get("provider", "auto")

    # 自动选择提供商
    if provider == "auto":
        provider = _get_default_image_provider()

    # ── 1. Agnes AI ──
    if provider == "agnes":
        agnes_key = os.getenv("AGNES_API_KEY", "")
        if not agnes_key:
            # 降级到 Pollinations
            provider = "pollinations"
        else:
            agnes_url = os.getenv("AGNES_API_URL", "https://apihub.agnes-ai.com/v1/images/generations")
            agnes_model = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")

            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    resp = await client.post(
                        agnes_url,
                        headers={
                            "Authorization": f"Bearer {agnes_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": agnes_model,
                            "prompt": prompt,
                            "size": size,
                            "n": n,
                            "extra_body": {"response_format": "url"},
                        },
                    )

                if resp.status_code != 200:
                    error_detail = resp.text[:500]
                    # Agnes 失败 → 降级到 Pollinations
                    print(f"[text2image] Agnes 失败 ({resp.status_code}), 降级到 Pollinations")
                    provider = "pollinations"
                else:
                    result = resp.json()
                    images = []
                    for item in result.get("data", []):
                        if item.get("url"):
                            images.append({"url": item["url"], "type": "url"})
                        elif item.get("b64_json"):
                            images.append({"url": f"data:image/png;base64,{item['b64_json']}", "type": "base64"})

                    if images:
                        return {"prompt": prompt, "images": images, "model": agnes_model, "provider": "agnes"}
                    else:
                        print("[text2image] Agnes 未返回图片, 降级到 Pollinations")
                        provider = "pollinations"
            except Exception as e:
                print(f"[text2image] Agnes 异常: {e}, 降级到 Pollinations")
                provider = "pollinations"

    # ── 2. Pollinations (免费，无需 Key) ──
    if provider == "pollinations":
        try:
            w, h = size.split("x") if "x" in size else ("1024", "1024")
            seed = int(asyncio.get_event_loop().time() * 1000) % 1000000
            encoded_prompt = urllib.parse.quote(prompt, safe="")
            img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={w}&height={h}&model=flux&nologo=true&seed={seed}"

            return {
                "prompt": prompt,
                "images": [{"url": img_url, "type": "url"}],
                "model": "flux",
                "provider": "pollinations",
            }
        except Exception as e:
            return JSONResponse({"error": f"Pollinations 生图失败: {str(e)}"}, status_code=500)

    # ── 3. 商汤 SenseTime ──
    if provider == "sensetime":
        sensetime_api_key = os.getenv("SENSETIME_API_KEY", "")
        sensetime_api_url = os.getenv("SENSETIME_API_URL", "https://api.sensenova.cn/compatible-mode/v1/images/generations")
        sensetime_model = os.getenv("SENSETIME_IMAGE_MODEL", "SenseMirage-Text-Image-Expert")

        if not sensetime_api_key:
            return JSONResponse({"error": "商汤 API Key 未配置", "suggestion": "pollinations"}, status_code=500)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    sensetime_api_url,
                    headers={
                        "Authorization": f"Bearer {sensetime_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": sensetime_model,
                        "prompt": prompt,
                        "n": n,
                        "size": size,
                    },
                )

            if resp.status_code != 200:
                error_detail = resp.text[:500]
                return JSONResponse({"error": f"商汤 API 失败 ({resp.status_code}): {error_detail}", "suggestion": "pollinations"}, status_code=502)

            result = resp.json()
            images = []
            for item in result.get("data", []):
                if item.get("url"):
                    images.append({"url": item["url"], "type": "url"})
                elif item.get("b64_json"):
                    images.append({"url": f"data:image/png;base64,{item['b64_json']}", "type": "base64"})

            if not images:
                return JSONResponse({"error": "商汤 API 未返回图片", "suggestion": "pollinations"}, status_code=502)

            return {"prompt": prompt, "images": images, "model": sensetime_model, "provider": "sensetime"}

        except httpx.TimeoutException:
            return JSONResponse({"error": "商汤 API 超时（120s）", "suggestion": "pollinations"}, status_code=504)

    return JSONResponse({"error": f"不支持的提供商: {provider}"}, status_code=400)


# ─── OpsPilot Demo API ───────────────────────────────

@app.post("/api/opspilot-demo")
async def opspilot_demo(request: Request):
    """OpsPilot Zero Demo — 4阶段AIOps流水线

    模拟 alert-intake → rca-analyst → remediation-planner → recovery-verifier 全流程。
    使用 NDJSON 流式输出每个阶段的执行进度和结果。
    """
    import asyncio
    import time as _time

    body = await request.json()
    scenario = body.get("scenario", "502")
    target_url = body.get("targetUrl", "https://oa.example.com")

    scenarios = {
        "502": {
            "alert": "OA系统首页返回 502 Bad Gateway，用户无法访问",
            "level": "P0",
            "http_status": 502,
            "cpu": 45, "memory": 62, "disk": 71,
            "error_logs": 18,
            "root_cause": "Nginx 后端 oa-backend 进程因 OOM 被 Kill，导致 upstream 连接失败",
            "evidence": [
                "nginx/error.log: [error] upstream connect refused (111: Connection refused)",
                "oa-backend.log: [FATAL] OutOfMemoryError: Java heap space",
                "dmesg: [oom-killer] Killed process 12345 (java) total-vm:4G",
            ],
            "fix_steps": [
                {"action": "重启 oa-backend 服务", "type": "立即", "risk": "低"},
                {"action": "调整 JVM 堆内存 -Xmx2g → -Xmx4g", "type": "需审批", "risk": "中"},
                {"action": "排查内存泄漏点，添加堆内存监控", "type": "长期", "risk": "低"},
            ],
            "recovery": {"http": 200, "response_time": 320, "cpu": 38, "memory": 55, "errors": 0},
        },
        "slow": {
            "alert": "OA系统响应缓慢，核心接口平均响应时间 >5s",
            "level": "P1",
            "http_status": 200,
            "cpu": 88, "memory": 79, "disk": 65,
            "error_logs": 7,
            "root_cause": "数据库慢查询导致连接池耗尽，进而拖慢整个应用",
            "evidence": [
                "oa-app.log: [WARN] HikariPool-1 - Connection is not available, request timed out after 30000ms",
                "mysql/slow.log: SELECT * FROM oa_workflow WHERE status='pending' ORDER BY create_time DESC (3.2s)",
                "system_monitor: CPU 88%, 连接池 active=50/50 (100%)",
            ],
            "fix_steps": [
                {"action": "KILL 长时间运行的慢查询会话", "type": "立即", "risk": "中"},
                {"action": "为 oa_workflow.status 字段添加索引", "type": "需审批", "risk": "低"},
                {"action": "扩容连接池 max-pool-size 50→80", "type": "需审批", "risk": "中"},
            ],
            "recovery": {"http": 200, "response_time": 480, "cpu": 52, "memory": 68, "errors": 0},
        },
        "disk": {
            "alert": "OA服务器磁盘使用率 96%，即将写满",
            "level": "P1",
            "http_status": 200,
            "cpu": 35, "memory": 58, "disk": 96,
            "error_logs": 3,
            "root_cause": "OA应用日志未配置轮转，单文件膨胀至 85GB",
            "evidence": [
                "system_monitor: 磁盘 / 已用 96% (450GB/470GB)",
                "log_search: /opt/oa/logs/oa-app.log 大小 85GB",
                "log_search: 未发现 logrotate 配置",
            ],
            "fix_steps": [
                {"action": "截断并归档 oa-app.log（保留最近7天）", "type": "立即", "risk": "低"},
                {"action": "配置 logrotate 每日轮转+压缩+保留30天", "type": "需审批", "risk": "低"},
                {"action": "磁盘扩容 500GB→1TB", "type": "长期", "risk": "中"},
            ],
            "recovery": {"http": 200, "response_time": 180, "cpu": 32, "memory": 55, "disk": 48, "errors": 0},
        },
    }

    data = scenarios.get(scenario, scenarios["502"])

    async def stream_pipeline():
        import json

        def chunk(obj):
            return json.dumps(obj, ensure_ascii=False) + "\n"

        # ── 阶段 0: manager 启动 ──
        yield chunk({
            "stage": "manager", "status": "started",
            "message": f"运维指挥官接收告警，启动 OpsPilot 流水线",
            "alert": data["alert"], "level": data["level"],
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        await asyncio.sleep(0.3)

        # ── 阶段 1: alert-intake ──
        yield chunk({"stage": "alert-intake", "status": "started", "message": "告警接单员开始快速侦察"})
        await asyncio.sleep(0.8)

        # 调用真实工具采集系统状态
        try:
            from tools.system_monitor import execute as monitor_exec
            mon_result = await monitor_exec({}, {})
            cpu_val = mon_result.get("cpu", {}).get("cpu_usage_percent", data["cpu"])
            mem_val = mon_result.get("memory", {}).get("usage_percent", data["memory"])
            disk_val = mon_result.get("disk", {}).get("usage_percent", data["disk"])
        except Exception:
            cpu_val, mem_val, disk_val = data["cpu"], data["memory"], data["disk"]

        yield chunk({
            "stage": "alert-intake", "status": "running",
            "message": "快速侦察完成",
            "result": {
                "http_status": data["http_status"],
                "response_time": data["http_status"] == 200 and 5200 or 50,
                "cpu": cpu_val, "memory": mem_val, "disk": disk_val,
                "error_logs": data["error_logs"],
                "level": data["level"],
                "route_to": "rca-analyst",
            },
        })
        await asyncio.sleep(0.4)

        yield chunk({"stage": "alert-intake", "status": "done", "message": "告警工单已生成，路由到 rca-analyst"})
        await asyncio.sleep(0.3)

        # ── 阶段 2: rca-analyst ──
        yield chunk({"stage": "rca-analyst", "status": "started", "message": "根因分析师开始深度排查"})
        await asyncio.sleep(0.8)

        yield chunk({
            "stage": "rca-analyst", "status": "running",
            "message": "正在分析日志和数据库...",
            "evidence": data["evidence"],
        })
        await asyncio.sleep(0.8)

        yield chunk({
            "stage": "rca-analyst", "status": "done",
            "message": "根因已定位",
            "root_cause": data["root_cause"],
            "evidence_chain": data["evidence"],
        })
        await asyncio.sleep(0.3)

        # ── 阶段 3: remediation-planner ──
        yield chunk({"stage": "remediation-planner", "status": "started", "message": "修复方案规划师开始制定方案"})
        await asyncio.sleep(0.8)

        baseline = {"http": data["http_status"], "cpu": cpu_val, "memory": mem_val, "disk": disk_val, "errors": data["error_logs"]}

        yield chunk({
            "stage": "remediation-planner", "status": "running",
            "message": "制定修复方案中...",
            "baseline": baseline,
        })
        await asyncio.sleep(0.6)

        yield chunk({
            "stage": "remediation-planner", "status": "done",
            "message": "修复方案已制定",
            "fix_steps": data["fix_steps"],
            "baseline": baseline,
        })
        await asyncio.sleep(0.3)

        # ── 模拟修复执行 ──
        yield chunk({"stage": "fix-execution", "status": "started", "message": "🔧 正在执行修复操作..."})
        await asyncio.sleep(1.5)
        yield chunk({"stage": "fix-execution", "status": "done", "message": "修复操作已完成"})
        await asyncio.sleep(0.3)

        # ── 阶段 4: recovery-verifier ──
        yield chunk({"stage": "recovery-verifier", "status": "started", "message": "恢复验证员开始验证"})
        await asyncio.sleep(0.8)

        # 采集修复后真实指标
        try:
            mon_result2 = await monitor_exec({}, {})
            cpu_after = mon_result2.get("cpu", {}).get("cpu_usage_percent", data["recovery"]["cpu"])
            mem_after = mon_result2.get("memory", {}).get("usage_percent", data["recovery"]["memory"])
            disk_after = mon_result2.get("disk", {}).get("usage_percent", data["recovery"]["disk"])
        except Exception:
            cpu_after, mem_after, disk_after = data["recovery"]["cpu"], data["recovery"]["memory"], data["recovery"]["disk"]

        after = {
            "http": data["recovery"]["http"],
            "response_time": data["recovery"]["response_time"],
            "cpu": cpu_after, "memory": mem_after, "disk": disk_after,
            "errors": data["recovery"]["errors"],
        }

        yield chunk({
            "stage": "recovery-verifier", "status": "running",
            "message": "对比修复前后指标...",
            "before": baseline, "after": after,
        })
        await asyncio.sleep(0.8)

        yield chunk({
            "stage": "recovery-verifier", "status": "done",
            "message": "验证完成",
            "verdict": "✅ 恢复成功",
            "before": baseline, "after": after,
        })
        await asyncio.sleep(0.3)

        # ── manager 汇总 ──
        yield chunk({
            "stage": "manager", "status": "done",
            "message": "运维指挥官输出总报告",
            "summary": {
                "alert": data["alert"],
                "level": data["level"],
                "root_cause": data["root_cause"],
                "fix_steps": data["fix_steps"],
                "verdict": "✅ 恢复成功",
                "before": baseline, "after": after,
            },
        })

    return StreamingResponse(stream_pipeline(), media_type="application/x-ndjson")


# ─── 语音 TTS API (微软 edge-tts) ─────────────────────

@app.get("/api/tts/voices")
async def tts_voices():
    """获取可用的 TTS 语音列表"""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        # 只返回中文 + 英文的语音
        filtered = []
        for v in voices:
            locale = v.get("Locale", "")
            if locale.startswith("zh") or locale.startswith("en"):
                filtered.append({
                    "name": v.get("ShortName", ""),
                    "locale": locale,
                    "gender": v.get("Gender", ""),
                    "friendly_name": v.get("FriendlyName", ""),
                })
        return {"voices": filtered}
    except ImportError:
        return JSONResponse({"error": "edge-tts 未安装，请运行 pip install edge-tts"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"获取语音列表失败: {e}"}, status_code=500)


@app.post("/api/tts/synthesize")
async def tts_synthesize(request: Request):
    """文字转语音 — 使用微软 edge-tts 合成音频

    请求体: { "text": "你好世界", "voice": "zh-CN-XiaoxiaoNeural" }
    返回: audio/mp3 音频流
    """
    try:
        import edge_tts
    except ImportError:
        return JSONResponse({"error": "edge-tts 未安装"}, status_code=500)

    body = await request.json()
    text = body.get("text", "").strip()
    voice = body.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = body.get("rate", "+0%")      # 语速: -50% ~ +100%
    volume = body.get("volume", "+0%")   # 音量: -100% ~ +100%

    if not text:
        return JSONResponse({"error": "text 不能为空"}, status_code=400)

    if len(text) > 3000:
        return JSONResponse({"error": "文本过长（最多 3000 字）"}, status_code=400)

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)

        async def audio_stream():
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]

        return StreamingResponse(audio_stream(), media_type="audio/mpeg")
    except Exception as e:
        return JSONResponse({"error": f"语音合成失败: {e}"}, status_code=500)


# ─── 知识库 API ───────────────────────────────────────

@app.post("/api/kb/upload")
async def kb_upload(request: Request):
    """上传文档到知识库

    请求体:
      {
        "sessionId": "sess_xxx",
        "title": "产品说明书",
        "text": "...",           # 方式1: 直接传文本
        "filePath": "doc.pdf",   # 方式2: 本地文件路径 (可选)
        "url": "https://..."     # 方式3: URL (可选)
      }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    session_id = body.get("sessionId", "")
    if not session_id:
        return JSONResponse({"error": "sessionId 必填"}, status_code=400)

    namespace = get_kb_namespace(session_id)
    title = body.get("title", "未命名文档")
    source_type = "text"
    source_path = ""
    text = ""

    # 方式1: 直接传文本
    if body.get("text"):
        text = body["text"]
        source_type = "text"
    # 方式2: 本地文件
    elif body.get("filePath"):
        file_path = body["filePath"]
        source_path = file_path
        if file_path.lower().endswith(".pdf"):
            source_type = "pdf"
            try:
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                pages = []
                for page in reader.pages:
                    pages.append(page.extract_text() or "")
                text = "\n\n".join(pages)
            except Exception as e:
                return JSONResponse({"error": f"PDF 解析失败: {e}"}, status_code=500)
        else:
            source_type = "markdown"
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:
                return JSONResponse({"error": f"文件读取失败: {e}"}, status_code=500)
    # 方式3: URL
    elif body.get("url"):
        url = body["url"]
        source_path = url
        source_type = "url"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return JSONResponse({"error": f"URL 请求失败: HTTP {resp.status_code}"}, status_code=500)
                if url.lower().endswith(".pdf"):
                    source_type = "pdf"
                    import tempfile
                    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
                    with os.fdopen(fd, "wb") as f:
                        f.write(resp.content)
                    try:
                        from pypdf import PdfReader
                        reader = PdfReader(tmp_path)
                        pages = []
                        for page in reader.pages:
                            pages.append(page.extract_text() or "")
                        text = "\n\n".join(pages)
                    finally:
                        os.remove(tmp_path)
                else:
                    # 网页 → 纯文本
                    import re
                    raw = resp.text
                    raw = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<[^>]+>", "", raw)
                    text = re.sub(r"\s+", " ", raw).strip()
        except Exception as e:
            return JSONResponse({"error": f"URL 抓取失败: {e}"}, status_code=500)
    else:
        return JSONResponse({"error": "需要提供 text / filePath / url 之一"}, status_code=400)

    if not text or not text.strip():
        return JSONResponse({"error": "文档内容为空"}, status_code=400)

    # 超长截断
    max_chars = 100000
    if len(text) > max_chars:
        text = text[:max_chars]
        print(f"[knowledge-base] 文档超长，截断到 {max_chars} 字")

    try:
        doc = await kb_store.add_document(
            namespace=namespace,
            title=title,
            source_type=source_type,
            text=text,
            source_path=source_path,
        )
        return {"ok": True, "document": doc.to_api()}
    except Exception as e:
        return JSONResponse({"error": f"文档处理失败: {e}"}, status_code=500)


@app.get("/api/kb/documents")
async def kb_list_documents(session_id: str = ""):
    """列出知识库文档
    
    - 传 session_id: 返回该工作区的文档
    - 不传 session_id: 返回所有工作区的文档 (管理视角)
    """
    if session_id:
        namespace = get_kb_namespace(session_id)
        docs = kb_store.list_documents(namespace)
        return {
            "documents": [d.to_api() for d in docs],
            "count": len(docs),
        }
    else:
        # 返回所有 namespace 的文档
        all_docs = kb_store.list_all_documents()
        return {
            "documents": [
                {**doc.to_api(), "namespace": ns}
                for ns, doc in all_docs
            ],
            "count": len(all_docs),
        }


@app.get("/api/kb/documents/{doc_id}/chunks")
async def kb_get_document_chunks(doc_id: str, session_id: str = ""):
    """获取文档的所有分块内容"""
    if session_id:
        namespace = get_kb_namespace(session_id)
        chunks = kb_store.get_document_chunks(namespace, doc_id)
    else:
        # 跨 namespace 查找
        chunks = kb_store.get_document_chunks_cross_ns(doc_id)
    if not chunks:
        return JSONResponse({"error": "文档不存在或无内容"}, status_code=404)
    return {
        "docId": doc_id,
        "chunks": [
            {
                "chunkIndex": c.chunk_index,
                "text": c.text,
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


@app.delete("/api/kb/documents/{doc_id}")
async def kb_delete_document(doc_id: str, session_id: str = ""):
    """删除知识库文档及其所有分块"""
    if session_id:
        namespace = get_kb_namespace(session_id)
        deleted = kb_store.delete_document(namespace, doc_id)
    else:
        # 跨 namespace 删除
        deleted = kb_store.delete_document_cross_ns(doc_id)
    return {"ok": deleted}


@app.post("/api/kb/search")
async def kb_search(request: Request):
    """搜索知识库（调试用）"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    session_id = body.get("sessionId", "")
    query = body.get("query", "")
    if not session_id or not query:
        return JSONResponse({"error": "sessionId 和 query 必填"}, status_code=400)

    namespace = get_kb_namespace(session_id)
    results = await kb_store.search(namespace, query, limit=5)
    return {
        "results": [
            {
                "docId": r.chunk.doc_id,
                "docTitle": r.doc_title,
                "chunkIndex": r.chunk.chunk_index,
                "text": r.chunk.text[:300],
                "score": round(r.score, 4),
            }
            for r in results
        ],
        "count": len(results),
    }


@app.get("/api/kb/stats")
async def kb_stats(session_id: str = ""):
    """知识库统计"""
    if not session_id:
        return JSONResponse({"error": "session_id 必填"}, status_code=400)
    namespace = get_kb_namespace(session_id)
    stats = kb_store.get_stats(namespace)
    return {"sessionId": session_id, **stats}


# ─── 知识库外部录入接口 (简化版, 方便外部调用) ─────────

@app.post("/api/kb/ingest")
async def kb_ingest(request: Request):
    """知识库录入接口 — 供外部系统/门户页调用

    请求体:
      {
        "key": "sess_xxx",       // 知识库密钥 (session_id)
        "title": "文档标题",
        "text": "内容...",        // 方式1: 纯文本
        "url": "https://..."     // 方式2: URL (可选)
      }
    响应:
      { "ok": true, "document": { docId, title, chunkCount, ... } }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求解析失败"}, status_code=400)

    session_id = body.get("key", "") or body.get("sessionId", "")
    if not session_id:
        return JSONResponse({"error": "key (知识库密钥) 必填"}, status_code=400)

    title = body.get("title", "未命名文档")
    namespace = get_kb_namespace(session_id)

    # 方式1: 纯文本
    if body.get("text"):
        text = body["text"]
        source_type = "text"
        source_path = ""
    # 方式2: URL
    elif body.get("url"):
        url = body["url"]
        source_path = url
        source_type = "url"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return JSONResponse({"error": f"URL 请求失败: HTTP {resp.status_code}"}, status_code=500)
                if url.lower().endswith(".pdf"):
                    source_type = "pdf"
                    import tempfile
                    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
                    with os.fdopen(fd, "wb") as f:
                        f.write(resp.content)
                    try:
                        from pypdf import PdfReader
                        reader = PdfReader(tmp_path)
                        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
                    finally:
                        os.remove(tmp_path)
                else:
                    import re
                    raw = resp.text
                    raw = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<[^>]+>", "", raw)
                    text = re.sub(r"\s+", " ", raw).strip()
        except Exception as e:
            return JSONResponse({"error": f"URL 抓取失败: {e}"}, status_code=500)
    else:
        return JSONResponse({"error": "需要提供 text 或 url"}, status_code=400)

    if not text or not text.strip():
        return JSONResponse({"error": "文档内容为空"}, status_code=400)

    # 超长截断
    max_chars = 100000
    if len(text) > max_chars:
        text = text[:max_chars]

    try:
        doc = await kb_store.add_document(
            namespace=namespace,
            title=title,
            source_type=source_type,
            text=text,
            source_path=source_path,
        )
        return {"ok": True, "document": doc.to_api()}
    except Exception as e:
        return JSONResponse({"error": f"录入失败: {e}"}, status_code=500)


@app.get("/api/kb/ingest")
async def kb_ingest_portal():
    """知识库录入门户页 — 独立 HTML 页面"""
    portal_path = os.path.join(os.path.dirname(__file__), "kb-portal.html")
    if os.path.isfile(portal_path):
        return FileResponse(portal_path)
    return JSONResponse({"error": "门户页面未找到"}, status_code=404)


# ─── Prime Agent 四大特性 API ───────────────────────────
# RLM Kernel + Continual Harness + Agent Message Bus + Goal/Heartbeat/Autonomous

@app.get("/api/rlm/kernels")
async def rlm_list_kernels():
    """列出所有活跃的 RLM 内核"""
    from rlm_kernel import list_kernels
    return {"kernels": list_kernels()}


@app.get("/api/rlm/kernel/{session_id}")
async def rlm_kernel_info(session_id: str):
    """获取指定会话的 RLM 内核信息"""
    from rlm_kernel import get_kernel
    kernel = get_kernel(session_id)
    return kernel.info()


@app.post("/api/rlm/execute")
async def rlm_execute(request: Request):
    """在 RLM 内核中执行 Python 代码"""
    from rlm_kernel import get_kernel
    body = await request.json()
    session_id = body.get("session_id", "default")
    code = body.get("code", "")
    kernel = get_kernel(session_id)
    result = await kernel.execute(code)
    return result


@app.get("/api/rlm/subagents/{session_id}")
async def rlm_list_subagents(session_id: str):
    """列出会话的 RLM 子代理"""
    from rlm_kernel import get_kernel
    kernel = get_kernel(session_id)
    children = await kernel.list_subagents()
    return {"subagents": children}


# ── Harness (持续化自我改进框架) ──

@app.get("/api/harness/status")
async def harness_status():
    """获取 harness 状态"""
    from harness import get_harness
    return get_harness().get_status()


@app.get("/api/harness/snapshots")
async def harness_snapshots():
    """列出 harness 快照"""
    from harness import get_harness
    return {"snapshots": get_harness().list_snapshots()}


@app.post("/api/harness/rollback")
async def harness_rollback(request: Request):
    """回滚 harness 到指定快照"""
    from harness import get_harness
    body = await request.json()
    snapshot_id = body.get("snapshot_id")
    result = get_harness().rollback(snapshot_id)
    return result


@app.get("/api/harness/effective-prompt")
async def harness_effective_prompt():
    """获取有效系统提示（基础 + 补充 + 记忆）"""
    from harness import get_harness
    return {"prompt": get_harness().get_effective_system_prompt()}


# ── Agent Message Bus (多智能体通信) ──

@app.get("/api/agents")
async def agents_list():
    """列出所有已注册的 agents"""
    from agent_message_bus import get_message_bus
    return {"agents": get_message_bus().list_agents()}


@app.post("/api/agents/send")
async def agents_send(request: Request):
    """发送消息给另一个 agent"""
    from agent_message_bus import get_message_bus
    body = await request.json()
    bus = get_message_bus()
    receipt = await bus.send(
        message=body.get("message", ""),
        receiver_role=body.get("receiver_role", "sibling"),
        receiver_name=body.get("receiver_name", ""),
        sender_id=body.get("sender_id", ""),
        mode=body.get("mode", "auto"),
    )
    return {
        "delivered": receipt.delivered,
        "deliveryStatus": receipt.delivery_status,
        "messageId": receipt.message_id,
        "error": receipt.error,
    }


# ── Goal (持久化目标) ──

@app.post("/api/goal/create")
async def goal_create(request: Request):
    """创建持久化目标"""
    from goal_manager import get_goal_manager
    body = await request.json()
    return get_goal_manager().create(
        session_id=body.get("session_id", ""),
        objective=body.get("objective", ""),
        budget=body.get("budget", 0),
    )


@app.get("/api/goal/{session_id}")
async def goal_status(session_id: str):
    """获取目标状态"""
    from goal_manager import get_goal_manager
    mgr = get_goal_manager()
    mgr.load(session_id)
    return mgr.get_status(session_id)


@app.post("/api/goal/{session_id}/complete")
async def goal_complete(session_id: str):
    """标记目标完成"""
    from goal_manager import get_goal_manager
    return get_goal_manager().complete(session_id)


@app.post("/api/goal/{session_id}/pause")
async def goal_pause(session_id: str):
    """暂停目标"""
    from goal_manager import get_goal_manager
    return get_goal_manager().pause(session_id)


@app.post("/api/goal/{session_id}/resume")
async def goal_resume(session_id: str):
    """恢复目标"""
    from goal_manager import get_goal_manager
    return get_goal_manager().resume(session_id)


# ── Heartbeat (心跳调度) ──

@app.post("/api/heartbeat/create")
async def heartbeat_create(request: Request):
    """创建心跳"""
    from heartbeat_manager import get_heartbeat_manager
    body = await request.json()
    hb = await get_heartbeat_manager().create_heartbeat(
        session_id=body.get("session_id", ""),
        instruction=body.get("instruction", ""),
        interval=body.get("interval", "10m"),
        label=body.get("label", "default"),
        delivery_mode=body.get("delivery_mode", "steer"),
    )
    return {
        "heartbeat_id": hb.heartbeat_id,
        "instruction": hb.instruction,
        "interval_seconds": hb.interval_seconds,
        "label": hb.label,
        "status": hb.status,
    }


@app.get("/api/heartbeat/{session_id}")
async def heartbeat_list(session_id: str):
    """列出会话的心跳"""
    from heartbeat_manager import get_heartbeat_manager
    return {"heartbeats": get_heartbeat_manager().get_heartbeats(session_id)}


@app.post("/api/heartbeat/{session_id}/pause")
async def heartbeat_pause(session_id: str, request: Request):
    """暂停心跳"""
    from heartbeat_manager import get_heartbeat_manager
    body = await request.json()
    await get_heartbeat_manager().pause_heartbeat(session_id, body.get("label", "default"))
    return {"status": "paused"}


@app.post("/api/heartbeat/{session_id}/resume")
async def heartbeat_resume(session_id: str, request: Request):
    """恢复心跳"""
    from heartbeat_manager import get_heartbeat_manager
    body = await request.json()
    await get_heartbeat_manager().resume_heartbeat(session_id, body.get("label", "default"))
    return {"status": "active"}


# ── Autonomous Mode (自治模式) ──

@app.post("/api/autonomous/enable")
async def autonomous_enable(request: Request):
    """启用自治模式"""
    from autonomous_mode import get_autonomous_controller, AutonomousConfig
    body = await request.json()
    config = AutonomousConfig(
        max_turns=body.get("max_turns", 20),
        max_tokens=body.get("max_tokens", 200000),
        max_wall_seconds=body.get("max_wall_seconds", 600),
        gate_command=body.get("gate_command", ""),
    )
    get_autonomous_controller().enable(body.get("session_id", ""), config)
    return {"status": "enabled"}


@app.post("/api/autonomous/disable")
async def autonomous_disable(request: Request):
    """禁用自治模式"""
    from autonomous_mode import get_autonomous_controller
    body = await request.json()
    get_autonomous_controller().disable(body.get("session_id", ""))
    return {"status": "disabled"}


@app.get("/api/autonomous/{session_id}")
async def autonomous_status(session_id: str):
    """获取自治模式状态"""
    from autonomous_mode import get_autonomous_controller
    return get_autonomous_controller().get_status(session_id)


# ── Schedules (定时调度) ──

@app.get("/api/schedules")
async def schedules_list():
    """列出所有定时任务"""
    from heartbeat_manager import get_heartbeat_manager
    return {"schedules": get_heartbeat_manager().list_schedules()}


@app.post("/api/schedules/add")
async def schedules_add(request: Request):
    """添加定时任务"""
    from heartbeat_manager import get_heartbeat_manager
    body = await request.json()
    job = await get_heartbeat_manager().add_schedule(
        agent_id=body.get("agent_id", ""),
        prompt=body.get("prompt", ""),
        in_minutes=body.get("in_minutes"),
        cron_expr=body.get("cron_expr", ""),
    )
    return {
        "job_id": job.job_id,
        "status": job.status,
        "fire_at": job.fire_at,
    }


@app.post("/api/schedules/cancel/{job_id}")
async def schedules_cancel(job_id: str):
    """取消定时任务"""
    from heartbeat_manager import get_heartbeat_manager
    success = await get_heartbeat_manager().cancel_schedule(job_id)
    return {"success": success}


# ── Token 用量统计 API ──

@app.get("/api/token-usage/today")
async def token_usage_today(session_id: str = ""):
    """获取今日 token 用量统计

    查询参数:
      - session_id: 传入时只统计该会话 (多公司隔离)，不传则统计全局

    响应:
      {
        "date": "2026-08-13",
        "promptTokens": 12345,
        "completionTokens": 6789,
        "totalTokens": 19134,
        "requestCount": 5
      }
    """
    from token_store import get_token_store
    return get_token_store().get_today_stats(session_id)


@app.get("/api/token-usage/history")
async def token_usage_history(days: int = 7, session_id: str = ""):
    """获取最近 N 天的 token 用量历史

    查询参数:
      - days: 天数 (默认 7)
      - session_id: 传入时只统计该会话

    响应:
      [
        {"date": "2026-08-13", "promptTokens": ..., "completionTokens": ..., "totalTokens": ..., "requestCount": ...},
        ...
      ]
    """
    from token_store import get_token_store
    days = max(1, min(days, 90))  # 限制 1~90 天
    return {"days": days, "history": get_token_store().get_history(days, session_id)}


@app.get("/api/token-usage/recent")
async def token_usage_recent(limit: int = 20, session_id: str = ""):
    """获取最近的 token 用量明细记录

    查询参数:
      - limit: 最多返回条数 (默认 20，上限 100)
      - session_id: 传入时只返回该会话的记录
    """
    from token_store import get_token_store
    limit = max(1, min(limit, 100))
    return {"records": get_token_store().get_recent_records(limit, session_id)}


@app.get("/api/token-usage/total")
async def token_usage_total(session_id: str = ""):
    """获取全部历史的 token 用量汇总"""
    from token_store import get_token_store
    return get_token_store().get_total_stats(session_id)


# ── Agent Manager API — 团队工作流管理 ──

@app.get("/api/agents-mgr/snapshot")
async def agents_mgr_snapshot():
    """获取 Agent 系统快照"""
    from agent_manager import get_agent_snapshot
    return get_agent_snapshot()


@app.get("/api/agents-mgr/sub-agent-types")
async def agents_mgr_sub_agent_types():
    from agent_manager import get_sub_agent_types
    return {"types": get_sub_agent_types()}


@app.get("/api/agents-mgr/tools")
async def agents_mgr_tools():
    from agent_manager import get_registered_tools
    return {"tools": get_registered_tools()}


@app.get("/api/agents-mgr/skills")
async def agents_mgr_skills():
    from agent_manager import get_registered_skills
    return {"skills": get_registered_skills()}


@app.get("/api/agents-mgr/activities")
async def agents_mgr_activities(limit: int = 50):
    from agent_manager import get_activities
    return {"activities": get_activities(limit)}


@app.get("/api/agents-mgr/executions")
async def agents_mgr_executions():
    from agent_manager import get_executions
    return {"executions": get_executions()}


@app.post("/api/agents-mgr/execute")
async def agents_mgr_execute(request: Request):
    from agent_manager import execute_sub_agent
    body = await request.json()
    agent_type = body.get("agent_type", "")
    task = body.get("task", "")
    if not agent_type or not task:
        return JSONResponse({"error": "agent_type 和 task 必填"}, status_code=400)
    return execute_sub_agent(agent_type, task)


@app.get("/api/agents-mgr/preset-scenarios")
async def agents_mgr_preset_scenarios():
    from agent_manager import get_preset_scenarios
    return {"scenarios": get_preset_scenarios()}


@app.post("/api/agents-mgr/team-workflow")
async def agents_mgr_team_workflow(request: Request):
    from agent_manager import run_team_workflow
    body = await request.json()
    leader_task = body.get("leader_task", "")
    workers = body.get("workers", [])
    if not leader_task or not workers:
        return JSONResponse({"error": "leader_task 和 workers 必填"}, status_code=400)
    return run_team_workflow(leader_task, workers)


@app.post("/api/agents-mgr/pipeline-workflow")
async def agents_mgr_pipeline_workflow(request: Request):
    from agent_manager import run_pipeline_workflow
    body = await request.json()
    scenario_id = body.get("scenario_id", "")
    override_steps = body.get("override_steps")
    if not scenario_id:
        return JSONResponse({"error": "scenario_id 必填"}, status_code=400)
    return run_pipeline_workflow(scenario_id, override_steps)


@app.post("/api/agents-mgr/matrix-workflow")
async def agents_mgr_matrix_workflow(request: Request):
    from agent_manager import run_matrix_workflow
    body = await request.json()
    scenario_id = body.get("scenario_id", "")
    override_workers = body.get("override_workers")
    if not scenario_id:
        return JSONResponse({"error": "scenario_id 必填"}, status_code=400)
    return run_matrix_workflow(scenario_id, override_workers)


@app.get("/api/agents-mgr/teams/{team_id}")
async def agents_mgr_team_info(team_id: str):
    from agent_manager import get_team_info
    return get_team_info(team_id)


@app.get("/api/agents-mgr/teams")
async def agents_mgr_teams():
    from agent_manager import list_teams
    return {"teams": list_teams()}


# ── 聊天室 API — 团队工作流聊天室模式（多任务） ──

@app.get("/api/agents-mgr/chat-room")
async def agent_chat_room_info():
    """获取聊天室信息（成员、状态、worker 进度、当前任务）"""
    from agent_manager import get_chat_room
    return get_chat_room()


@app.get("/api/agents-mgr/chat-room/history")
async def agent_chat_room_history(limit: int = 200):
    """获取当前任务的消息历史"""
    from agent_manager import get_chat_history
    return {"messages": get_chat_history(limit)}


@app.post("/api/agents-mgr/chat-room/send")
async def agent_chat_room_send(request: Request):
    """发送消息到聊天室（异步执行，立即返回）

    消息先记录到聊天室，worker 在后台异步执行。
    前端通过轮询 /chat-room/history 获取最新消息。
    """
    from agent_manager import send_chat_message_async_bg
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "message 必填"}, status_code=400)
    # 后台异步执行，不阻塞请求
    send_chat_message_async_bg(message)
    return {"status": "ok", "message": "消息已提交，worker 正在后台处理"}


@app.post("/api/agents-mgr/chat-room/stream")
async def agent_chat_room_stream(request: Request):
    """SSE 流式发送消息到聊天室 — 实时推送 worker 状态和消息

    返回 text/event-stream，前端通过 EventSource 或 fetch 消费。
    每个事件格式: data: {"type": "message"|"worker_status"|"done"|"error", ...}\\n\\n
    """
    from agent_manager import stream_chat_room_message
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "message 必填"}, status_code=400)

    async def sse_generator():
        try:
            async for sse_line in stream_chat_room_message(message):
                yield sse_line.encode("utf-8")
        except Exception as e:
            import json as _json
            error_data = _json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n".encode("utf-8")

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@app.post("/api/agents-mgr/chat-room/stop")
async def agent_chat_room_stop():
    """停止当前正在执行的任务

    设置取消标志，worker 链在下一个检查点中断。
    已完成的结果保留，未执行的 worker 状态重置为 idle。
    """
    from agent_manager import stop_chat_task
    return stop_chat_task()


@app.post("/api/agents-mgr/chat-room/clear")
async def agent_chat_room_clear():
    """清空当前任务的消息历史"""
    from agent_manager import clear_chat_history
    return clear_chat_history()


# ── 多任务 API ──

@app.get("/api/agents-mgr/chat-room/tasks")
async def agent_chat_room_tasks():
    """获取所有任务列表"""
    from agent_manager import get_chat_tasks
    return {"tasks": get_chat_tasks()}


@app.get("/api/agents-mgr/chat-room/tasks/{task_id}")
async def agent_chat_room_task_detail(task_id: str):
    """获取单个任务详情（含消息历史）"""
    from agent_manager import get_chat_task_detail
    return get_chat_task_detail(task_id)


@app.post("/api/agents-mgr/chat-room/tasks/create")
async def agent_chat_room_task_create(request: Request):
    """创建新任务"""
    from agent_manager import create_chat_task
    body = await request.json()
    title = body.get("title", "")
    return create_chat_task(title)


@app.post("/api/agents-mgr/chat-room/tasks/select")
async def agent_chat_room_task_select(request: Request):
    """切换当前选中任务"""
    from agent_manager import select_chat_task
    body = await request.json()
    task_id = body.get("task_id", "")
    return select_chat_task(task_id)


@app.post("/api/agents-mgr/chat-room/tasks/delete")
async def agent_chat_room_task_delete(request: Request):
    """删除任务"""
    from agent_manager import delete_chat_task
    body = await request.json()
    task_id = body.get("task_id", "")
    return delete_chat_task(task_id)


@app.post("/api/agents-mgr/chat-room/tasks/rename")
async def agent_chat_room_task_rename(request: Request):
    """重命名任务"""
    from agent_manager import rename_chat_task
    body = await request.json()
    task_id = body.get("task_id", "")
    title = body.get("title", "")
    return rename_chat_task(task_id, title)


# ── Files (文件下载存储管理) ──

@app.get("/api/files/list")
async def files_list():
    """列出所有已下载的文件"""
    from tools.file_download import _ensure_downloads_dir, _format_file_size
    import os as _os
    downloads_dir = _ensure_downloads_dir()
    try:
        files = []
        for name in sorted(_os.listdir(downloads_dir)):
            filepath = _os.path.join(downloads_dir, name)
            if not _os.path.isfile(filepath):
                continue
            stat = _os.stat(filepath)
            ext = _os.path.splitext(name)[1].lower()
            files.append({
                "filename": name,
                "size": stat.st_size,
                "sizeHuman": _format_file_size(stat.st_size),
                "extension": ext,
                "createdAt": stat.st_ctime,
                "modifiedAt": stat.st_mtime,
                "downloadUrl": f"/api/files/download/{name}",
            })
        return {
            "count": len(files),
            "totalSize": sum(f["size"] for f in files),
            "totalSizeHuman": _format_file_size(sum(f["size"] for f in files)),
            "files": files,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/files/download/{filename:path}")
async def files_download(filename: str):
    """下载已存储的文件"""
    from tools.file_download import _sanitize_filename, DOWNLOADS_DIR
    import os as _os
    safe_name = _sanitize_filename(filename)
    filepath = _os.path.join(DOWNLOADS_DIR, safe_name)
    if not _os.path.isfile(filepath):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(filepath, filename=safe_name)


@app.delete("/api/files/{filename:path}")
async def files_delete(filename: str):
    """删除已下载的文件"""
    from tools.file_download import _sanitize_filename, DOWNLOADS_DIR
    import os as _os
    safe_name = _sanitize_filename(filename)
    filepath = _os.path.join(DOWNLOADS_DIR, safe_name)
    if not _os.path.isfile(filepath):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    try:
        _os.remove(filepath)
        return {"success": True, "filename": safe_name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    # reload=False: DuckDB 文件锁与 uvicorn reloader 冲突
    # 热重载时旧进程未释放 DuckDB 锁 → 新进程降级为纯内存 → 会话历史丢失
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8089,
        reload=False,
    )
