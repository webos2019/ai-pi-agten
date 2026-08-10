"""RLM Kernel — 递归语言模型持久化内核

借鉴 Prime Agent 的 RLM (Recursive Language Model) 设计:
- 持久化 Python 命名空间作为模型的唯一工具
- 变量、import、函数跨轮次存活，compaction 不清除
- rlm() 递归调用子代理，返回 admission handle（不等待结果）
- 子代理通过 agent_message 回传结果

核心不变量:
1. 执行是编程式的 — 一切通过 Python 代码完成
2. 子代理是原生 RLM 调用 — rlm() 返回 handle，不阻塞
3. 状态超越单个轮次 — 命名空间持久化

用法:
    kernel = get_kernel("session-123")
    result = await kernel.execute("x = 42; _result = x * 2")
    # 下一轮次:
    result = await kernel.execute("_result = x + 8")  # x 仍然存在
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class RLMHandle:
    """rlm() 调用返回的 admission handle

    与 Prime Agent 一致:
    - 调用立即返回，不等待子代理完成
    - 子代理在后台独立运行
    - 结果通过 agent_message 或 handle.result 获取
    """
    rlm_child_id: str
    name: str
    session_dir: str
    model: str
    status: str = "running"  # running / completed / errored
    result: str | None = None
    task: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


@dataclass
class ExecRecord:
    """执行历史记录"""
    code: str
    timestamp: float
    duration_ms: int
    success: bool
    error: str = ""


# ─── RLM Kernel ────────────────────────────────────────

class RLMKernel:
    """持久化 Python 内核 — 模型的编程界面

    核心设计:
    - _namespace: 持久化变量字典，跨轮次存活
    - _child_registry: 子代理注册表，存活于 compaction
    - _exec_history: 执行历史（用于调试和恢复）
    - _imports: 已导入的模块列表
    """

    def __init__(self, kernel_id: str = ""):
        from stream import create_id
        self.kernel_id = kernel_id or f"kernel-{create_id()}"
        self._namespace: dict[str, Any] = {}
        self._child_registry: dict[str, RLMHandle] = {}
        self._exec_history: list[ExecRecord] = []
        self._imports: list[str] = []
        self._created_at = time.time()
        self._last_active_at = time.time()

    def init(self):
        """初始化内核环境 — 预加载内置对象"""
        self._namespace["rlm"] = self.rlm
        self._namespace["list_subagents"] = self.list_subagents
        self._namespace["delete_subagent"] = self.delete_subagent
        self._namespace["kernel_info"] = self.info

    async def execute(self, code: str) -> dict[str, Any]:
        """执行 Python 代码 — 变量持久化在 _namespace 中

        这是模型唯一的工具：通过 Python 代码完成一切操作
        - 文件操作: open(), os.path, pathlib
        - Shell 命令: os.system(), subprocess
        - 工具调用: 从 tool_registry 导入
        - 子代理: rlm(...)

        Args:
            code: Python 代码字符串

        Returns:
            {"success": bool, "output": str, "variables": list, "error": str}
        """
        self._last_active_at = time.time()
        exec_start = time.time()

        try:
            # 在持久化命名空间中执行
            # 使用 exec 的两参数形式，让局部变量持久化
            exec(code, self._namespace, self._namespace)

            # 收集结果
            result_val = self._namespace.get("_result", "")
            output = str(result_val) if result_val != "" else ""

            result = {
                "success": True,
                "output": output,
                "variables": [
                    k for k in self._namespace.keys()
                    if not k.startswith("_") and k not in ("rlm", "list_subagents", "delete_subagent", "kernel_info")
                ],
            }
            self._exec_history.append(ExecRecord(
                code=code[:500],
                timestamp=exec_start,
                duration_ms=int((time.time() - exec_start) * 1000),
                success=True,
            ))
        except Exception as e:
            result = {"success": False, "output": "", "error": str(e), "variables": []}
            self._exec_history.append(ExecRecord(
                code=code[:500],
                timestamp=exec_start,
                duration_ms=int((time.time() - exec_start) * 1000),
                success=False,
                error=str(e),
            ))

        return result

    async def rlm(
        self,
        task: str,
        name: str = "child",
        model: str | None = None,
        depth: int = 0,
        agent_type: str = "research",
    ) -> RLMHandle:
        """rlm() — 递归语言模型调用

        与 Prime Agent 的 rlm() 设计一致:
        - 立即返回 admission handle，不等待子代理完成
        - 子代理在后台独立运行
        - 结果通过 handle.result 或 agent_message 获取

        Args:
            task: 子任务描述
            name: 子代理名称（用于 peer discovery）
            model: 模型名称（默认继承父代理）
            depth: 递归深度
            agent_type: 子代理类型 (research/analysis/writer/...)

        Returns:
            RLMHandle — admission handle
        """
        from stream import create_id

        child_id = create_id()
        session_dir = f"./data/rlm_sessions/{child_id}"
        os.makedirs(session_dir, exist_ok=True)

        handle = RLMHandle(
            rlm_child_id=child_id,
            name=name,
            session_dir=session_dir,
            model=model or "deepseek-chat",
            task=task,
        )
        self._child_registry[child_id] = handle

        # 异步启动子代理（不等待）
        asyncio.create_task(self._run_child(child_id, task, depth, agent_type))

        return handle

    async def _run_child(
        self,
        child_id: str,
        task: str,
        depth: int,
        agent_type: str,
    ):
        """后台运行子代理并更新 handle"""
        try:
            # 动态导入以避免循环依赖
            from sub_agent import run_sub_agent, MAX_SUB_AGENT_DEPTH

            if depth >= MAX_SUB_AGENT_DEPTH:
                handle = self._child_registry.get(child_id)
                if handle:
                    handle.status = "errored"
                    handle.result = f"递归深度超过上限 ({MAX_SUB_AGENT_DEPTH})"
                return

            result = await run_sub_agent(agent_type, task, {}, depth)

            handle = self._child_registry.get(child_id)
            if handle:
                handle.status = "completed"
                handle.result = result
                handle.completed_at = time.time()

                # 写入 session artifact
                artifact_path = os.path.join(handle.session_dir, "result.json")
                with open(artifact_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "child_id": child_id,
                        "task": task,
                        "result": result[:5000],
                        "status": "completed",
                        "duration_ms": int((handle.completed_at - handle.created_at) * 1000),
                    }, f, ensure_ascii=False, indent=2)

        except Exception as e:
            handle = self._child_registry.get(child_id)
            if handle:
                handle.status = "errored"
                handle.result = str(e)
                handle.completed_at = time.time()

    async def list_subagents(self) -> list[dict[str, Any]]:
        """列出所有子代理 — 供内核中的 Python 代码调用"""
        return [
            {
                "child_id": h.rlm_child_id,
                "name": h.name,
                "status": h.status,
                "model": h.model,
                "task": h.task[:100],
                "has_result": h.result is not None,
            }
            for h in self._child_registry.values()
        ]

    async def delete_subagent(self, handle_or_id: Any):
        """删除子代理"""
        if isinstance(handle_or_id, RLMHandle):
            child_id = handle_or_id.rlm_child_id
        else:
            child_id = str(handle_or_id)
        self._child_registry.pop(child_id, None)

    def info(self) -> dict[str, Any]:
        """内核信息"""
        return {
            "kernel_id": self.kernel_id,
            "created_at": self._created_at,
            "last_active_at": self._last_active_at,
            "namespace_size": len(self._namespace),
            "child_count": len(self._child_registry),
            "exec_count": len(self._exec_history),
            "active_children": sum(1 for h in self._child_registry.values() if h.status == "running"),
        }

    def get_namespace_snapshot(self) -> dict[str, Any]:
        """获取命名空间快照（用于 compaction 后恢复）

        只保存可序列化的变量
        """
        snapshot = {}
        for k, v in self._namespace.items():
            if k.startswith("__"):
                continue
            try:
                json.dumps(v)
                snapshot[k] = v
            except (TypeError, ValueError):
                snapshot[k] = str(v)  # 降级为字符串
        return snapshot

    def restore_namespace(self, snapshot: dict[str, Any]):
        """从快照恢复命名空间"""
        self._namespace.update(snapshot)
        self.init()  # 重新预加载内置对象

    def get_exec_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取执行历史"""
        return [
            {
                "code": r.code,
                "timestamp": r.timestamp,
                "duration_ms": r.duration_ms,
                "success": r.success,
                "error": r.error,
            }
            for r in self._exec_history[-limit:]
        ]


# ─── 全局内核管理 ──────────────────────────────────────

_rlm_kernels: dict[str, RLMKernel] = {}


def get_kernel(session_id: str) -> RLMKernel:
    """获取或创建会话的 RLM 内核"""
    if session_id not in _rlm_kernels:
        kernel = RLMKernel()
        kernel.init()
        _rlm_kernels[session_id] = kernel
    return _rlm_kernels[session_id]


def destroy_kernel(session_id: str):
    """销毁会话内核"""
    _rlm_kernels.pop(session_id, None)


def list_kernels() -> list[dict[str, Any]]:
    """列出所有活跃内核"""
    return [k.info() for k in _rlm_kernels.values()]
