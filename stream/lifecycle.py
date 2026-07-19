"""流式生命周期管理 — NDJSON 实时流生成器

核心改造: StreamWriter 从"攒 list 后吐"改为基于 asyncio.Queue 的实时推送。
这样 on_start 回调里每次 write_chunk 都能立即触发 yield，
Agent 步骤轨迹和文本增量可以实时到达前端。

兼容性:
- write_chunk / close / get_chunks 接口保持不变（get_chunks 返回已写入的 list 快照）
- TextCollectingWriter 等子类无需修改
- 新增 chunks() async generator 供 create_ndjson_stream 消费
"""

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Awaitable

from .protocol import create_start_chunk, create_done_chunk, create_error_chunk


class StreamWriter:
    """流式写入器 — 基于 asyncio.Queue 实时推送 chunk

    每次 write_chunk 立即把 chunk 放入 queue，
    消费端 (create_ndjson_stream) 可实时 yield。
    同时维护一份 list 快照，兼容 get_chunks() 旧接口。
    """

    def __init__(self):
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._chunks: list[dict[str, Any]] = []  # 快照（兼容 get_chunks）
        self._closed = False

    def write_chunk(self, chunk: dict[str, Any]) -> None:
        """同步写入 chunk — 立即放入 queue 供消费端实时读取"""
        self._chunks.append(chunk)
        self._queue.put_nowait(chunk)

    async def write_chunk_async(self, chunk: dict[str, Any]) -> None:
        """异步写入 chunk（供需要 await 的场景）"""
        self._chunks.append(chunk)
        await self._queue.put(chunk)

    def close(self) -> None:
        """关闭写入器 — 放入哨兵 None 标记流结束"""
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)

    def is_closed(self) -> bool:
        return self._closed

    def get_chunks(self) -> list[dict[str, Any]]:
        """返回已写入 chunk 的 list 快照（兼容旧接口，非实时消费入口）"""
        return self._chunks

    async def chunks(self) -> AsyncIterator[dict[str, Any]]:
        """实时消费入口 — 从 queue 逐个 yield chunk，遇到 None 哨兵停止"""
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk


class StreamLifecycle:
    """流式生命周期管理器 — 保证 start/done/error 只发一次，终止后不再写入"""

    def __init__(self, writer: StreamWriter):
        self._writer = writer
        self._started = False
        self._terminated = False
        self._closed = False

    def emit_start_once(
        self,
        message_id: str,
        steer_queue_id: str | None = None,
    ) -> bool:
        if self._started or self._terminated or self._closed:
            return False
        self._started = True
        self._writer.write_chunk(create_start_chunk(message_id, steer_queue_id))
        return True

    def emit_done_once(self) -> bool:
        if self._terminated or self._closed:
            return False
        self._terminated = True
        self._writer.write_chunk(create_done_chunk())
        return True

    def emit_error_once(self, error: str) -> bool:
        if self._terminated or self._closed:
            return False
        self._terminated = True
        self._writer.write_chunk(create_error_chunk(error))
        return True

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        self._writer.close()

    def write_chunk(self, chunk: dict[str, Any]) -> None:
        if self._terminated or self._closed:
            return
        self._writer.write_chunk(chunk)


async def _run_producer(
    on_start: Callable[[StreamWriter], Awaitable[None]],
    writer: StreamWriter,
) -> None:
    """生产者任务 — 执行 on_start 回调，异常写入 error chunk，最终 close"""
    try:
        await on_start(writer)
    except Exception as err:
        error_msg = str(err) if str(err) else "未知错误"
        writer.write_chunk({"type": "error", "error": error_msg})
    finally:
        writer.close()


async def create_ndjson_stream(
    on_start: Callable[[StreamWriter], Awaitable[None]],
) -> AsyncIterator[str]:
    """
    创建 NDJSON 实时异步流生成器。

    并发模型:
    - producer task: 执行 on_start 回调，通过 writer.write_chunk 推送 chunk
    - consumer loop: 从 writer.chunks() 实时 yield JSON 字符串

    on_start 每次调用 write_chunk 都会立即触发 yield，
    不再等待 on_start 完成才输出。
    """
    writer = StreamWriter()
    producer = asyncio.create_task(_run_producer(on_start, writer))

    try:
        async for chunk in writer.chunks():
            yield json.dumps(chunk, ensure_ascii=False) + "\n"
        # 消费完所有 chunk 后，等待 producer 完成（捕获其异常）
        await producer
    except Exception as err:
        error_msg = str(err) if str(err) else "未知错误"
        yield json.dumps({"type": "error", "error": error_msg}, ensure_ascii=False) + "\n"
        if not producer.done():
            producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass
