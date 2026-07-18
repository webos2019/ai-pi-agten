"""流式生命周期管理 — NDJSON 流生成器"""

import json
from typing import Any, AsyncIterator, Callable, Awaitable

from .protocol import create_start_chunk, create_done_chunk, create_error_chunk


class StreamWriter:
    """流式写入器 — 暂存 chunk 到列表，供 NDJSON 生成器逐行输出"""

    def __init__(self):
        self._chunks: list[dict[str, Any]] = []

    def write_chunk(self, chunk: dict[str, Any]) -> None:
        self._chunks.append(chunk)

    def close(self) -> None:
        pass

    def get_chunks(self) -> list[dict[str, Any]]:
        return self._chunks


class StreamLifecycle:
    """流式生命周期管理器 — 保证 start/done/error 只发一次，终止后不再写入"""

    def __init__(self, writer: StreamWriter):
        self._writer = writer
        self._started = False
        self._terminated = False
        self._closed = False

    def emit_start_once(self, message_id: str) -> bool:
        if self._started or self._terminated or self._closed:
            return False
        self._started = True
        self._writer.write_chunk(create_start_chunk(message_id))
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


async def create_ndjson_stream(
    on_start: Callable[[StreamWriter], Awaitable[None]],
) -> AsyncIterator[str]:
    """
    创建 NDJSON 异步流生成器。
    on_start 回调执行编排逻辑并写入 chunk，
    随后逐行 yield JSON 字符串（以换行符结尾）。
    """
    writer = StreamWriter()
    try:
        await on_start(writer)
    except Exception as err:
        error_msg = str(err) if str(err) else "未知错误"
        writer.write_chunk({"type": "error", "error": error_msg})
    finally:
        writer.close()

    for chunk in writer.get_chunks():
        yield json.dumps(chunk, ensure_ascii=False) + "\n"
