"""Agent Message Bus — 多智能体直接通信

借鉴 Prime Agent 的 agent_message 设计:
- 运行中的 agent 可以互相发送消息
- 三种投递模式: auto / steer / follow_up
- family roster: 父子兄弟关系的自动发现
- 消息路由 + 速率限制 + 队列限制

与 Prime Agent 的对应:
  agent_message.send(message, receiver_role, receiver_name, mode)
  agent_message.list_agents()

用法:
    bus = get_message_bus()
    bus.register_agent("agent-1", "researcher", "session-1")
    bus.register_agent("agent-2", "writer", "session-1", parent_id="agent-1", role="child")

    receipt = await bus.send(
        message="请检查 API 端点",
        receiver_role="child",
        receiver_name="writer",
        sender_id="agent-1",
        mode=DeliveryMode.AUTO,
    )
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ─── 常量 ──────────────────────────────────────────────

MAX_RATE_PER_MINUTE = 30
MAX_PENDING_PER_AGENT = 50
MAX_MESSAGE_SIZE = 10_000  # 字符
RATE_WINDOW_SECONDS = 60


# ─── 枚举 ──────────────────────────────────────────────

class DeliveryMode(str, Enum):
    """投递模式"""
    AUTO = "auto"              # 空闲时立即投递，忙碌时 steer
    STEER = "steer"            # 注入到当前工作
    FOLLOW_UP = "follow_up"    # 等当前工作完成后投递


class AgentStatus(str, Enum):
    """Agent 状态"""
    IDLE = "idle"
    BUSY = "busy"              # 正在处理一个轮次
    WAITING = "waiting"        # 等待用户输入
    STOPPED = "stopped"        # 已停止


# ─── 数据结构 ──────────────────────────────────────────

@dataclass
class AgentRecord:
    """注册的 agent 记录"""
    agent_id: str
    name: str
    session_id: str
    status: AgentStatus = AgentStatus.IDLE
    parent_id: str = ""        # 父 agent ID
    role: str = "root"         # root / child / sibling
    created_at: float = field(default_factory=time.time)
    # 消息队列
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    steer_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass
class MessageReceipt:
    """消息回执"""
    delivered: bool             # True=已投递, False=排队/拒绝
    delivery_status: str        # delivered / queued / steered / rejected / not_found
    message_id: str
    timestamp: float = field(default_factory=time.time)
    error: str = ""


# ─── Agent Message Bus ────────────────────────────────

class AgentMessageBus:
    """Agent 消息总线 — 全局单例

    负责:
    - agent 注册与发现
    - 消息路由（根据 role + name 查找接收方）
    - 投递模式处理（auto/steer/follow_up）
    - 速率限制 + 队列限制 + 消息大小限制
    - 广播（family roster 内）
    """

    def __init__(self):
        self._agents: dict[str, AgentRecord] = {}
        self._rate_limiter: dict[str, list[float]] = {}  # agent_id -> [timestamps]

    # ── 注册 ──

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
        self._rate_limiter.pop(agent_id, None)

    def set_status(self, agent_id: str, status: AgentStatus):
        """设置 agent 状态"""
        agent = self._agents.get(agent_id)
        if agent:
            agent.status = status

    # ── 发送消息 ──

    async def send(
        self,
        message: str,
        receiver_role: str = "sibling",
        receiver_name: str = "",
        sender_id: str = "",
        mode: str = "auto",
    ) -> MessageReceipt:
        """发送消息给另一个 agent

        Args:
            message: 消息内容
            receiver_role: "parent" / "child" / "sibling" / "all"
            receiver_name: 接收方名称（可选，用于多兄弟场景）
            sender_id: 发送方 agent ID
            mode: "auto" / "steer" / "follow_up"

        Returns:
            MessageReceipt
        """
        from stream import create_id
        msg_id = create_id()

        # 1. 消息大小限制
        if len(message) > MAX_MESSAGE_SIZE:
            return MessageReceipt(
                delivered=False, delivery_status="rejected",
                message_id=msg_id, error="message too large",
            )

        # 2. 速率限制
        if sender_id and not self._check_rate_limit(sender_id):
            return MessageReceipt(
                delivered=False, delivery_status="rate_limited",
                message_id=msg_id, error="rate limit exceeded",
            )

        # 3. 广播
        if receiver_role == "all":
            return await self._broadcast(message, sender_id, msg_id)

        # 4. 查找接收方
        receiver = self._find_receiver(sender_id, receiver_role, receiver_name)
        if not receiver:
            return MessageReceipt(
                delivered=False, delivery_status="not_found",
                message_id=msg_id,
                error=f"no agent found for role={receiver_role} name={receiver_name}",
            )

        # 5. 队列限制
        if receiver.inbox.qsize() >= MAX_PENDING_PER_AGENT:
            return MessageReceipt(
                delivered=False, delivery_status="queue_full",
                message_id=msg_id, error="receiver queue full",
            )

        # 6. 根据投递模式处理
        delivery_mode = DeliveryMode(mode) if mode in [m.value for m in DeliveryMode] else DeliveryMode.AUTO

        if delivery_mode == DeliveryMode.STEER:
            # steer: 注入到当前工作
            await receiver.steer_queue.put({
                "id": msg_id, "message": message,
                "sender_id": sender_id, "timestamp": time.time(),
            })
            return MessageReceipt(delivered=True, delivery_status="steered", message_id=msg_id)

        elif delivery_mode == DeliveryMode.FOLLOW_UP:
            # follow_up: 排队等待
            await receiver.inbox.put({
                "id": msg_id, "message": message,
                "sender_id": sender_id, "timestamp": time.time(),
            })
            return MessageReceipt(delivered=False, delivery_status="queued", message_id=msg_id)

        else:
            # auto: 空闲时立即投递，忙碌时 steer
            if receiver.status == AgentStatus.BUSY:
                await receiver.steer_queue.put({
                    "id": msg_id, "message": message,
                    "sender_id": sender_id, "timestamp": time.time(),
                })
                return MessageReceipt(delivered=True, delivery_status="steered", message_id=msg_id)
            else:
                await receiver.inbox.put({
                    "id": msg_id, "message": message,
                    "sender_id": sender_id, "timestamp": time.time(),
                })
                return MessageReceipt(delivered=True, delivery_status="delivered", message_id=msg_id)

    async def _broadcast(self, message: str, sender_id: str, msg_id: str) -> MessageReceipt:
        """在 family roster 内广播"""
        sender = self._agents.get(sender_id)
        if not sender:
            return MessageReceipt(
                delivered=False, delivery_status="sender_not_found",
                message_id=msg_id, error="sender not registered",
            )

        family = self._get_family(sender_id)
        delivered_count = 0
        for agent in family:
            if agent.agent_id != sender_id and agent.inbox.qsize() < MAX_PENDING_PER_AGENT:
                await agent.inbox.put({
                    "id": msg_id, "message": message,
                    "sender_id": sender_id, "timestamp": time.time(),
                })
                delivered_count += 1

        return MessageReceipt(
            delivered=delivered_count > 0,
            delivery_status=f"broadcast to {delivered_count} agents",
            message_id=msg_id,
        )

    # ── 接收消息 ──

    async def receive(self, agent_id: str) -> dict[str, Any] | None:
        """接收 inbox 消息（非阻塞）

        Returns:
            {"id": str, "message": str, "sender_id": str, "timestamp": float}
            或 None（无消息）
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None
        try:
            return agent.inbox.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def receive_steer(self, agent_id: str) -> dict[str, Any] | None:
        """接收 steer 消息（非阻塞）

        Steer 消息是注入到当前工作中的插话
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None
        try:
            return agent.steer_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ── 查询 ──

    def list_agents(self) -> list[dict[str, Any]]:
        """列出所有已注册的 agents"""
        return [
            {
                "agentId": a.agent_id,
                "name": a.name,
                "status": a.status.value,
                "role": a.role,
                "parentId": a.parent_id,
                "sessionId": a.session_id,
                "pendingMessages": a.inbox.qsize(),
                "pendingSteers": a.steer_queue.qsize(),
            }
            for a in self._agents.values()
        ]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """获取单个 agent 信息"""
        a = self._agents.get(agent_id)
        if not a:
            return None
        return {
            "agentId": a.agent_id,
            "name": a.name,
            "status": a.status.value,
            "role": a.role,
            "parentId": a.parent_id,
            "sessionId": a.session_id,
            "pendingMessages": a.inbox.qsize(),
            "pendingSteers": a.steer_queue.qsize(),
        }

    # ── 内部方法 ──

    def _find_receiver(
        self,
        sender_id: str,
        role: str,
        name: str,
    ) -> AgentRecord | None:
        """根据角色和名称查找接收方"""
        sender = self._agents.get(sender_id)
        if not sender:
            return None

        if role == "parent":
            return self._agents.get(sender.parent_id)

        if role == "child":
            # 查找自己的子代理
            for agent in self._agents.values():
                if agent.parent_id == sender_id and (not name or agent.name == name):
                    return agent

        if role == "sibling":
            # 查找同父的兄弟
            for agent in self._agents.values():
                if (agent.parent_id == sender.parent_id
                        and agent.agent_id != sender_id
                        and (not name or agent.name == name)):
                    return agent

        return None

    def _get_family(self, agent_id: str) -> list[AgentRecord]:
        """获取同 family 的所有 agents（共享同一个 root）"""
        agent = self._agents.get(agent_id)
        if not agent:
            return []
        # 找到 root
        root_id = agent.parent_id or agent_id
        # 返回所有以 root_id 为根的 agents
        family = []
        for a in self._agents.values():
            if a.agent_id == root_id or a.parent_id == root_id:
                family.append(a)
        return family

    def _check_rate_limit(self, agent_id: str) -> bool:
        """检查速率限制"""
        now = time.time()
        timestamps = self._rate_limiter.setdefault(agent_id, [])
        # 清理窗口外的记录
        self._rate_limiter[agent_id] = [t for t in timestamps if now - t < RATE_WINDOW_SECONDS]
        if len(self._rate_limiter[agent_id]) >= MAX_RATE_PER_MINUTE:
            return False
        self._rate_limiter[agent_id].append(now)
        return True


# ─── 全局实例 ──────────────────────────────────────────

_message_bus: AgentMessageBus | None = None


def get_message_bus() -> AgentMessageBus:
    """获取全局消息总线实例"""
    global _message_bus
    if _message_bus is None:
        _message_bus = AgentMessageBus()
    return _message_bus
