"""流式协议 — NDJSON chunk 工厂函数

每个 chunk 是一行 JSON，通过换行符分隔形成 NDJSON 流。
chunk type 枚举:
  - start / done / error         生命周期
  - text / reasoning             文本增量
  - tool_call / tool_result      工具调用
  - resource_start / resource_end / resource_error   资源读取
  - recovering / recovery_fallback   错误恢复
  - agent_step_start / agent_step_end   Agent 步骤轨迹
"""

import time
import uuid
from typing import Any


def create_id() -> str:
    """生成短唯一 ID（毫秒时间戳 + uuid 片段）"""
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"


def create_start_chunk(message_id: str) -> dict[str, Any]:
    return {"type": "start", "messageId": message_id}


def create_text_chunk(content: str) -> dict[str, Any]:
    return {"type": "text", "content": content}


def create_reasoning_chunk(content: str) -> dict[str, Any]:
    return {"type": "reasoning", "content": content}


def create_tool_call_chunk(
    tool_call_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    server_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "tool_call",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "toolArgs": tool_args,
    }
    if server_id:
        chunk["serverId"] = server_id
    if source:
        chunk["source"] = source
    return chunk


def create_tool_result_chunk(
    tool_call_id: str,
    tool_name: str,
    tool_result: str,
    is_valid: bool = True,
    is_authoritative: bool = False,
    server_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "tool_result",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "toolResult": tool_result,
        "isValid": is_valid,
        "isAuthoritative": is_authoritative,
    }
    if server_id:
        chunk["serverId"] = server_id
    if source:
        chunk["source"] = source
    return chunk


def create_resource_start_chunk(
    resource_name: str,
    resource_uri: str,
    server_id: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "resource_start",
        "resourceName": resource_name,
        "resourceUri": resource_uri,
    }
    if server_id:
        chunk["serverId"] = server_id
    return chunk


def create_resource_end_chunk(
    resource_name: str,
    resource_uri: str,
    server_id: str | None = None,
    content_preview: str | None = None,
    is_truncated: bool = False,
    preview_chars: int | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "resource_end",
        "resourceName": resource_name,
        "resourceUri": resource_uri,
        "isTruncated": is_truncated,
    }
    if server_id:
        chunk["serverId"] = server_id
    if content_preview is not None:
        chunk["contentPreview"] = content_preview
    if preview_chars is not None:
        chunk["previewChars"] = preview_chars
    return chunk


def create_resource_error_chunk(
    resource_name: str,
    resource_uri: str,
    error: str,
    server_id: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "resource_error",
        "resourceName": resource_name,
        "resourceUri": resource_uri,
        "error": error,
    }
    if server_id:
        chunk["serverId"] = server_id
    return chunk


def create_error_chunk(
    error: str,
    retryable: bool | None = None,
    retry_delay: int | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {"type": "error", "error": error}
    if retryable is not None:
        chunk["retryable"] = retryable
    if retry_delay is not None:
        chunk["retryDelay"] = retry_delay
    return chunk


def create_recovering_chunk(message: str, attempt: int, max_attempts: int) -> dict[str, Any]:
    return {
        "type": "recovering",
        "message": message,
        "attempt": attempt,
        "maxAttempts": max_attempts,
    }


def create_recovery_fallback_chunk(message: str, fallback_method: str) -> dict[str, Any]:
    return {
        "type": "recovery_fallback",
        "message": message,
        "fallbackMethod": fallback_method,
    }


def create_done_chunk() -> dict[str, Any]:
    return {"type": "done"}


# ─── Agent Step Chunks ──────────────────────────────────

# Agent 步骤动作类型
AGENT_STEP_ACTIONS = [
    "read_resource",
    "plan_extract",
    "plan_readiness",
    "draft_tasklist",
    "validate_tasklist",
    "revise_tasklist",
    "revision_eval",
    "final_answer",
]


def create_agent_step_start_chunk(
    run_id: str,
    step_index: int,
    action_type: str,
    title: str,
    agent_name: str = "pi-agent",
    part_id: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "agent_step_start",
        "runId": run_id,
        "stepIndex": step_index,
        "actionType": action_type,
        "title": title,
        "agentName": agent_name,
    }
    if part_id:
        chunk["partId"] = part_id
    return chunk


def create_agent_step_end_chunk(
    run_id: str,
    step_index: int,
    status: str,  # "success" | "error" | "skipped"
    summary: str | None = None,
    duration_ms: int | None = None,
    part_id: str | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "type": "agent_step_end",
        "runId": run_id,
        "stepIndex": step_index,
        "status": status,
    }
    if summary is not None:
        chunk["summary"] = summary
    if duration_ms is not None:
        chunk["durationMs"] = duration_ms
    if part_id:
        chunk["partId"] = part_id
    return chunk
