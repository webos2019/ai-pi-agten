"""Autonomous Mode — 有限自治模式

借鉴 Prime Agent 的 /autonomous 设计:
- 在无人类输入时继续推进
- 质量门检查（gate command）
- 限制：最大轮次、最大 token、最大壁钟时间
- 门检查失败 → 返回输出给 agent 再试
- 避免重复运行同一个失败的门（工作区未变化时跳过）

用法:
    ctrl = get_autonomous_controller()
    ctrl.enable("session-1", AutonomousConfig(
        gate_command="python -m pytest",
        max_turns=20,
    ))
    should, reason = ctrl.should_continue("session-1")
    if should:
        gate_result = await ctrl.run_gate("session-1")
        if not gate_result["passed"]:
            # 返回门输给 agent 再试
            ...
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ─── 配置与状态 ────────────────────────────────────────

@dataclass
class AutonomousConfig:
    """自治模式配置"""
    enabled: bool = False
    max_turns: int = 20              # 最大轮次
    max_tokens: int = 200_000        # 最大 token
    max_wall_seconds: float = 600.0  # 最大壁钟时间（秒）
    gate_command: str = ""           # 质量门命令（如 "python -m pytest"）
    gate_timeout: float = 30.0       # 门命令超时（秒）


@dataclass
class AutonomousState:
    """自治模式运行时状态"""
    config: AutonomousConfig = field(default_factory=AutonomousConfig)
    turns_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.time)
    last_gate_output: str = ""
    last_gate_passed: bool = False
    last_gate_hash: str = ""        # 上次门检查时工作区的 hash
    gate_run_count: int = 0


# ─── Autonomous Controller ────────────────────────────

class AutonomousController:
    """自治模式控制器

    管理每个会话的自治模式状态
    """

    def __init__(self):
        self._states: dict[str, AutonomousState] = {}

    def enable(self, session_id: str, config: AutonomousConfig | None = None):
        """启用自治模式"""
        cfg = config or AutonomousConfig(enabled=True)
        cfg.enabled = True
        self._states[session_id] = AutonomousState(config=cfg)

    def disable(self, session_id: str):
        """禁用自治模式"""
        self._states.pop(session_id, None)

    def is_active(self, session_id: str) -> bool:
        """是否处于自治模式"""
        state = self._states.get(session_id)
        return state is not None and state.config.enabled

    def should_continue(self, session_id: str) -> tuple[bool, str]:
        """是否应该继续自治

        Returns:
            (should_continue, reason)
        """
        state = self._states.get(session_id)
        if not state or not state.config.enabled:
            return False, "autonomous mode not active"

        # 轮次限制
        if state.turns_used >= state.config.max_turns:
            return False, f"max turns reached ({state.config.max_turns})"

        # token 限制
        if state.tokens_used >= state.config.max_tokens:
            return False, f"max tokens reached ({state.config.max_tokens})"

        # 壁钟时间限制
        elapsed = time.time() - state.started_at
        if elapsed >= state.config.max_wall_seconds:
            return False, f"max wall time reached ({state.config.max_wall_seconds}s)"

        return True, "within limits"

    async def run_gate(self, session_id: str, workspace_dir: str = ".") -> dict[str, Any]:
        """运行质量门

        Returns:
            {"passed": bool, "output": str, "skipped": bool}
        """
        state = self._states.get(session_id)
        if not state or not state.config.gate_command:
            return {"passed": True, "output": "(no gate configured)", "skipped": False}

        # 计算工作区 hash — 避免重复运行相同状态的门
        ws_hash = self._workspace_hash(workspace_dir)
        if ws_hash == state.last_gate_hash and not state.last_gate_passed:
            # 工作区未变化且上次门失败 → 跳过
            return {
                "passed": False,
                "output": state.last_gate_output,
                "skipped": True,
                "reason": "workspace unchanged since last failed gate",
            }

        state.gate_run_count += 1

        try:
            proc = await asyncio.create_subprocess_shell(
                state.config.gate_command,
                cwd=workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=state.config.gate_timeout
            )
            output = stdout.decode("utf-8", errors="replace")
            passed = proc.returncode == 0

            state.last_gate_output = output[-2000:]  # 保留最后 2000 字符
            state.last_gate_passed = passed
            state.last_gate_hash = ws_hash

            return {"passed": passed, "output": output[-2000:], "skipped": False}
        except asyncio.TimeoutError:
            state.last_gate_passed = False
            state.last_gate_output = "gate timed out"
            return {"passed": False, "output": "gate timed out", "skipped": False}
        except Exception as e:
            state.last_gate_passed = False
            state.last_gate_output = str(e)
            return {"passed": False, "output": str(e), "skipped": False}

    def record_turn(self, session_id: str, tokens: int = 0):
        """记录一轮自治执行"""
        state = self._states.get(session_id)
        if state:
            state.turns_used += 1
            state.tokens_used += tokens

    def get_status(self, session_id: str) -> dict[str, Any]:
        """获取自治模式状态"""
        state = self._states.get(session_id)
        if not state:
            return {"active": False}
        return {
            "active": state.config.enabled,
            "turnsUsed": state.turns_used,
            "turnsMax": state.config.max_turns,
            "tokensUsed": state.tokens_used,
            "tokensMax": state.config.max_tokens,
            "elapsedSeconds": round(time.time() - state.started_at, 1),
            "wallMax": state.config.max_wall_seconds,
            "gate": state.config.gate_command or "(none)",
            "gateRunCount": state.gate_run_count,
            "lastGatePassed": state.last_gate_passed,
        }

    def _workspace_hash(self, dir_path: str) -> str:
        """计算工作区文件的 hash（简化版）

        只扫描代码文件，忽略 .git / node_modules / data 等
        """
        hasher = hashlib.md5()
        skip_dirs = {".git", "node_modules", "__pycache__", "data", ".venv", "venv", "dist", "build"}
        skip_exts = {".pyc", ".log", ".tmp", ".bin", ".pt", ".pptx"}

        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in sorted(files):
                if any(f.endswith(ext) for ext in skip_exts):
                    continue
                path = os.path.join(root, f)
                try:
                    stat = os.stat(path)
                    hasher.update(f"{path}:{stat.st_mtime}:{stat.st_size}".encode())
                except OSError:
                    pass
        return hasher.hexdigest()


# ─── 全局实例 ──────────────────────────────────────────

_autonomous_controller: AutonomousController | None = None


def get_autonomous_controller() -> AutonomousController:
    global _autonomous_controller
    if _autonomous_controller is None:
        _autonomous_controller = AutonomousController()
    return _autonomous_controller
