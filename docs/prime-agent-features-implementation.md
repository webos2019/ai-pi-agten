# Prime Agent 四大核心特性落地方案

> 基于 [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) 的 RLM 编程模型、架构总览、长时间运行 Agent 三份核心文档，
> 结合 `newtask-pi` 现有代码结构，给出四大特性的对比分析与可落地实现。

---

## 目录

1. [现状对比总览](#1-现状对比总览)
2. [特性一：递归语言模型 (RLM) 抽象](#特性一递归语言模型-rlm-抽象)
3. [特性二：持续化自我改进框架](#特性二持续化自我改进框架)
4. [特性三：长时间运行/后台会话支持](#特性三长时间运行后台会话支持)
5. [特性四：多智能体直接通信](#特性四多智能体直接通信)
6. [集成路线图](#2-集成路线图)

---

## 1. 现状对比总览

| 特性 | Prime Agent | newtask-pi 现状 | 差距 |
|------|-------------|-----------------|------|
| **RLM 抽象** | 持久化 IPython 内核 + `rlm()` 递归调用 + 状态跨轮次存活 | `sub_agent.py` 有子代理系统（7 种类型 + 深度限制），但无持久化内核，子代理是同步等待结果 | 缺少持久化 Python 环境、异步子代理 handle、跨轮次状态存活 |
| **持续化框架** | `/refine` 审查轨迹 → 有证据更新 harness 状态（补充提示/记忆/技能描述） + 快照回滚 | `thread_state.py` 有 pinned_decisions + compaction，但无 `/refine` 机制，无法自我改进 harness | 缺少 `/refine` 命令、harness 状态管理、快照/回滚 |
| **长时间运行** | daemon-backed sessions + 心跳 + 定时调度 + 持久化目标 + 自治模式 + 自动压缩 | 有 compaction + DuckDB 持久化，但无 daemon/心跳/目标/自治模式 | 缺少后台会话、心跳、调度、目标、自治模式 |
| **多智能体通信** | `agent_message.send()` 在运行中的 agent 间直接路由消息（steer/follow_up/auto 三种模式） + family roster | 子代理是单向的（父→子→父结果），无 peer-to-peer 通信 | 缺少 agent 间直接消息、peer discovery、消息路由 |

---

## 特性一：递归语言模型 (RLM) 抽象

### Prime Agent 设计

```
┌─────────────────────────────────────────────────────┐
│                    RLM Loop                          │
│                                                      │
│  Task ──→ Parent Model ──→ IPython Call ──→ Kernel  │
│                            (持久化)                   │
│                                │                     │
│                    ┌───────────┼───────────┐        │
│                    ▼           ▼           ▼        │
│               Files/Data   Skills     rlm(...)      │
│               (inspect)   (call)     (child agents)  │
│                    │           │           │         │
│                    └───────────┴───────────┘        │
│                                │                     │
│                          Admission Handle            │
│                                │                     │
│                          Parent Model                │
│                                │                     │
│                            Answer                    │
└─────────────────────────────────────────────────────┘
```

**核心不变量：**

1. **执行是编程式的** — IPython 是唯一的内置模型工具，文件操作/Shell 命令/工具调用/子代理都通过 Python 代码完成
2. **子代理是原生 RLM 调用** — `rlm(...)` 返回 admission handle（不等待结果），子代理通过 `agent_message` 回复
3. **技能增加编程能力** — Python-backed skills 是可导入的包
4. **状态设计为超越单个轮次** — 变量、import、函数跨轮次存活

### newtask-pi 现状

```python
# sub_agent.py — 当前的子代理是同步的
result = await run_sub_agent("research", "搜索XX", context, depth)
# ↑ 父代理等待子代理完成后才继续
```

### 实现方案

#### 1.1 持久化 Python 内核 (`rlm_kernel.py`)

```python
"""RLM Kernel — 持久化 Python 控制环境

借鉴 Prime Agent 的 RLM 设计：
- IPython 作为唯一的模型工具
- 变量、import、函数跨轮次存活
- compaction 不清除内核状态
- rlm() 递归调用子代理
"""

from __future__ import annotations
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class RLMHandle:
    """rlm() 调用返回的 admission handle — 不等待子代理完成"""
    rlm_child_id: str
    name: str
    session_dir: str
    model: str
    status: str = "running"  # running / completed / errored
    result: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class RLMKernel:
    """持久化 Python 内核 — 模型的编程界面

    核心设计:
    - _namespace: 持久化变量字典，跨轮次存活
    - _child_registry: 子代理注册表，存活于 compaction / restart
    - _exec_history: 执行历史（用于调试和恢复）
    """

    kernel_id: str = field(default_factory=lambda: f"kernel-{int(time.time()*1000)}")
    _namespace: dict[str, Any] = field(default_factory=dict)
    _child_registry: dict[str, RLMHandle] = field(default_factory=dict)
    _exec_history: list[dict[str, Any]] = field(default_factory=list)

    def init(self):
        """初始化内核环境 — 预加载 rlm 和 agent_message"""
        self._namespace["rlm"] = self.rlm
        self._namespace["agent_message"] = self.agent_message
        self._namespace["compact"] = self.compact
        self._namespace["goal"] = self.goal

    async def execute(self, code: str) -> dict[str, Any]:
        """执行 Python 代码 — 变量持久化在 _namespace 中

        这是模型唯一的工具：通过 Python 代码完成一切操作
        """
        exec_start = time.time()
        try:
            # 在持久化命名空间中执行
            local_ns = self._namespace.copy()
            exec(code, local_ns, local_ns)
            # 合并回持久化命名空间
            self._namespace.update(local_ns)

            result = {
                "success": True,
                "output": str(local_ns.get("_result", "")),
                "variables": list(local_ns.keys()),
            }
        except Exception as e:
            result = {"success": False, "error": str(e)}
        finally:
            self._exec_history.append({
                "code": code[:500],
                "timestamp": exec_start,
                "duration_ms": int((time.time() - exec_start) * 1000),
                "success": result["success"],
            })

        return result

    async def rlm(
        self,
        task: str,
        name: str = "child",
        model: str | None = None,
        depth: int = 0,
    ) -> RLMHandle:
        """rlm() — 递归语言模型调用

        与 Prime Agent 一致:
        - 立即返回 admission handle，不等待子代理完成
        - 子代理在后台独立运行
        - 结果通过 agent_message 回传
        """
        from stream import create_id
        child_id = create_id()
        handle = RLMHandle(
            rlm_child_id=child_id,
            name=name,
            session_dir=f"./data/rlm_sessions/{child_id}",
            model=model or "deepseek-chat",
        )
        self._child_registry[child_id] = handle

        # 异步启动子代理（不等待）
        asyncio.create_task(self._run_child(child_id, task, depth))

        return handle

    async def _run_child(self, child_id: str, task: str, depth: int):
        """后台运行子代理"""
        from sub_agent import run_sub_agent
        try:
            result = await run_sub_agent("research", task, {}, depth)
            handle = self._child_registry.get(child_id)
            if handle:
                handle.status = "completed"
                handle.result = result
        except Exception as e:
            handle = self._child_registry.get(child_id)
            if handle:
                handle.status = "errored"
                handle.result = str(e)

    async def list_subagents(self) -> list[RLMHandle]:
        """列出所有子代理"""
        return list(self._child_registry.values())

    async def delete_subagent(self, handle: RLMHandle):
        """删除子代理"""
        self._child_registry.pop(handle.rlm_child_id, None)

    def get_namespace(self) -> dict[str, Any]:
        """获取当前命名空间（用于 compaction 后恢复）"""
        return self._namespace.copy()

    def restore_namespace(self, ns: dict[str, Any]):
        """从快照恢复命名空间"""
        self._namespace.update(ns)
        # 重新预加载内置对象
        self.init()

    # ── 内置 skill 对象 ──

    async def agent_message(self, *args, **kwargs):
        """agent_message skill — 多智能体通信（见特性四）"""
        from agent_message_bus import agent_message_bus
        return await agent_message_bus.send(*args, **kwargs)

    async def compact(self, *args, **kwargs):
        """compact skill — 上下文压缩"""
        return {"status": "compacted", "namespace_preserved": True}

    async def goal(self, *args, **kwargs):
        """goal skill — 持久化目标"""
        from goal_manager import goal_manager
        return await goal_manager.handle(*args, **kwargs)


# 全局内核实例（每个会话一个）
_rlm_kernels: dict[str, RLMKernel] = {}


def get_kernel(session_id: str) -> RLMKernel:
    """获取或创建会话的 RLM 内核"""
    if session_id not in _rlm_kernels:
        kernel = RLMKernel()
        kernel.init()
        _rlm_kernels[session_id] = kernel
    return _rlm_kernels[session_id]
```

#### 1.2 集成到 agent_loop — 将 `ipython` 作为模型工具

```python
# 在 chat_orchestrator.py 中新增 ipython 工具

async def _ipython_tool_handler(args: dict[str, Any], context: dict[str, Any]) -> str:
    """IPython 工具 — 模型通过 Python 代码完成一切操作"""
    session_id = context.get("_session_id", "default")
    kernel = get_kernel(session_id)
    code = args.get("code", "")
    result = await kernel.execute(code)
    return json.dumps(result, ensure_ascii=False)

# 注册为工具
IPYTHON_TOOL = ChatToolDefinition(
    name="ipython",
    description="在持久化 Python 内核中执行代码。变量、import、函数跨轮次存活。",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python 代码"},
        },
        "required": ["code"],
    },
    handler=_ipython_tool_handler,
)
```

---

## 特性二：持续化自我改进框架

### Prime Agent 设计

```
/refine 命令
    │
    ▼
审查当前轨迹 (transcript)
    │
    ▼
识别可改进的模式 ──→ 提取有证据的教训
    │
    ▼
更新 harness 状态:
    ├─ supplemental_prompts (补充提示)
    ├─ memories (记忆)
    ├─ skill_descriptions (技能描述)
    └─ subagent_specs (子代理规格)
    │
    ▼
快照保存 ──→ 支持回滚
    │
    ▼
下次会话自动加载更新的 harness
```

**关键约束：**
- 永不重写不可变的底层系统提示
- 更新必须是小而有证据支撑的
- 快照支持回滚

### newtask-pi 现状

`thread_state.py` 已有：
- `pinned_decisions` — 关键决策保留（≤20 条）
- `summary` — 早期对话摘要
- `compaction` — 自动压缩

但缺少：
- `/refine` 主动审查机制
- harness 状态管理
- 快照/回滚

### 实现方案

#### 2.1 Harness 状态管理 (`harness.py`)

```python
"""Continual Harness — 持续化自我改进框架

借鉴 Prime Agent 的 Continual Harness 设计：
- /refine 审查当前轨迹 → 提取有证据的教训 → 更新 harness 状态
- harness 状态包括：补充提示、记忆、技能描述、子代理规格
- 快照支持回滚
- 永不重写底层系统提示
"""

from __future__ import annotations
import json
import time
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HarnessSnapshot:
    """harness 快照 — 用于回滚"""
    snapshot_id: str
    timestamp: float
    supplemental_prompts: list[str]
    memories: list[dict[str, Any]]
    skill_updates: dict[str, str]
    subagent_specs: dict[str, dict[str, Any]]
    refine_reason: str


@dataclass
class ContinualHarness:
    """持续化框架状态

    四类可更新状态:
    - supplemental_prompts: 补充到系统提示末尾的指令
    - memories: 跨会话记忆（比 pinned_decisions 更结构化）
    - skill_updates: 技能描述的微调
    - subagent_specs: 子代理规格的微调
    """

    # 不可变的基础系统提示（永不重写）
    base_system_prompt: str = ""

    # 可更新的补充状态
    supplemental_prompts: list[str] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    skill_updates: dict[str, str] = field(default_factory=dict)
    subagent_specs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 快照历史
    _snapshots: list[HarnessSnapshot] = field(default_factory=list)
    _max_snapshots: int = 20

    # 存储路径
    _storage_dir: str = "./data/harness"

    def get_effective_system_prompt(self) -> str:
        """获取有效系统提示 = 基础提示 + 补充提示"""
        parts = [self.base_system_prompt]
        if self.supplemental_prompts:
            parts.append("\n\n## 补充指令（由 /refine 生成）\n")
            parts.append("\n".join(f"- {p}" for p in self.supplemental_prompts))
        if self.memories:
            parts.append("\n\n## 框架记忆\n")
            for m in self.memories[-5:]:  # 最近 5 条
                parts.append(f"- [{m.get('category', 'general')}] {m['content']}")
        return "\n".join(parts)

    def refine(
        self,
        transcript: list[dict[str, str]],
        llm_refine_result: dict[str, Any],
    ) -> str:
        """执行一次 /refine 更新

        Args:
            transcript: 当前会话轨迹
            llm_refine_result: LLM 分析后的结构化改进建议
                {
                    "lessons": [{"category": "...", "content": "...", "evidence": "..."}],
                    "supplemental_prompts": ["..."],
                    "skill_updates": {"skill_name": "new_description"},
                }

        Returns:
            更新摘要
        """
        # 保存快照（回滚用）
        self._save_snapshot(llm_refine_result.get("reason", "manual refine"))

        # 应用更新
        for prompt in llm_refine_result.get("supplemental_prompts", []):
            if prompt not in self.supplemental_prompts:
                self.supplemental_prompts.append(prompt)

        for lesson in llm_refine_result.get("lessons", []):
            self.memories.append({
                "content": lesson["content"],
                "category": lesson.get("category", "general"),
                "evidence": lesson.get("evidence", ""),
                "timestamp": time.time(),
            })

        for skill_name, new_desc in llm_refine_result.get("skill_updates", {}).items():
            self.skill_updates[skill_name] = new_desc

        # 持久化
        self._persist()

        # 返回摘要
        return (
            f"✅ Harness 已更新:\n"
            f"  - 补充提示: +{len(llm_refine_result.get('supplemental_prompts', []))}\n"
            f"  - 记忆: +{len(llm_refine_result.get('lessons', []))}\n"
            f"  - 技能更新: +{len(llm_refine_result.get('skill_updates', {}))}\n"
            f"  - 快照: {self._snapshots[-1].snapshot_id}"
        )

    def rollback(self, snapshot_id: str | None = None) -> bool:
        """回滚到指定快照（默认回滚到上一个）"""
        if not self._snapshots:
            return False
        if snapshot_id:
            target = next((s for s in self._snapshots if s.snapshot_id == snapshot_id), None)
        else:
            target = self._snapshots[-1]
        if not target:
            return False
        self.supplemental_prompts = target.supplemental_prompts.copy()
        self.memories = target.memories.copy()
        self.skill_updates = target.skill_updates.copy()
        self.subagent_specs = target.subagent_specs.copy()
        self._persist()
        return True

    def _save_snapshot(self, reason: str):
        from stream import create_id
        snap = HarnessSnapshot(
            snapshot_id=create_id(),
            timestamp=time.time(),
            supplemental_prompts=self.supplemental_prompts.copy(),
            memories=self.memories.copy(),
            skill_updates=self.skill_updates.copy(),
            subagent_specs=self.subagent_specs.copy(),
            refine_reason=reason,
        )
        self._snapshots.append(snap)
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots = self._snapshots[-self._max_snapshots:]

    def _persist(self):
        os.makedirs(self._storage_dir, exist_ok=True)
        path = os.path.join(self._storage_dir, "harness_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "supplemental_prompts": self.supplemental_prompts,
                "memories": self.memories,
                "skill_updates": self.skill_updates,
                "subagent_specs": self.subagent_specs,
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        path = os.path.join(self._storage_dir, "harness_state.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.supplemental_prompts = data.get("supplemental_prompts", [])
            self.memories = data.get("memories", [])
            self.skill_updates = data.get("skill_updates", {})
            self.subagent_specs = data.get("subagent_specs", {})


# 全局实例
harness = ContinualHarness()
```

#### 2.2 `/refine` 命令处理器

```python
# 在 chat_orchestrator.py 中新增 /refine 处理

REFINE_SYSTEM_PROMPT = """你是一个持续化框架改进助手。
分析当前对话轨迹，提取有证据支撑的教训。

输出 JSON 格式:
{
  "reason": "改进原因",
  "lessons": [
    {"category": "tool_usage|reasoning|context|error_handling", "content": "教训内容", "evidence": "来自轨迹的证据"}
  ],
  "supplemental_prompts": ["补充到系统提示的指令"],
  "skill_updates": {"skill_name": "更新后的描述"}
}

要求:
- 每条教训必须有 evidence（来自轨迹的具体引用）
- 更新必须是小而具体的，不是大而宽泛的
- 最多提取 5 条教训
"""

async def _handle_refine(
    session: ChatSession,
    lifecycle: StreamLifecycle,
) -> bool:
    """处理 /refine 命令 — 审查当前轨迹并更新 harness"""
    from harness import harness
    from stream import create_text_chunk
    from deepseek import chat_completion

    # 1. 构建当前轨迹摘要
    thread = session.thread_store.get(session.thread_id)
    if not thread:
        lifecycle.write_chunk(create_text_chunk("⚠️ 无可审查的对话轨迹"))
        lifecycle.emit_done_once()
        return True

    transcript = [{"role": m.role, "content": m.text} for m in thread.messages]

    # 2. 调用 LLM 分析轨迹
    messages = [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": f"请分析以下对话轨迹并提取改进建议:\n\n{json.dumps(transcript[-20:], ensure_ascii=False)}"},
    ]
    response = await chat_completion(messages=messages)
    refine_text = response.choices[0].message.content

    # 3. 解析 LLM 输出
    try:
        refine_result = json.loads(refine_text)
    except json.JSONDecodeError:
        refine_result = {"reason": "parse error", "lessons": [], "supplemental_prompts": []}

    # 4. 应用更新
    summary = harness.refine(transcript, refine_result)

    # 5. 输出结果
    lifecycle.write_chunk(create_text_chunk(summary))
    lifecycle.emit_done_once()
    return True
```

---

## 特性三：长时间运行/后台会话支持

### Prime Agent 设计

```
┌──────────────────────────────────────────────────────────┐
│                  Session Worker (daemon)                  │
│                                                           │
│  Heartbeat ──┐                                           │
│  Schedule  ──┤──→ Prompt Queue ──→ AgentSession ──→ LLM  │
│  Goal      ──┤                    │                      │
│  Autonomous──┘                    ├─→ IPython Kernel     │
│                                   ├─→ RLM Children       │
│                                   └─→ Artifacts (JSONL)  │
│                                                           │
│  客户端 detach 后 worker 继续运行                          │
│  客户端 reattach 时从 artifacts 恢复                       │
└──────────────────────────────────────────────────────────┘
```

**五条核心能力：**

| 能力 | 说明 |
|------|------|
| Daemon-Backed Sessions | 终端关闭后 worker 继续运行，可 reattach |
| Heartbeats | `/heartbeat` 定期重新进入会话 |
| Schedules | `prime-agent schedule` 一次性或 cron 定时任务 |
| Persistent Goals | `/goal` 持续到目标完成 |
| Autonomous Mode | `/autonomous` 有限自治，质量门检查 |

### newtask-pi 现状

- ✅ 有 compaction（自动压缩）
- ✅ 有 DuckDB 持久化
- ✅ 有 steer/插话
- ❌ 无 daemon 后台会话
- ❌ 无心跳/调度
- ❌ 无持久化目标
- ❌ 无自治模式

### 实现方案

#### 3.1 心跳管理器 (`heartbeat_manager.py`)

```python
"""Heartbeat Manager — 心跳调度

借鉴 Prime Agent 的三种调度面:
- /heartbeat (用户) — 一个可见的周期性指令
- rlm_heartbeat (Agent) — 多个编程式管理的内部心跳
- schedule (CLI) — 通用一次性或 cron 任务
"""

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class Heartbeat:
    """心跳定义"""
    heartbeat_id: str
    instruction: str        # 心跳触发时注入的指令
    interval_seconds: float  # 间隔（秒）
    label: str = "default"
    delivery_mode: str = "steer"  # steer / follow_up
    status: str = "active"   # active / paused
    last_fired_at: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class ScheduledJob:
    """定时任务"""
    job_id: str
    agent_id: str
    prompt: str
    schedule_type: str   # "once" or "cron"
    fire_at: float       # 一次性：触发时间戳
    cron_expr: str = ""  # cron 表达式
    status: str = "pending"  # pending / claimed / done / cancelled


class HeartbeatManager:
    """心跳与调度管理器"""

    def __init__(self):
        self._heartbeats: dict[str, Heartbeat] = {}
        self._schedules: dict[str, ScheduledJob] = {}
        self._prompt_callback: Callable | None = None
        self._runner_task: asyncio.Task | None = None

    def set_prompt_callback(self, cb: Callable[[str, str], Awaitable[None]]):
        """设置回调：当心跳/调度触发时，调用 cb(session_id, prompt)"""
        self._prompt_callback = cb

    async def create_heartbeat(
        self,
        session_id: str,
        instruction: str,
        interval: str = "10m",
        label: str = "default",
        delivery_mode: str = "steer",
    ) -> Heartbeat:
        """创建心跳 — /heartbeat every 10m Check deployment"""
        from stream import create_id
        seconds = self._parse_interval(interval)
        hb = Heartbeat(
            heartbeat_id=create_id(),
            instruction=instruction,
            interval_seconds=seconds,
            label=label,
            delivery_mode=delivery_mode,
        )
        self._heartbeats[session_id] = hb
        return hb

    async def fire_if_due(self, session_id: str) -> str | None:
        """检查心跳是否到期，返回要注入的指令"""
        hb = self._heartbeats.get(session_id)
        if not hb or hb.status != "active":
            return None
        now = time.time()
        if now - hb.last_fired_at >= hb.interval_seconds:
            hb.last_fired_at = now
            return hb.instruction
        return None

    async def pause_heartbeat(self, session_id: str):
        hb = self._heartbeats.get(session_id)
        if hb:
            hb.status = "paused"

    async def resume_heartbeat(self, session_id: str):
        hb = self._heartbeats.get(session_id)
        if hb:
            hb.status = "active"

    async def add_schedule(
        self,
        agent_id: str,
        prompt: str,
        fire_at: float | None = None,
        in_minutes: float | None = None,
    ) -> ScheduledJob:
        """添加一次性定时任务 — prime-agent schedule add"""
        from stream import create_id
        if in_minutes is not None:
            fire_at = time.time() + in_minutes * 60
        job = ScheduledJob(
            job_id=create_id(),
            agent_id=agent_id,
            prompt=prompt,
            schedule_type="once",
            fire_at=fire_at or time.time(),
        )
        self._schedules[job.job_id] = job
        return job

    async def check_schedules(self) -> list[ScheduledJob]:
        """检查到期的定时任务"""
        now = time.time()
        due = []
        for job in self._schedules.values():
            if job.status == "pending" and job.fire_at <= now:
                job.status = "claimed"
                due.append(job)
        return due

    def _parse_interval(self, interval: str) -> float:
        """解析间隔: '10m' -> 600, '30s' -> 30, '1h' -> 3600"""
        unit = interval[-1]
        val = float(interval[:-1])
        return {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60) * val


# 全局实例
heartbeat_manager = HeartbeatManager()
```

#### 3.2 持久化目标 (`goal_manager.py`)

```python
"""Goal Manager — 持久化目标

借鉴 Prime Agent 的 /goal 设计:
- 目标是跨轮次的持久化对象
- harness 在每个普通轮次后继续提示目标
- 只有 goal.complete() 才标记成功完成
- 支持 token 预算限制
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Goal:
    """持久化目标"""
    goal_id: str
    objective: str           # 目标描述
    status: str = "active"   # active / paused / completed / errored / budget_exceeded
    created_at: float = field(default_factory=time.time)
    token_usage: int = 0     # 已用 token
    token_budget: int = 0    # 预算（0=无限制）
    elapsed_seconds: float = 0.0
    continuation_count: int = 0  # 连续推进次数
    progress_log: list[str] = field(default_factory=list)


class GoalManager:
    """目标管理器"""

    def __init__(self):
        self._goals: dict[str, Goal] = {}  # session_id -> Goal

    async def handle(self, session_id: str, action: str = "get", **kwargs) -> dict[str, Any]:
        """统一入口 — 供 RLM kernel 的 goal skill 调用"""
        if action == "get":
            return self.get_status(session_id)
        elif action == "complete":
            return self.complete(session_id)
        elif action == "create":
            return self.create(session_id, kwargs.get("objective", ""), kwargs.get("budget", 0))
        elif action == "pause":
            return self.pause(session_id)
        elif action == "resume":
            return self.resume(session_id)
        return {"error": f"unknown action: {action}"}

    def create(self, session_id: str, objective: str, budget: int = 0) -> dict[str, Any]:
        from stream import create_id
        goal = Goal(
            goal_id=create_id(),
            objective=objective,
            token_budget=budget,
        )
        self._goals[session_id] = goal
        return {"status": "created", "goal": self._goal_to_dict(goal)}

    def get_status(self, session_id: str) -> dict[str, Any]:
        goal = self._goals.get(session_id)
        if not goal:
            return {"status": "no_goal"}
        return {"status": goal.status, "goal": self._goal_to_dict(goal)}

    def complete(self, session_id: str) -> dict[str, Any]:
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "completed"
            return {"status": "completed"}
        return {"status": "no_goal"}

    def pause(self, session_id: str) -> dict[str, Any]:
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "paused"
            return {"status": "paused"}
        return {"status": "no_goal"}

    def resume(self, session_id: str) -> dict[str, Any]:
        goal = self._goals.get(session_id)
        if goal:
            goal.status = "active"
            return {"status": "active"}
        return {"status": "no_goal"}

    def should_continue(self, session_id: str) -> bool:
        """是否应该在普通轮次后继续提示目标"""
        goal = self._goals.get(session_id)
        if not goal or goal.status != "active":
            return False
        if goal.token_budget > 0 and goal.token_usage >= goal.token_budget:
            goal.status = "budget_exceeded"
            return False
        return True

    def add_progress(self, session_id: str, progress: str, tokens_used: int = 0):
        goal = self._goals.get(session_id)
        if goal:
            goal.progress_log.append(progress)
            goal.token_usage += tokens_used
            goal.continuation_count += 1
            goal.elapsed_seconds = time.time() - goal.created_at

    def _goal_to_dict(self, goal: Goal) -> dict[str, Any]:
        return {
            "id": goal.goal_id,
            "objective": goal.objective,
            "status": goal.status,
            "tokenUsage": goal.token_usage,
            "tokenBudget": goal.token_budget,
            "elapsedSeconds": round(goal.elapsed_seconds, 1),
            "continuationCount": goal.continuation_count,
        }


# 全局实例
goal_manager = GoalManager()
```

#### 3.3 自治模式 (`autonomous_mode.py`)

```python
"""Autonomous Mode — 有限自治模式

借鉴 Prime Agent 的 /autonomous 设计:
- 在无人类输入时继续推进
- 质量门检查（gate command）
- 限制：最大轮次、最大 token、最大时间
- 门检查失败 → 返回输出给 agent 再试
- 门检查通过 → 可以结束
- 避免重复运行同一个失败的门（工作区未变化时）
"""

from __future__ import annotations
import asyncio
import time
import hashlib
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AutonomousConfig:
    """自治模式配置"""
    enabled: bool = False
    max_turns: int = 20          # 最大轮次
    max_tokens: int = 200_000    # 最大 token
    max_wall_seconds: float = 600.0  # 最大壁钟时间
    gate_command: str = ""       # 质量门命令（如 "npm run check"）
    gate_timeout: float = 30.0   # 门命令超时


@dataclass
class AutonomousState:
    """自治模式运行时状态"""
    config: AutonomousConfig = field(default_factory=AutonomousConfig)
    turns_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.time)
    last_gate_output: str = ""
    last_gate_hash: str = ""     # 上次门检查时工作区的 hash


class AutonomousController:
    """自治模式控制器"""

    def __init__(self):
        self._states: dict[str, AutonomousState] = {}

    def enable(self, session_id: str, config: AutonomousConfig):
        self._states[session_id] = AutonomousState(config=config)

    def disable(self, session_id: str):
        self._states.pop(session_id, None)

    def is_active(self, session_id: str) -> bool:
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

        # 检查轮次限制
        if state.turns_used >= state.config.max_turns:
            return False, f"max turns reached ({state.config.max_turns})"

        # 检查 token 限制
        if state.tokens_used >= state.config.max_tokens:
            return False, f"max tokens reached ({state.config.max_tokens})"

        # 检查壁钟时间限制
        elapsed = time.time() - state.started_at
        if elapsed >= state.config.max_wall_seconds:
            return False, f"max wall time reached ({state.config.max_wall_seconds}s)"

        return True, "within limits"

    async def run_gate(self, session_id: str, workspace_dir: str = ".") -> dict[str, Any]:
        """运行质量门

        Returns:
            {"passed": bool, "output": str}
        """
        state = self._states.get(session_id)
        if not state or not state.config.gate_command:
            return {"passed": True, "output": "(no gate configured)"}

        # 计算工作区 hash — 避免重复运行相同状态的门
        ws_hash = self._workspace_hash(workspace_dir)
        if ws_hash == state.last_gate_hash and state.last_gate_output:
            return {"passed": False, "output": state.last_gate_output, "skipped": True}

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

            state.last_gate_output = output
            state.last_gate_hash = ws_hash

            return {"passed": passed, "output": output}
        except asyncio.TimeoutError:
            return {"passed": False, "output": "gate timed out"}
        except Exception as e:
            return {"passed": False, "output": str(e)}

    def record_turn(self, session_id: str, tokens: int = 0):
        state = self._states.get(session_id)
        if state:
            state.turns_used += 1
            state.tokens_used += tokens

    def _workspace_hash(self, dir_path: str) -> str:
        """计算工作区文件的 hash（简化版）"""
        import os
        hasher = hashlib.md5()
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "data")]
            for f in sorted(files):
                if f.endswith((".py", ".ts", ".tsx", ".md", ".json")):
                    path = os.path.join(root, f)
                    try:
                        with open(path, "rb") as fh:
                            hasher.update(fh.read())
                    except Exception:
                        pass
        return hasher.hexdigest()

    def get_status(self, session_id: str) -> dict[str, Any]:
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
        }


# 全局实例
autonomous_controller = AutonomousController()
```

---

## 特性四：多智能体直接通信

### Prime Agent 设计

```
Agent A (running)                     Agent B (running)
    │                                     │
    │  agent_message.send(                │
    │    "Check the endpoint",            │
    │    receiver_role="sibling",         │
    │    receiver_name="api-reviewer",    │
    │    mode="auto"                      │
    │  )                                  │
    │─────────── daemon routes ──────────→│
    │                                     │  消息注入 B 的上下文
    │                                     │  B 处理后回复
    │  ←──────── agent_message.reply ─────│
    │                                     │

三种投递模式:
- auto: 目标空闲时立即投递，忙碌时 steer
- steer: 注入到目标的当前工作中
- follow_up: 等目标当前工作完成后投递
```

### newtask-pi 现状

`sub_agent.py` 的子代理是**单向同步**的：
```
父 → delegate_sub_agent(task) → 子代理运行 → 返回结果给父
```
没有：
- Agent 之间的直接消息
- Peer discovery
- 消息路由
- 异步回复

### 实现方案

#### 4.1 Agent 消息总线 (`agent_message_bus.py`)

```python
"""Agent Message Bus — 多智能体直接通信

借鉴 Prime Agent 的 agent_message 设计:
- 运行中的 agent 可以互相发送消息
- 三种投递模式: auto / steer / follow_up
- family roster: 父子兄弟关系的自动发现
- daemon 负责路由和限流
"""

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class DeliveryMode(str, Enum):
    AUTO = "auto"          # 空闲时立即投递，忙碌时 steer
    STEER = "steer"        # 注入到当前工作
    FOLLOW_UP = "follow_up"  # 等当前工作完成后投递


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"          # 正在处理一个轮次
    WAITING = "waiting"    # 等待用户输入


@dataclass
class AgentRecord:
    """注册的 agent 记录"""
    agent_id: str
    name: str
    session_id: str
    status: AgentStatus = AgentStatus.IDLE
    parent_id: str = ""    # 父 agent ID
    role: str = "root"     # root / child / sibling
    created_at: float = field(default_factory=time.time)
    # 消息队列
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    _steer_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass
class MessageReceipt:
    """消息回执"""
    delivered: bool       # True=立即投递, False=排队
    delivery_status: str  # "delivered" / "queued" / "steered"
    message_id: str
    timestamp: float = field(default_factory=time.time)


class AgentMessageBus:
    """Agent 消息总线 — 全局单例"""

    def __init__(self):
        self._agents: dict[str, AgentRecord] = {}
        self._rate_limit: dict[str, list[float]] = {}  # agent_id -> [timestamps]
        self._max_rate_per_minute = 30
        self._max_pending_per_agent = 50
        self._max_message_size = 10_000  # 字符

    def register_agent(
        self,
        agent_id: str,
        name: str,
        session_id: str,
        parent_id: str = "",
        role: str = "root",
    ) -> AgentRecord:
        """注册一个 agent"""
        record = AgentRecord(
            agent_id=agent_id,
            name=name,
            session_id=session_id,
            parent_id=parent_id,
            role=role,
        )
        self._agents[agent_id] = record
        return record

    def unregister_agent(self, agent_id: str):
        """注销 agent"""
        self._agents.pop(agent_id, None)

    async def send(
        self,
        message: str,
        receiver_role: str = "sibling",
        receiver_name: str = "",
        sender_id: str = "",
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> MessageReceipt:
        """发送消息给另一个 agent

        Args:
            message: 消息内容
            receiver_role: "parent" / "child" / "sibling" / "all"
            receiver_name: 接收方名称
            sender_id: 发送方 ID
            mode: 投递模式
        """
        from stream import create_id

        # 消息大小限制
        if len(message) > self._max_message_size:
            return MessageReceipt(delivered=False, delivery_status="rejected_too_large",
                                  message_id=create_id())

        # 速率限制
        if not self._check_rate_limit(sender_id):
            return MessageReceipt(delivered=False, delivery_status="rate_limited",
                                  message_id=create_id())

        # 广播
        if receiver_role == "all":
            return await self._broadcast(message, sender_id, create_id())

        # 查找接收方
        receiver = self._find_receiver(sender_id, receiver_role, receiver_name)
        if not receiver:
            return MessageReceipt(delivered=False, delivery_status="not_found",
                                  message_id=create_id())

        # 待处理队列限制
        if receiver._inbox.qsize() >= self._max_pending_per_agent:
            return MessageReceipt(delivered=False, delivery_status="queue_full",
                                  message_id=create_id())

        msg_id = create_id()

        # 根据投递模式处理
        if mode == DeliveryMode.STEER or (mode == DeliveryMode.AUTO and receiver.status == AgentStatus.BUSY):
            # steer: 注入到当前工作
            await receiver._steer_queue.put({"id": msg_id, "message": message, "sender_id": sender_id})
            return MessageReceipt(delivered=True, delivery_status="steered", message_id=msg_id)

        elif mode == DeliveryMode.FOLLOW_UP:
            # follow_up: 排队等待
            await receiver._inbox.put({"id": msg_id, "message": message, "sender_id": sender_id})
            return MessageReceipt(delivered=False, delivery_status="queued", message_id=msg_id)

        else:
            # auto + idle: 立即投递
            await receiver._inbox.put({"id": msg_id, "message": message, "sender_id": sender_id})
            return MessageReceipt(delivered=True, delivery_status="delivered", message_id=msg_id)

    async def _broadcast(self, message: str, sender_id: str, msg_id: str) -> MessageReceipt:
        """在 family roster 内广播"""
        sender = self._agents.get(sender_id)
        if not sender:
            return MessageReceipt(delivered=False, delivery_status="sender_not_found",
                                  message_id=msg_id)
        # 找到同 family 的 agents
        family = self._get_family(sender_id)
        for agent in family:
            if agent.agent_id != sender_id:
                await agent._inbox.put({"id": msg_id, "message": message, "sender_id": sender_id})
        return MessageReceipt(delivered=True, delivery_status="broadcast", message_id=msg_id)

    def _find_receiver(self, sender_id: str, role: str, name: str) -> AgentRecord | None:
        """根据角色和名称查找接收方"""
        sender = self._agents.get(sender_id)
        if not sender:
            return None

        if role == "parent":
            return self._agents.get(sender.parent_id)

        if role == "child":
            for agent in self._agents.values():
                if agent.parent_id == sender_id and (not name or agent.name == name):
                    return agent

        if role == "sibling":
            for agent in self._agents.values():
                if (agent.parent_id == sender.parent_id
                    and agent.agent_id != sender_id
                    and (not name or agent.name == name)):
                    return agent

        return None

    def _get_family(self, agent_id: str) -> list[AgentRecord]:
        """获取同 family 的所有 agents"""
        agent = self._agents.get(agent_id)
        if not agent:
            return []
        root_id = agent.parent_id or agent_id
        return [a for a in self._agents.values()
                if a.parent_id == root_id or a.agent_id == root_id]

    def list_agents(self) -> list[dict[str, Any]]:
        """列出所有已注册的 agents"""
        return [
            {
                "agentId": a.agent_id,
                "name": a.name,
                "status": a.status.value,
                "role": a.role,
                "parentId": a.parent_id,
            }
            for a in self._agents.values()
        ]

    async def receive(self, agent_id: str) -> dict[str, Any] | None:
        """接收消息（非阻塞）"""
        agent = self._agents.get(agent_id)
        if not agent:
            return None
        try:
            msg = agent._inbox.get_nowait()
            return msg
        except asyncio.QueueEmpty:
            return None

    async def receive_steer(self, agent_id: str) -> dict[str, Any] | None:
        """接收 steer 消息（非阻塞）"""
        agent = self._agents.get(agent_id)
        if not agent:
            return None
        try:
            msg = agent._steer_queue.get_nowait()
            return msg
        except asyncio.QueueEmpty:
            return None

    def set_status(self, agent_id: str, status: AgentStatus):
        agent = self._agents.get(agent_id)
        if agent:
            agent.status = status

    def _check_rate_limit(self, agent_id: str) -> bool:
        now = time.time()
        timestamps = self._rate_limit.setdefault(agent_id, [])
        # 清理 1 分钟前的记录
        self._rate_limit[agent_id] = [t for t in timestamps if now - t < 60]
        if len(self._rate_limit[agent_id]) >= self._max_rate_per_minute:
            return False
        self._rate_limit[agent_id].append(now)
        return True


# 全局实例
agent_message_bus = AgentMessageBus()
```

#### 4.2 集成到 chat_orchestrator — 在会话启动时注册 agent

```python
# 在 chat_orchestrator.py 的 chat_stream 函数中新增:

from agent_message_bus import agent_message_bus, AgentStatus
from stream import create_id

# 会话启动时注册 agent
agent_id = create_id()
agent_message_bus.register_agent(
    agent_id=agent_id,
    name=f"agent-{session.thread_id[:8]}",
    session_id=session.thread_id,
    role="root",
)

try:
    agent_message_bus.set_status(agent_id, AgentStatus.BUSY)

    # ... 正常的 agent_loop 运行 ...

    # 在每个轮次开始前检查 inbox
    msg = await agent_message_bus.receive(agent_id)
    if msg:
        # 将消息注入为 user 消息
        context["extra_messages"] = [{"role": "user", "content": f"[来自其他Agent] {msg['message']}"}]

finally:
    agent_message_bus.set_status(agent_id, AgentStatus.IDLE)
    # 不立即注销 — daemon-backed agent 保持可寻址
```

---

## 2. 集成路线图

### 阶段一：基础层（1-2 天）

| 文件 | 说明 | 优先级 |
|------|------|--------|
| `rlm_kernel.py` | 持久化 Python 内核 + `rlm()` 递归调用 | P0 |
| `harness.py` | Harness 状态管理 + 快照/回滚 | P0 |
| `agent_message_bus.py` | Agent 消息总线 + peer discovery | P1 |

### 阶段二：调度层（2-3 天）

| 文件 | 说明 | 优先级 |
|------|------|--------|
| `heartbeat_manager.py` | 心跳 + 定时调度 | P1 |
| `goal_manager.py` | 持久化目标 | P1 |
| `autonomous_mode.py` | 自治模式 + 质量门 | P2 |

### 阶段三：集成层（1-2 天）

| 改动 | 说明 | 优先级 |
|------|------|--------|
| `chat_orchestrator.py` | 新增 `/refine`、`/goal`、`/heartbeat`、`/autonomous` 命令路由 | P0 |
| `app.py` | 新增 `/api/agents` (列出运行中 agents)、`/api/agents/send` (发送消息) API | P1 |
| `sub_agent.py` | 改造为异步 handle（不等待结果），通过 `agent_message` 回传 | P2 |
| `thread_state.py` | compaction 时保留 RLM kernel namespace | P2 |

### 阶段四：前端（1-2 天）

| 组件 | 说明 | 优先级 |
|------|------|--------|
| `AgentPanel.tsx` | 显示运行中 agents 列表 + 发送消息 | P2 |
| `GoalIndicator.tsx` | 显示当前目标状态和进度 | P2 |
| `AutonomousToggle.tsx` | 自治模式开关 + 配置 | P3 |

### 架构图（集成后）

```
┌─────────────────────────────────────────────────────────────┐
│                     newtask-pi (集成后)                       │
│                                                               │
│  ┌──────────┐    ┌──────────────────────────────────────┐   │
│  │ Frontend  │    │         Backend (FastAPI)              │   │
│  │ React     │←──→│  /api/chat  /api/agents  /api/goal    │   │
│  │           │    │  /api/heartbeat  /api/autonomous      │   │
│  └──────────┘    └──────────────┬───────────────────────┘   │
│                                  │                            │
│                    ┌─────────────▼─────────────┐             │
│                    │    chat_orchestrator.py     │             │
│                    │  /refine  /goal  /heartbeat │             │
│                    │  /autonomous  /send         │             │
│                    └─────────────┬─────────────┘             │
│                                  │                            │
│         ┌────────────────────────┼────────────────────────┐  │
│         ▼                        ▼                        ▼  │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────┐ │
│  │ rlm_kernel   │    │  agent_loop       │    │ harness   │ │
│  │ (持久化 Python)│    │  (双层循环)       │    │ (自我改进) │ │
│  │ rlm() 递归    │    │  tool execution   │    │ /refine   │ │
│  └──────┬───────┘    └────────┬─────────┘    └───────────┘ │
│         │                     │                            │
│         ▼                     ▼                            │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────┐ │
│  │ sub_agent    │    │ agent_message_bus │    │ goal_mgr  │ │
│  │ (异步 handle)│←──→│ (多智能体通信)     │←──→│ heartbeat │ │
│  └──────────────┘    └──────────────────┘    └───────────┘ │
│         │                     │                            │
│         ▼                     ▼                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              thread_state.py + duckdb_store            │  │
│  │          (三层记忆 + 持久化 + compaction)              │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 总结

| 特性 | 实现复杂度 | 核心文件 | 现有代码复用率 |
|------|-----------|---------|---------------|
| RLM 抽象 | ★★★★ | `rlm_kernel.py` + 改造 `sub_agent.py` | 40%（复用 sub_agent 的类型定义和工具桥接） |
| 持续化框架 | ★★★ | `harness.py` + `/refine` 路由 | 30%（复用 thread_state 的 pinned_decisions） |
| 长时间运行 | ★★★★ | `heartbeat_manager.py` + `goal_manager.py` + `autonomous_mode.py` | 20%（复用 DuckDB 持久化和 compaction） |
| 多智能体通信 | ★★★ | `agent_message_bus.py` + 集成 | 10%（全新实现） |

**总工作量估算：** 5-9 天（含测试和前端）
