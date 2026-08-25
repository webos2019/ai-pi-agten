"""Agent Manager — 连接真实 sub-agent 系统的管理面板后端

不再模拟数据，而是读取:
- sub_agent.py 中的 SUB_AGENT_TYPES (7 种真实子代理类型)
- tool_registry 中已注册的所有工具
- skill_registry 中已发现的所有技能
- 可通过 API 触发真实的子代理执行

架构:
  父 Agent (主聊天) ←→ delegate_sub_agent 工具
    ├─ research 子代理 (web_search, web_fetch, ...)
    ├─ analysis 子代理 (calculator, unit_convert, ...)
    ├─ writer 子代理 (纯 LLM)
    ├─ log_analyst 子代理 (log_search, service_check)
    ├─ db_doctor 子代理 (db_diagnose, log_search)
    ├─ infra_inspector 子代理 (system_monitor, service_check, log_search)
    └─ remediation 子代理 (service_check, system_monitor)
"""

from __future__ import annotations

import time
import threading
import asyncio
from typing import Any


# ─── 读取真实 sub-agent 类型 ──────────────────────────

def _load_sub_agent_types() -> list[dict[str, Any]]:
    """从 sub_agent.py 读取真实的子代理类型定义"""
    try:
        from sub_agent import SUB_AGENT_TYPES, MAX_SUB_AGENT_DEPTH, SUB_AGENT_MAX_TURNS
        result = []
        for name, sa in SUB_AGENT_TYPES.items():
            result.append({
                "name": sa.name,
                "description": sa.description,
                "system_prompt": sa.system_prompt,
                "tool_names": sa.tool_names,
                "model": sa.model,
                "max_turns": sa.max_turns,
            })
        return result
    except Exception:
        return []


def _load_registered_tools() -> list[dict[str, Any]]:
    """从 tool_registry 读取所有已注册工具"""
    try:
        from tool_registry import tool_registry
        result = []
        for t in tool_registry.list():
            result.append({
                "name": t.name,
                "description": t.description,
                "keywords": t.keywords,
                "planning_category": t.planning_category,
                "decision_weight": t.decision_weight,
                "result_is_authoritative": t.result_is_authoritative,
            })
        return result
    except Exception:
        return []


def _load_registered_skills() -> list[dict[str, Any]]:
    """从 skill_registry 读取所有已发现的技能"""
    try:
        from skill_registry import skill_registry
        skills = skill_registry.list_meta()
        result = []
        for s in skills:
            result.append({
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tool_names": s.tool_names,
                "routing_hints": s.routing_hints,
                "tags": s.tags,
            })
        return result
    except Exception:
        return []


# ─── 活动日志 ──────────────────────────────────────────

_activity_lock = threading.Lock()
_activities: list[dict[str, Any]] = []
_activity_counter = 0


def _add_activity(type_: str, message: str, data: dict[str, Any] | None = None) -> None:
    global _activity_counter
    with _activity_lock:
        _activity_counter += 1
        event = {
            "id": f"activity-{int(time.time())}-{_activity_counter}",
            "type": type_,
            "message": message,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "data": data or {},
        }
        _activities.insert(0, event)
        del _activities[100:]  # 保留最近 100 条


def _list_activities(limit: int = 50) -> list[dict[str, Any]]:
    with _activity_lock:
        return list(_activities[:limit])


# ─── 子代理执行记录 ────────────────────────────────────

_execution_lock = threading.Lock()
_executions: list[dict[str, Any]] = []


def _record_execution(agent_type: str, task: str, result: str, duration_ms: int, status: str) -> None:
    with _execution_lock:
        record = {
            "id": f"exec-{int(time.time())}-{len(_executions)+1}",
            "agent_type": agent_type,
            "task": task,
            "result_preview": result[:500] if result else "",
            "duration_ms": duration_ms,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _executions.insert(0, record)
        del _executions[50:]  # 保留最近 50 条


def _list_executions() -> list[dict[str, Any]]:
    with _execution_lock:
        return list(_executions)


# ─── 真实子代理执行 ────────────────────────────────────

async def _run_sub_agent_async(agent_type: str, task: str) -> dict[str, Any]:
    """异步运行真实的子代理"""
    from sub_agent import run_sub_agent, MAX_SUB_AGENT_DEPTH

    context = {
        "_lifecycle": None,  # 不发射流式事件（API 模式）
        "_sub_agent_depth": 0,
        "_run_id": f"mgr-{int(time.time())}",
    }

    t0 = time.time()
    try:
        result = await run_sub_agent(agent_type, task, context, 0)
        duration_ms = int((time.time() - t0) * 1000)
        _record_execution(agent_type, task, result, duration_ms, "success")
        _add_activity("orchestration", f"子代理 {agent_type} 执行完成 ({duration_ms}ms)", {
            "agent_type": agent_type, "task": task[:100], "duration_ms": duration_ms,
        })
        return {
            "agent_type": agent_type,
            "task": task,
            "result": result,
            "duration_ms": duration_ms,
            "status": "success",
        }
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        error_msg = str(e)
        _record_execution(agent_type, task, error_msg, duration_ms, "error")
        _add_activity("orchestration", f"子代理 {agent_type} 执行失败: {error_msg[:100]}", {
            "agent_type": agent_type, "error": error_msg[:200],
        })
        return {
            "agent_type": agent_type,
            "task": task,
            "result": f"错误: {error_msg}",
            "duration_ms": duration_ms,
            "status": "error",
        }


def run_sub_agent_sync(agent_type: str, task: str) -> dict[str, Any]:
    """同步包装 — 在新的事件循环中运行子代理"""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_sub_agent_async(agent_type, task))
        finally:
            loop.close()
    except Exception as e:
        return {
            "agent_type": agent_type,
            "task": task,
            "result": f"运行错误: {e}",
            "duration_ms": 0,
            "status": "error",
        }


# ─── API 数据接口 ──────────────────────────────────────

def get_agent_snapshot() -> dict[str, Any]:
    """获取 Agent 系统全局快照 — 真实数据"""
    return {
        "sub_agent_types": _load_sub_agent_types(),
        "tools": _load_registered_tools(),
        "skills": _load_registered_skills(),
        "activities": _list_activities(),
        "executions": _list_executions(),
    }


def get_sub_agent_types() -> list[dict[str, Any]]:
    """获取所有子代理类型"""
    return _load_sub_agent_types()


def get_registered_tools() -> list[dict[str, Any]]:
    """获取所有已注册工具"""
    return _load_registered_tools()


def get_registered_skills() -> list[dict[str, Any]]:
    """获取所有已注册技能"""
    return _load_registered_skills()


def get_activities(limit: int = 50) -> list[dict[str, Any]]:
    """获取活动日志"""
    return _list_activities(limit)


def get_executions() -> list[dict[str, Any]]:
    """获取子代理执行记录"""
    return _list_executions()


def execute_sub_agent(agent_type: str, task: str) -> dict[str, Any]:
    """执行真实的子代理（同步返回结果）"""
    _add_activity("worker", f"启动子代理 {agent_type}: {task[:80]}", {
        "agent_type": agent_type, "task": task,
    })
    return run_sub_agent_sync(agent_type, task)


# ─── 团队工作流 — manager 分配给多 worker 并发执行 ──────

async def _run_team_workflow_async(
    workflow_id: str,
    leader_task: str,
    workers: list[dict[str, str]],
) -> dict[str, Any]:
    """异步执行团队工作流

    流程:
      1. manager(组长) 接收任务，记录活动
      2. 并发委托给多个 worker 子代理执行
      3. 收集所有 worker 的报告
      4. 汇总输出最终结果

    workers: [{"agent_type": "research", "task": "..."}, ...]
    """
    from sub_agent import run_sub_agent

    _add_activity("team", f"团队工作流 '{workflow_id}' 启动 — 组长任务: {leader_task[:60]}", {
        "workflow_id": workflow_id, "leader_task": leader_task,
        "worker_count": len(workers),
    })

    context = {
        "_lifecycle": None,
        "_sub_agent_depth": 0,
        "_run_id": f"wf-{workflow_id}",
    }

    t0 = time.time()

    # ── 并发执行所有 worker ──
    _add_activity("orchestration", f"组长分配任务给 {len(workers)} 个 worker 并发执行", {
        "workers": [f"{w['agent_type']}: {w['task'][:40]}" for w in workers],
    })

    async def _run_one_worker(worker_spec: dict[str, str], idx: int) -> dict[str, Any]:
        agent_type = worker_spec.get("agent_type", "research")
        task = worker_spec.get("task", "")
        wt0 = time.time()
        try:
            _add_activity("worker", f"[Worker {idx+1}] {agent_type} 开始执行: {task[:60]}", {
                "worker_index": idx + 1, "agent_type": agent_type,
            })
            result = await run_sub_agent(agent_type, task, {**context, "_sub_agent_depth": 0}, 0)
            wdt = int((time.time() - wt0) * 1000)
            _record_execution(agent_type, task, result, wdt, "success")
            _add_activity("worker", f"[Worker {idx+1}] {agent_type} 完成报告 ({wdt}ms)", {
                "worker_index": idx + 1, "agent_type": agent_type, "duration_ms": wdt,
            })
            return {
                "worker_index": idx + 1,
                "agent_type": agent_type,
                "task": task,
                "result": result,
                "duration_ms": wdt,
                "status": "success",
            }
        except Exception as e:
            wdt = int((time.time() - wt0) * 1000)
            err = str(e)
            _record_execution(agent_type, task, err, wdt, "error")
            _add_activity("worker", f"[Worker {idx+1}] {agent_type} 执行失败: {err[:60]}", {
                "worker_index": idx + 1, "agent_type": agent_type, "error": err[:200],
            })
            return {
                "worker_index": idx + 1,
                "agent_type": agent_type,
                "task": task,
                "result": f"错误: {err}",
                "duration_ms": wdt,
                "status": "error",
            }

    # 并发执行所有 worker
    worker_tasks = [_run_one_worker(w, i) for i, w in enumerate(workers)]
    worker_results = await asyncio.gather(*worker_tasks)

    total_ms = int((time.time() - t0) * 1000)

    # ── 组长汇总 ──
    _add_activity("orchestration", f"组长收齐 {len(worker_results)} 份报告，开始汇总", {
        "workflow_id": workflow_id, "total_ms": total_ms,
    })

    # 用 writer 子代理做汇总
    summary_parts = []
    for wr in worker_results:
        summary_parts.append(f"### Worker {wr['worker_index']} ({wr['agent_type']}) 报告\n任务: {wr['task']}\n结果:\n{wr['result']}")
    combined = "\n\n---\n\n".join(summary_parts)

    summary_task = (
        f"你是团队组长。以下是 {len(worker_results)} 个 worker 提交的报告，"
        f"请汇总为一份综合报告:\n\n原始任务: {leader_task}\n\n{combined}\n\n"
        f"请输出:\n1. 各 worker 结果摘要\n2. 综合结论\n3. 建议"
    )

    try:
        summary_result = await run_sub_agent("writer", summary_task, {**context, "_sub_agent_depth": 0}, 0)
    except Exception as e:
        summary_result = f"汇总失败: {e}"

    summary_ms = int((time.time() - t0) * 1000) - total_ms
    _add_activity("team", f"团队工作流 '{workflow_id}' 完成 — 汇总耗时 {summary_ms}ms", {
        "workflow_id": workflow_id, "total_ms": total_ms + summary_ms,
    })

    return {
        "workflow_id": workflow_id,
        "leader_task": leader_task,
        "status": "completed",
        "worker_count": len(workers),
        "workers": worker_results,
        "summary": summary_result,
        "total_duration_ms": total_ms + summary_ms,
    }


def run_team_workflow(
    leader_task: str,
    workers: list[dict[str, str]],
) -> dict[str, Any]:
    """同步包装 — 在新事件循环中运行团队工作流"""
    workflow_id = f"wf-{int(time.time())}"
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_team_workflow_async(workflow_id, leader_task, workers)
            )
        finally:
            loop.close()
    except Exception as e:
        return {
            "workflow_id": workflow_id,
            "leader_task": leader_task,
            "status": "error",
            "error": str(e),
            "workers": [],
            "summary": "",
            "total_duration_ms": 0,
        }


# ─── 串行 Pipeline 工作流 — worker 依次执行，上一步输出传入下一步 ──

async def _run_pipeline_workflow_async(
    workflow_id: str,
    incident_context: dict[str, Any],
    steps: list[dict[str, str]],
) -> dict[str, Any]:
    """异步执行串行 Pipeline 工作流

    流程:
      1. manager(组长) 接收 incident 上下文
      2. 依次执行每个 step (串行):
         - 每个 worker 的 task 可引用 {prev_result} 获取上一步输出
         - 每个 worker 的 task 可引用 {incident.*} 获取故障上下文
      3. 最后用 writer 子代理汇总全链路报告

    steps: [{"agent_type": "infra_inspector", "task": "..."}, ...]
    incident_context: {"incident_id": "...", "scenario": "...", "description": "...", "alerts": [...], ...}
    """
    from sub_agent import run_sub_agent

    _add_activity("team", f"串行 Pipeline '{workflow_id}' 启动 — Incident: {incident_context.get('incident_id', '?')}", {
        "workflow_id": workflow_id,
        "incident_id": incident_context.get("incident_id"),
        "step_count": len(steps),
    })

    context = {
        "_lifecycle": None,
        "_sub_agent_depth": 0,
        "_run_id": f"pipe-{workflow_id}",
    }

    t0 = time.time()
    step_results: list[dict[str, Any]] = []
    prev_result = ""

    for i, step in enumerate(steps):
        agent_type = step.get("agent_type", "research")
        raw_task = step.get("task", "")
        step_label = step.get("label", f"Step {i+1}")

        # 模板替换: {prev_result} 和 {incident.xxx}
        task = raw_task
        task = task.replace("{prev_result}", prev_result)
        for k, v in incident_context.items():
            if isinstance(v, str):
                task = task.replace(f"{{incident.{k}}}", v)
        # 如果没有模板变量，追加 incident 摘要
        if "{incident" not in raw_task and not prev_result:
            incident_summary = _format_incident_summary(incident_context)
            task = f"{incident_summary}\n\n{task}"

        _add_activity("worker", f"[Pipeline Step {i+1}/{len(steps)}] {step_label} — {agent_type} 开始执行", {
            "workflow_id": workflow_id, "step": i+1, "agent_type": agent_type,
        })

        wt0 = time.time()
        try:
            result = await run_sub_agent(agent_type, task, {**context, "_sub_agent_depth": 0}, 0)
            wdt = int((time.time() - wt0) * 1000)
            _record_execution(agent_type, task, result, wdt, "success")
            _add_activity("worker", f"[Step {i+1}] {agent_type} 完成 ({wdt}ms)", {
                "workflow_id": workflow_id, "step": i+1, "duration_ms": wdt,
            })
            step_results.append({
                "step_index": i + 1,
                "label": step_label,
                "agent_type": agent_type,
                "task": task,
                "result": result,
                "duration_ms": wdt,
                "status": "success",
            })
            prev_result = result
        except Exception as e:
            wdt = int((time.time() - wt0) * 1000)
            err = str(e)
            _record_execution(agent_type, task, err, wdt, "error")
            _add_activity("worker", f"[Step {i+1}] {agent_type} 失败: {err[:60]}", {
                "workflow_id": workflow_id, "step": i+1, "error": err[:200],
            })
            step_results.append({
                "step_index": i + 1,
                "label": step_label,
                "agent_type": agent_type,
                "task": task,
                "result": f"错误: {err}",
                "duration_ms": wdt,
                "status": "error",
            })
            prev_result = f"错误: {err}"

    total_ms = int((time.time() - t0) * 1000)

    # ── 组长汇总全链路 ──
    _add_activity("orchestration", f"Pipeline 完成 — {len(step_results)} 步，开始汇总", {
        "workflow_id": workflow_id, "total_ms": total_ms,
    })

    summary_parts = []
    for sr in step_results:
        summary_parts.append(
            f"### Step {sr['step_index']}: {sr['label']} ({sr['agent_type']})\n"
            f"状态: {sr['status']}\n"
            f"耗时: {sr['duration_ms']}ms\n"
            f"结果:\n{sr['result']}"
        )
    combined = "\n\n---\n\n".join(summary_parts)

    summary_task = (
        f"你是运维 SRE 组长。以下是针对 Incident {incident_context.get('incident_id', '?')} "
        f"的串行排查流水线结果，请汇总为一份完整的故障处理报告:\n\n"
        f"故障场景: {incident_context.get('scenario', '?')}\n"
        f"故障描述: {incident_context.get('description', '')}\n\n"
        f"{combined}\n\n"
        f"请输出:\n"
        f"1. 故障概况 (Incident ID/场景/影响)\n"
        f"2. 排查链路总结 (每步关键发现)\n"
        f"3. 根因分析\n"
        f"4. 修复建议与后续行动\n"
    )

    try:
        summary_result = await run_sub_agent("writer", summary_task, {**context, "_sub_agent_depth": 0}, 0)
    except Exception as e:
        summary_result = f"汇总失败: {e}"

    summary_ms = int((time.time() - t0) * 1000) - total_ms
    _add_activity("team", f"串行 Pipeline '{workflow_id}' 全部完成 — 总耗时 {total_ms + summary_ms}ms", {
        "workflow_id": workflow_id, "total_ms": total_ms + summary_ms,
    })

    return {
        "workflow_id": workflow_id,
        "workflow_type": "pipeline",
        "incident": incident_context,
        "status": "completed",
        "step_count": len(step_results),
        "steps": step_results,
        "summary": summary_result,
        "total_duration_ms": total_ms + summary_ms,
    }


def _format_incident_summary(incident: dict[str, Any]) -> str:
    """格式化 incident 上下文为可注入的摘要文本"""
    parts = [f"Incident ID: {incident.get('incident_id', '?')}"]
    parts.append(f"场景: {incident.get('scenario', '?')}")
    if incident.get("description"):
        parts.append(f"故障描述:\n{incident['description']}")
    if incident.get("alerts"):
        parts.append("初始告警:")
        for a in incident["alerts"]:
            parts.append(f"  - {a}")
    if incident.get("environment"):
        parts.append(f"环境: {incident['environment']}")
    if incident.get("customer"):
        parts.append(f"客户: {incident['customer']}")
    return "\n".join(parts)


# ─── 预设场景 ─────────────────────────────────────────

PRESET_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "INC-1014-storage-driver-mismatch",
        "incident_id": "INC-1014",
        "scenario": "storage_driver_mismatch",
        "title": "存储驱动不匹配导致 MySQL 数据损坏",
        "severity": "P0",
        "customer": "杭州示例电商",
        "environment": "docker + docker-compose + mysql + overlay2",
        "description": (
            "20:00 运维在不了解底层存储差异的情况下，将一台新部署的节点 Docker 存储驱动从 overlay2 改为 vfs，"
            "试图排查磁盘性能问题。改动后，该节点上的 MySQL 容器启动失败，"
            "docker inspect 显示容器层文件存在但数据目录为空，"
            "docker logs mysql 报 InnoDB: Database page corrupted。数据丢失风险升级为 P0。"
        ),
        "alerts": [
            "20:01 mysql-master mysql_startup_failed P0 容器启动失败",
            "20:02 docker host storage_driver_change P0 存储驱动变更为 vfs",
            "20:03 mysql-master innodb_corruption_detected P0 InnoDB 数据页损坏",
        ],
        "hypotheses": [
            "存储驱动切换导致层文件系统不兼容，overlay2 层数据在 vfs 驱动下不可正确挂载",
            "切换前未执行 docker save 备份镜像层数据",
            "未同步旧节点 overlay2 元数据到新节点，导致文件系统层 ID 映射丢失",
        ],
        # Matrix Team: 3 个 worker 一次分配并发执行
        "workers": [
            {
                "agent_type": "task_analyzer",
                "task": (
                    "你是 OpsPilot Matrix Team 的 task-analyzer worker。\n"
                    "故障 Incident: INC-1014 — 存储驱动不匹配导致 MySQL 数据损坏\n\n"
                    "故障描述:\n"
                    "  运维在不了解底层存储差异的情况下，将节点 Docker 存储驱动从 overlay2 改为 vfs。\n"
                    "  MySQL 容器启动失败，docker inspect 显示容器层文件存在但数据目录为空，\n"
                    "  docker logs mysql 报 InnoDB: Database page corrupted。数据丢失风险 P0。\n\n"
                    "初始告警:\n"
                    "  - 20:01 mysql-master mysql_startup_failed P0 容器启动失败\n"
                    "  - 20:02 docker host storage_driver_change P0 存储驱动变更为 vfs\n"
                    "  - 20:03 mysql-master innodb_corruption_detected P0 InnoDB 数据页损坏\n\n"
                    "排查假设:\n"
                    "  1. 存储驱动切换导致层文件系统不兼容\n"
                    "  2. 切换前未执行 docker save 备份镜像层数据\n"
                    "  3. 未同步旧节点 overlay2 元数据到新节点\n\n"
                    "请执行:\n"
                    "1. 使用 system_monitor 检查当前节点 CPU/内存/磁盘状态\n"
                    "2. 使用 service_check 检查 MySQL 容器和 Docker daemon 的可达性\n"
                    "3. 使用 log_search 搜索最近的 docker 和 mysql 相关日志\n\n"
                    "输出:\n"
                    "- 故障分类 (基础设施/数据库/存储)\n"
                    "- 影响范围评估\n"
                    "- 优先级确认 (P0/P1/P2)\n"
                    "- 3 个根因排查方向\n"
                    "- 对 change-executor 和 result-verifier 的分派建议"
                ),
            },
            {
                "agent_type": "change_executor",
                "task": (
                    "你是 OpsPilot Matrix Team 的 change-executor worker。\n"
                    "故障 Incident: INC-1014 — 存储驱动不匹配导致 MySQL 数据损坏\n\n"
                    "故障描述:\n"
                    "  运维将节点 Docker 存储驱动从 overlay2 改为 vfs。\n"
                    "  MySQL 容器启动失败，InnoDB: Database page corrupted。\n"
                    "  数据丢失风险 P0。\n\n"
                    "环境: docker + docker-compose + mysql + overlay2\n\n"
                    "请执行:\n"
                    "1. 使用 service_check 确认当前 MySQL 容器和 Docker daemon 状态 (执行前基线)\n"
                    "2. 使用 system_monitor 确认当前磁盘和内存状态\n"
                    "3. 使用 log_search 搜索 'storage_driver', 'vfs', 'overlay2' 变更日志\n\n"
                    "输出:\n"
                    "- 执行前状态记录\n"
                    "- 紧急止血方案: 回滚存储驱动到 overlay2 的分步操作\n"
                    "- 数据恢复方案: 从备份恢复 MySQL 数据 (需审批)\n"
                    "- 长期改进: 存储驱动标准化建议\n"
                    "- 每步的回滚方案\n"
                    "- 执行后预期恢复的服务和指标"
                ),
            },
            {
                "agent_type": "result_verifier",
                "task": (
                    "你是 OpsPilot Matrix Team 的 result-verifier worker。\n"
                    "故障 Incident: INC-1014 — 存储驱动不匹配导致 MySQL 数据损坏\n\n"
                    "故障描述:\n"
                    "  运维将节点 Docker 存储驱动从 overlay2 改为 vfs。\n"
                    "  MySQL 容器启动失败，InnoDB: Database page corrupted。\n"
                    "  数据丢失风险 P0。\n\n"
                    "环境: docker + docker-compose + mysql + overlay2\n\n"
                    "请执行:\n"
                    "1. 使用 service_check 验证 MySQL 容器端口 (3306) 和 HTTP 健康端点\n"
                    "2. 使用 system_monitor 检查当前 CPU/内存/磁盘/负载状态\n"
                    "3. 使用 log_search 搜索修复后的日志，确认无新 ERROR\n\n"
                    "输出:\n"
                    "- 恢复验证清单:\n"
                    "  | 检查项 | 修复前 | 修复后目标 | 当前状态 | 验证方法 |\n"
                    "  |--------|--------|-----------|---------|----------|\n"
                    "- 恢复状态判断: 完全恢复/部分恢复/未恢复\n"
                    "- 24h 观察指标建议\n"
                    "- 7d 观察指标建议\n"
                    "- Incident 关闭建议 (是否可以关闭/前提条件)"
                ),
            },
        ],
        # 保留 steps 字段兼容旧的串行 Pipeline (如果用户选择串行模式)
        "steps": [
            {
                "label": "任务分析 — task_analyzer",
                "agent_type": "task_analyzer",
                "task": (
                    "你是 OpsPilot Matrix Team 的 task-analyzer worker。\n"
                    "故障 Incident: INC-1014 — 存储驱动不匹配导致 MySQL 数据损坏\n\n"
                    "故障描述:\n"
                    "  运维将节点 Docker 存储驱动从 overlay2 改为 vfs。\n"
                    "  MySQL 容器启动失败，InnoDB: Database page corrupted。\n"
                    "  数据丢失风险 P0。\n\n"
                    "初始告警:\n"
                    "  - 20:01 mysql-master mysql_startup_failed P0\n"
                    "  - 20:02 docker host storage_driver_change P0\n"
                    "  - 20:03 mysql-master innodb_corruption_detected P0\n\n"
                    "请执行:\n"
                    "1. 使用 system_monitor 检查节点资源状态\n"
                    "2. 使用 service_check 检查 MySQL 容器可达性\n"
                    "3. 使用 log_search 搜索 docker/mysql 日志\n\n"
                    "输出:\n"
                    "- 故障分类\n"
                    "- 影响范围\n"
                    "- 3 个根因排查方向"
                ),
            },
            {
                "label": "变更执行 — change_executor",
                "agent_type": "change_executor",
                "task": (
                    "你是 OpsPilot Matrix Team 的 change-executor worker。\n"
                    "根据上一步分析，制定变更执行方案:\n\n"
                    "上一步结果:\n{prev_result}\n\n"
                    "请执行:\n"
                    "1. 使用 service_check 确认执行前基线\n"
                    "2. 使用 system_monitor 确认资源状态\n"
                    "3. 使用 log_search 搜索变更日志\n\n"
                    "输出:\n"
                    "- 紧急止血方案\n"
                    "- 数据恢复方案\n"
                    "- 回滚方案"
                ),
            },
            {
                "label": "结果验证 — result_verifier",
                "agent_type": "result_verifier",
                "task": (
                    "你是 OpsPilot Matrix Team 的 result-verifier worker。\n"
                    "验证变更执行效果:\n\n"
                    "执行方案:\n{prev_result}\n\n"
                    "请执行:\n"
                    "1. 使用 service_check 验证服务恢复\n"
                    "2. 使用 system_monitor 检查资源恢复\n"
                    "3. 使用 log_search 确认无新错误\n\n"
                    "输出:\n"
                    "- 恢复验证清单\n"
                    "- Incident 关闭建议"
                ),
            },
        ],
    },
]


def get_preset_scenarios() -> list[dict[str, Any]]:
    """获取所有预设场景"""
    return PRESET_SCENARIOS


def get_preset_scenario(scenario_id: str) -> dict[str, Any] | None:
    """按 ID 获取预设场景"""
    for s in PRESET_SCENARIOS:
        if s["id"] == scenario_id:
            return s
    return None


def run_pipeline_workflow(
    scenario_id: str,
    override_steps: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """同步包装 — 执行串行 Pipeline 工作流

    参数:
        scenario_id: 预设场景 ID (如 'INC-1014-storage-driver-mismatch')
        override_steps: 可选，自定义步骤覆盖预设场景的 steps

    返回:
        工作流执行结果
    """
    scenario = get_preset_scenario(scenario_id)
    if not scenario:
        return {
            "workflow_id": f"pipe-{int(time.time())}",
            "status": "error",
            "error": f"未知场景 ID: {scenario_id}",
            "steps": [],
            "summary": "",
            "total_duration_ms": 0,
        }

    incident_context = {
        "incident_id": scenario.get("incident_id", ""),
        "scenario": scenario.get("scenario", ""),
        "description": scenario.get("description", ""),
        "alerts": scenario.get("alerts", []),
        "environment": scenario.get("environment", ""),
        "customer": scenario.get("customer", ""),
    }

    steps = override_steps or scenario.get("steps", [])

    workflow_id = f"pipe-{int(time.time())}"
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_pipeline_workflow_async(workflow_id, incident_context, steps)
            )
        finally:
            loop.close()
    except Exception as e:
        return {
            "workflow_id": workflow_id,
            "workflow_type": "pipeline",
            "status": "error",
            "error": str(e),
            "steps": [],
            "summary": "",
            "total_duration_ms": 0,
        }


# ─── Matrix Team 并发工作流 — 一次分配多 worker 并发执行 ──

async def _run_matrix_workflow_async(
    workflow_id: str,
    incident_context: dict[str, Any],
    workers: list[dict[str, str]],
) -> dict[str, Any]:
    """异步执行 Matrix Team 并发工作流

    与 team workflow 的区别:
    - 输入是预设场景的 workers 列表 (一次分配)
    - 每份 worker 报告汇总为故障处理报告
    - 带有 incident 上下文
    """
    from sub_agent import run_sub_agent

    _add_activity("team", f"Matrix Team '{workflow_id}' 启动 — 一次分配 {len(workers)} 个 worker 并发执行", {
        "workflow_id": workflow_id,
        "incident_id": incident_context.get("incident_id"),
        "worker_count": len(workers),
    })

    context = {
        "_lifecycle": None,
        "_sub_agent_depth": 0,
        "_run_id": f"matrix-{workflow_id}",
    }

    t0 = time.time()

    _add_activity("orchestration", f"组长一次分配任务给 {len(workers)} 个 worker: {[w['agent_type'] for w in workers]}", {
        "workers": [f"{w['agent_type']}" for w in workers],
    })

    async def _run_one_matrix_worker(worker_spec: dict[str, str], idx: int) -> dict[str, Any]:
        agent_type = worker_spec.get("agent_type", "research")
        task = worker_spec.get("task", "")
        wt0 = time.time()
        try:
            _add_activity("worker", f"[Matrix Worker {idx+1}] {agent_type} 开始执行", {
                "worker_index": idx + 1, "agent_type": agent_type,
            })
            result = await run_sub_agent(agent_type, task, {**context, "_sub_agent_depth": 0}, 0)
            wdt = int((time.time() - wt0) * 1000)
            _record_execution(agent_type, task, result, wdt, "success")
            _add_activity("worker", f"[Matrix Worker {idx+1}] {agent_type} 完成报告 ({wdt}ms)", {
                "worker_index": idx + 1, "agent_type": agent_type, "duration_ms": wdt,
            })
            return {
                "worker_index": idx + 1,
                "agent_type": agent_type,
                "task": task,
                "result": result,
                "duration_ms": wdt,
                "status": "success",
            }
        except Exception as e:
            wdt = int((time.time() - wt0) * 1000)
            err = str(e)
            _record_execution(agent_type, task, err, wdt, "error")
            _add_activity("worker", f"[Matrix Worker {idx+1}] {agent_type} 执行失败: {err[:60]}", {
                "worker_index": idx + 1, "agent_type": agent_type, "error": err[:200],
            })
            return {
                "worker_index": idx + 1,
                "agent_type": agent_type,
                "task": task,
                "result": f"错误: {err}",
                "duration_ms": wdt,
                "status": "error",
            }

    # 并发执行所有 worker
    worker_tasks = [_run_one_matrix_worker(w, i) for i, w in enumerate(workers)]
    worker_results = await asyncio.gather(*worker_tasks)

    total_ms = int((time.time() - t0) * 1000)

    # ── 组长汇总 ──
    _add_activity("orchestration", f"Matrix Team 收齐 {len(worker_results)} 份报告，开始汇总", {
        "workflow_id": workflow_id, "total_ms": total_ms,
    })

    summary_parts = []
    for wr in worker_results:
        summary_parts.append(
            f"### Worker {wr['worker_index']} — {wr['agent_type']} 报告\n"
            f"状态: {wr['status']}\n"
            f"耗时: {wr['duration_ms']}ms\n"
            f"结果:\n{wr['result']}"
        )
    combined = "\n\n---\n\n".join(summary_parts)

    summary_task = (
        f"你是 OpsPilot Matrix Team 的组长 (SRE Lead)。\n"
        f"以下是针对 Incident {incident_context.get('incident_id', '?')} "
        f"的 Matrix Team 并发执行结果，请汇总为一份完整的故障处理报告:\n\n"
        f"故障场景: {incident_context.get('scenario', '?')}\n"
        f"故障描述: {incident_context.get('description', '')}\n\n"
        f"{combined}\n\n"
        f"请输出:\n"
        f"1. 故障概况 (Incident ID/场景/严重等级/影响)\n"
        f"2. 各 Worker 结果摘要 (task_analyzer / change_executor / result_verifier)\n"
        f"3. 综合根因分析\n"
        f"4. 修复方案与执行建议\n"
        f"5. 验证结论与后续监控计划\n"
    )

    try:
        summary_result = await run_sub_agent("writer", summary_task, {**context, "_sub_agent_depth": 0}, 0)
    except Exception as e:
        summary_result = f"汇总失败: {e}"

    summary_ms = int((time.time() - t0) * 1000) - total_ms
    _add_activity("team", f"Matrix Team '{workflow_id}' 全部完成 — 总耗时 {total_ms + summary_ms}ms", {
        "workflow_id": workflow_id, "total_ms": total_ms + summary_ms,
    })

    return {
        "workflow_id": workflow_id,
        "workflow_type": "matrix",
        "incident": incident_context,
        "status": "completed",
        "worker_count": len(workers),
        "workers": worker_results,
        "summary": summary_result,
        "total_duration_ms": total_ms + summary_ms,
    }


def run_matrix_workflow(
    scenario_id: str,
    override_workers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """同步包装 — 执行 Matrix Team 并发工作流

    参数:
        scenario_id: 预设场景 ID
        override_workers: 可选，自定义 workers 覆盖预设场景的 workers

    返回:
        工作流执行结果
    """
    scenario = get_preset_scenario(scenario_id)
    if not scenario:
        return {
            "workflow_id": f"matrix-{int(time.time())}",
            "status": "error",
            "error": f"未知场景 ID: {scenario_id}",
            "workers": [],
            "summary": "",
            "total_duration_ms": 0,
        }

    incident_context = {
        "incident_id": scenario.get("incident_id", ""),
        "scenario": scenario.get("scenario", ""),
        "description": scenario.get("description", ""),
        "alerts": scenario.get("alerts", []),
        "environment": scenario.get("environment", ""),
        "customer": scenario.get("customer", ""),
    }

    workers = override_workers or scenario.get("workers", [])
    workflow_id = f"matrix-{int(time.time())}"

    if not workers:
        return {
            "workflow_id": workflow_id,
            "status": "error",
            "error": "场景没有 workers 定义",
            "workers": [],
            "summary": "",
            "total_duration_ms": 0,
        }

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_matrix_workflow_async(workflow_id, incident_context, workers)
            )
        finally:
            loop.close()
    except Exception as e:
        return {
            "workflow_id": workflow_id,
            "workflow_type": "matrix",
            "status": "error",
            "error": str(e),
            "workers": [],
            "summary": "",
            "total_duration_ms": 0,
        }


# ─── OA Team 定义 + Matrix Team 房间 ─────────────────────

# Team 定义
OA_TEAM: dict[str, Any] = {
    "team_id": "oa-team",
    "team_name": "OA Team",
    "description": "运维故障处理团队 — 1 个 TeamLeader + 3 个业务 Worker",
    "leader": "oa_team_leader",  # 独立的 TeamLeader Worker
    "workers": ["task_analyzer", "change_executor", "result_verifier"],
    "rules": [
        "使用当前配置的真实 LLM (DeepSeek) 完成推理和协作",
        "manager 只负责创建和管理，不参与团队执行",
        "事故任务由 oa-team 的 Matrix Team 房间接收",
        "用户在消息开头 @oa-team-leader 来触发组长",
        "3 个业务 Worker 的 AgentSpec、Skill、工具契约都已内联",
        "3 个 Worker 只作为被 TeamLeader 调度的专业角色，不承担 TeamLeader 身份",
    ],
    "leader_agent_type": "oa_team_leader",
    "leader_tools": ["delegate_sub_agent", "system_monitor", "service_check"],
    "leader_max_turns": 12,
    "worker_specs": {
        "task_analyzer": {
            "role": "Alert Intake Agent",
            "mission": "将零散客户客诉、监控告警和高层指标归并成事故候选，输出影响范围、严重等级、时间线和证据索引。",
            "tools": ["system_monitor", "service_check", "log_search"],
            "max_turns": 8,
            "skills": ["alert-fusion: 按服务、时间窗口、症状和影响面合并相关客诉与告警", "impact-mapping: 推断受影响服务、接口、用户动作、业务影响和严重等级"],
            "output_contract": {
                "incident_id": "INC-xxxx",
                "severity": "P1/P2/P3",
                "affected_services": [],
                "timeline": [],
                "symptoms": [],
                "evidence_refs": [],
            },
        },
        "change_executor": {
            "role": "Remediation Planner Agent",
            "mission": "将 RCA 结论转换成安全的修复计划、验证计划、回滚点和审批任务。",
            "tools": ["service_check", "system_monitor", "log_search"],
            "max_turns": 8,
            "skills": ["remediation-plan: 生成修复步骤、验证步骤和回滚步骤", "risk-guard: 按风险等级判断是否允许自动执行"],
            "output_contract": {
                "risk_level": "L0/L1/L2/L3",
                "auto_execute": True,
                "auto_actions": [],
                "approval_actions": [],
                "validation": [],
                "rollback_point": "",
            },
        },
        "result_verifier": {
            "role": "Recovery Verifier Agent",
            "mission": "两阶段核验（方案审核 → 执行核验）。",
            "tools": ["service_check", "system_monitor", "log_search"],
            "max_turns": 8,
            "skills": ["recovery-verify: 审核", "data-advisor: 执行核验"],
            "output_contract": {
                "recovered": True,
                "executed_actions": [],
                "approval_actions": [],
                "verification": [],
                "postmortem_notes": [],
                "telemetry_advice": [],
            },
        },
    },
}


# Matrix Team 房间 — 每个 Team 一个独立的执行房间
_TEAM_ROOMS: dict[str, dict[str, Any]] = {}


def _get_or_create_team_room(team_id: str) -> dict[str, Any]:
    """获取或创建 Team 的 Matrix Team 房间

    房间包含:
    - team 定义
    - 成员列表 (leader + workers)
    - 消息历史
    - 状态
    """
    if team_id not in _TEAM_ROOMS:
        team_def = OA_TEAM if team_id == "oa-team" else None
        if not team_def:
            return {}
        _TEAM_ROOMS[team_id] = {
            "room_id": f"room-{team_id}-{int(time.time())}",
            "team_id": team_id,
            "team": team_def,
            "members": {
                "leader": team_def["leader"],
                "workers": team_def["workers"],
            },
            "messages": [],
            "status": "ready",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _add_activity("team", f"创建 Matrix Team 房间: {team_id} — 组长: {team_def['leader']}, Workers: {team_def['workers']}", {
            "team_id": team_id,
            "leader": team_def["leader"],
            "workers": team_def["workers"],
        })
    return _TEAM_ROOMS[team_id]


def get_team_info(team_id: str = "oa-team") -> dict[str, Any]:
    """获取 Team 信息（包括成员、规则、房间状态）"""
    team_def = OA_TEAM if team_id == "oa-team" else None
    if not team_def:
        return {"error": f"未知 Team: {team_id}"}

    room = _get_or_create_team_room(team_id)
    return {
        "team_id": team_def["team_id"],
        "team_name": team_def["team_name"],
        "description": team_def["description"],
        "leader": team_def["leader"],
        "leader_agent_type": team_def["leader_agent_type"],
        "leader_tools": team_def["leader_tools"],
        "leader_max_turns": team_def["leader_max_turns"],
        "workers": team_def["workers"],
        "worker_specs": team_def["worker_specs"],
        "rules": team_def["rules"],
        "room": {
            "room_id": room.get("room_id"),
            "status": room.get("status"),
            "created_at": room.get("created_at"),
            "message_count": len(room.get("messages", [])),
        },
    }


def list_teams() -> list[dict[str, Any]]:
    """列出所有 Team"""
    return [get_team_info("oa-team")]


async def _run_oa_team_async(
    workflow_id: str,
    user_message: str,
    incident_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """异步执行 oa-team 工作流 — 串行 Pipeline 协作模式

    流程:
      1. 确认 Matrix Team 房间已创建
      2. 用户消息 @oa-team-leader 触发组长
      3. 组长 (oa_team_leader) 先分析任务，制定排查计划
      4. 串行执行 3 个 Worker (上一步输出传入下一步):
         a. task_analyzer: 分析根因 → 输出根因报告
         b. change_executor: 拿到根因 → 制定修复方案
         c. result_verifier: 拿到修复方案 → 制定验证计划
      5. 组长收齐 3 份报告，汇总为故障处理报告
    """
    from sub_agent import run_sub_agent

    room = _get_or_create_team_room("oa-team")
    room["status"] = "executing"

    # 记录用户消息到房间
    room["messages"].append({
        "role": "user",
        "content": user_message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    _add_activity("team", f"oa-team 房间收到消息 — @oa-team-leader 触发组长 (串行协作模式)", {
        "workflow_id": workflow_id,
        "room_id": room["room_id"],
        "message_preview": user_message[:100],
    })

    context = {
        "_lifecycle": None,
        "_sub_agent_depth": 0,
        "_run_id": f"oateam-{workflow_id}",
    }

    t0 = time.time()
    team_def = OA_TEAM
    worker_specs = team_def["worker_specs"]

    # ── Step 1: 组长先分析任务，制定排查计划 ──
    leader_input = user_message
    if incident_context:
        incident_summary = _format_incident_summary(incident_context)
        leader_input = f"{incident_summary}\n\n---\n\n用户消息:\n{user_message}"

    _add_activity("orchestration", f"组长 oa_team_leader 开始分析任务并制定排查计划", {
        "workflow_id": workflow_id,
    })

    leader_plan_task = (
        f"你是 oa-team 的 TeamLeader (oa-team-leader)。\n"
        f"你是运维 SRE 组长，负责事故协调和任务分解。\n\n"
        f"收到以下事故消息:\n{leader_input}\n\n"
        f"请快速分析并输出:\n"
        f"1. 事故摘要 (1-2 句话)\n"
        f"2. 严重等级判断 (P0/P1/P2)\n"
        f"3. 排查优先级 (先查什么，后查什么)\n"
        f"4. 对三个 Worker 的分派建议:\n"
        f"   - task_analyzer 应重点分析什么\n"
        f"   - change_executor 应准备什么方案\n"
        f"   - result_verifier 应验证什么指标\n"
        f"请简洁输出，不要超过 500 字。"
    )

    try:
        leader_plan = await run_sub_agent("oa_team_leader", leader_plan_task, {**context, "_sub_agent_depth": 0}, 0)
    except Exception as e:
        leader_plan = f"组长分析失败，使用原始消息继续: {e}"

    plan_ms = int((time.time() - t0) * 1000)
    _add_activity("orchestration", f"组长分析完成 ({plan_ms}ms)，开始串行分派", {
        "workflow_id": workflow_id, "plan_ms": plan_ms,
    })

    # ── Step 2: 串行执行 3 个 Worker (Pipeline 协作) ──
    # 执行顺序: task_analyzer → change_executor → result_verifier
    # 每个 Worker 拿到上一个 Worker 的输出 + 组长的分派建议

    worker_chain = [
        ("task_analyzer", "根因分析"),
        ("change_executor", "修复方案"),
        ("result_verifier", "验证计划"),
    ]

    worker_results: list[dict[str, Any]] = []
    prev_result = ""  # 上一个 Worker 的输出

    for idx, (worker_name, phase_label) in enumerate(worker_chain):
        spec = worker_specs.get(worker_name, {})
        role = spec.get("role", "")
        mission = spec.get("mission", "")
        skills_list = spec.get("skills", [])
        output_contract = spec.get("output_contract", {})

        skills_str = "\n".join(f"  - {s}" for s in skills_list) if skills_list else "  (无)"
        contract_str = ""
        if output_contract:
            import json as _json
            contract_str = f"\n输出契约 (JSON):\n{_json.dumps(output_contract, ensure_ascii=False, indent=2)}\n"

        # 构建 Worker 任务 — 包含组长的分派建议 + 上一个 Worker 的结果
        prev_context_str = ""
        if prev_result:
            prev_context_str = (
                f"\n{'='*50}\n"
                f"上一阶段 Worker 报告 (请基于此继续工作):\n"
                f"{'='*50}\n"
                f"{prev_result}\n\n"
            )

        worker_task = (
            f"你是 oa-team 的 {worker_name} ({role})。\n"
            f"Mission: {mission}\n\n"
            f"Skills:\n{skills_str}\n"
            f"{contract_str}\n"
            f"{'='*50}\n"
            f"组长 @oa_team_leader 的分派建议:\n"
            f"{'='*50}\n"
            f"{leader_plan}\n\n"
            f"{prev_context_str}"
            f"{'='*50}\n"
            f"原始事故消息:\n"
            f"{'='*50}\n"
            f"{leader_input}\n\n"
            f"请执行你的专业职责，使用你的工具完成{phase_label}，"
            f"然后按输出契约输出结构化报告。"
        )

        _add_activity("worker", f"[Pipeline Step {idx+1}/3] {worker_name} 开始执行 — {phase_label}", {
            "workflow_id": workflow_id, "step": idx + 1,
            "agent_type": worker_name, "phase": phase_label,
            "has_prev_result": bool(prev_result),
        })

        wt0 = time.time()
        try:
            result = await run_sub_agent(worker_name, worker_task, {**context, "_sub_agent_depth": 0}, 0)
            wdt = int((time.time() - wt0) * 1000)
            _record_execution(worker_name, worker_task, result, wdt, "success")
            _add_activity("worker", f"[Step {idx+1}] {worker_name} 完成 {phase_label} ({wdt}ms)", {
                "workflow_id": workflow_id, "step": idx + 1, "duration_ms": wdt,
            })
            wr = {
                "step_index": idx + 1,
                "agent_type": worker_name,
                "role": role,
                "phase": phase_label,
                "task": worker_task,
                "result": result,
                "duration_ms": wdt,
                "status": "success",
                "received_prev_result": bool(prev_result),
            }
            worker_results.append(wr)
            prev_result = result  # 传递给下一个 Worker
        except Exception as e:
            wdt = int((time.time() - wt0) * 1000)
            err = str(e)
            _record_execution(worker_name, worker_task, err, wdt, "error")
            _add_activity("worker", f"[Step {idx+1}] {worker_name} 失败: {err[:60]}", {
                "workflow_id": workflow_id, "step": idx + 1, "error": err[:200],
            })
            wr = {
                "step_index": idx + 1,
                "agent_type": worker_name,
                "role": role,
                "phase": phase_label,
                "task": worker_task,
                "result": f"错误: {err}",
                "duration_ms": wdt,
                "status": "error",
                "received_prev_result": bool(prev_result),
            }
            worker_results.append(wr)
            prev_result = f"错误: {err}"  # 传递错误信息给下一个 Worker

    pipeline_ms = int((time.time() - t0) * 1000)

    # ── Step 3: 组长汇总全链路 ──
    _add_activity("orchestration", f"Pipeline 3 步全部完成 ({pipeline_ms}ms)，组长开始汇总", {
        "workflow_id": workflow_id, "pipeline_ms": pipeline_ms,
    })

    summary_parts = []
    for wr in worker_results:
        summary_parts.append(
            f"### Step {wr['step_index']}: {wr['agent_type']} ({wr['role']}) — {wr['phase']}\n"
            f"状态: {wr['status']}\n"
            f"耗时: {wr['duration_ms']}ms\n"
            f"收到上一阶段结果: {'是' if wr.get('received_prev_result') else '否'}\n"
            f"结果:\n{wr['result']}"
        )
    combined = "\n\n---\n\n".join(summary_parts)

    leader_summary_task = (
        f"你是 oa-team 的 TeamLeader (oa-team-leader)。\n"
        f"以下是你的 3 个业务 Worker 串行协作提交的报告。\n"
        f"每个 Worker 都收到了上一个 Worker 的输出，形成了完整的排查链路。\n\n"
        f"组长分析计划:\n{leader_plan}\n\n"
        f"用户原始消息:\n{user_message}\n\n"
        f"{combined}\n\n"
        f"请汇总为完整的故障处理报告，包含:\n"
        f"1. 故障概况 (Incident ID/场景/严重等级/影响)\n"
        f"2. 根因分析 (基于 task_analyzer 的发现)\n"
        f"3. 修复方案 (基于 change_executor 的方案，含风险等级)\n"
        f"4. 验证结论 (基于 result_verifier 的验证结果)\n"
        f"5. 协作链路总结 (每步关键发现如何影响下一步决策)\n"
        f"6. 后续监控计划与改进建议\n"
    )

    try:
        summary_result = await run_sub_agent("oa_team_leader", leader_summary_task, {**context, "_sub_agent_depth": 0}, 0)
    except Exception as e:
        try:
            summary_result = await run_sub_agent("writer", leader_summary_task, {**context, "_sub_agent_depth": 0}, 0)
        except Exception as e2:
            summary_result = f"汇总失败: {e}; 降级也失败: {e2}"

    total_ms = int((time.time() - t0) * 1000)
    _add_activity("team", f"oa-team 串行协作工作流完成 — 总耗时 {total_ms}ms", {
        "workflow_id": workflow_id, "total_ms": total_ms,
    })

    room["status"] = "completed"
    room["messages"].append({
        "role": "leader",
        "content": summary_result,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    return {
        "workflow_id": workflow_id,
        "workflow_type": "oa_team_pipeline",
        "team_id": "oa-team",
        "team_leader": "oa_team_leader",
        "room_id": room["room_id"],
        "status": "completed",
        "leader_plan": leader_plan,
        "worker_count": len(worker_results),
        "workers": worker_results,
        "leader_summary": summary_result,
        "summary": summary_result,  # 兼容前端字段
        "pipeline_mode": "serial",  # 标记串行协作模式
        "total_duration_ms": total_ms,
    }


def run_oa_team(
    user_message: str,
    incident_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """同步包装 — 执行 oa-team 工作流

    参数:
        user_message: 用户 @oa-team-leader 的事故消息
        incident_context: 可选的 incident 上下文 (incident_id, scenario, ...)

    返回:
        工作流执行结果 (含 worker 报告 + 组长汇总)
    """
    workflow_id = f"oateam-{int(time.time())}"
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_oa_team_async(workflow_id, user_message, incident_context)
            )
        finally:
            loop.close()
    except Exception as e:
        return {
            "workflow_id": workflow_id,
            "workflow_type": "oa_team",
            "status": "error",
            "error": str(e),
            "workers": [],
            "summary": "",
            "leader_summary": "",
            "total_duration_ms": 0,
        }


# ─── 聊天室机制 — 多任务模式，用户可随时 @worker 咨询进度 ─────────

# 聊天室锁 — 保护 _CHAT_TASKS 和当前选中任务的并发访问
_chat_room_lock = threading.Lock()

# 多任务存储 — 每个任务有独立的消息历史和 worker 状态
# 结构: { task_id: { id, title, messages, worker_status, created_at, last_active_at } }
_CHAT_TASKS: dict[str, dict[str, Any]] = {}

# 当前选中的任务 ID
_CURRENT_TASK_ID: str = ""

# 任务取消标志 — key: task_id, value: True 表示需要取消
_CHAT_CANCEL_FLAGS: dict[str, bool] = {}

# 聊天室固定成员
_CHAT_MEMBERS = ["oa_team_leader", "task_analyzer", "change_executor", "result_verifier"]


def _init_default_task() -> str:
    """初始化默认任务（如果没有任何任务）"""
    global _CURRENT_TASK_ID
    with _chat_room_lock:
        if not _CHAT_TASKS:
            task_id = f"task-{int(time.time())}"
            _CHAT_TASKS[task_id] = {
                "id": task_id,
                "title": "默认任务",
                "messages": [],
                "worker_status": {w: "idle" for w in _CHAT_MEMBERS},
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "last_active_at": time.time(),
            }
            _CURRENT_TASK_ID = task_id
        if not _CURRENT_TASK_ID or _CURRENT_TASK_ID not in _CHAT_TASKS:
            _CURRENT_TASK_ID = list(_CHAT_TASKS.keys())[0]
        return _CURRENT_TASK_ID


def _parse_mentions(message: str) -> list[str]:
    """从消息中解析 @mention，返回被提及的 worker 名称列表"""
    import re
    # 匹配 @worker_name (支持下划线和连字符)
    matches = re.findall(r'@([a-z_]+)', message.lower())
    # 过滤出有效的 worker 名称
    valid_workers = set(OA_TEAM["workers"] + [OA_TEAM["leader"]])
    return [m for m in matches if m in valid_workers]


def get_chat_room() -> dict[str, Any]:
    """获取聊天室信息（成员、状态、当前任务）"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id, {})
        return {
            "room_id": f"chatroom",
            "members": list(_CHAT_MEMBERS),
            "status": "open",
            "created_at": task.get("created_at", ""),
            "message_count": len(task.get("messages", [])),
            "worker_status": dict(task.get("worker_status", {})),
            "current_task_id": task_id,
        }


def get_chat_tasks() -> list[dict[str, Any]]:
    """获取所有任务列表（按最后活跃时间排序）"""
    _init_default_task()
    with _chat_room_lock:
        tasks = []
        for tid, t in _CHAT_TASKS.items():
            tasks.append({
                "id": t["id"],
                "title": t["title"],
                "message_count": len(t["messages"]),
                "created_at": t["created_at"],
                "last_active_at": t.get("last_active_at", 0),
                "is_current": tid == _CURRENT_TASK_ID,
                "worker_status": dict(t.get("worker_status", {})),
            })
        # 按最后活跃时间降序
        tasks.sort(key=lambda x: x["last_active_at"], reverse=True)
        return tasks


def get_chat_task_detail(task_id: str) -> dict[str, Any]:
    """获取单个任务详情（含消息历史）"""
    with _chat_room_lock:
        t = _CHAT_TASKS.get(task_id)
        if not t:
            return {"error": f"任务不存在: {task_id}"}
        return {
            "id": t["id"],
            "title": t["title"],
            "messages": list(t["messages"][-200:]),
            "worker_status": dict(t.get("worker_status", {})),
            "created_at": t["created_at"],
            "message_count": len(t["messages"]),
        }


def create_chat_task(title: str = "") -> dict[str, Any]:
    """创建新任务"""
    global _CURRENT_TASK_ID
    with _chat_room_lock:
        task_id = f"task-{int(time.time() * 1000)}"
        task_title = title.strip()[:60] if title.strip() else f"任务-{len(_CHAT_TASKS) + 1}"
        _CHAT_TASKS[task_id] = {
            "id": task_id,
            "title": task_title,
            "messages": [],
            "worker_status": {w: "idle" for w in _CHAT_MEMBERS},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_active_at": time.time(),
        }
        _CURRENT_TASK_ID = task_id
    _add_activity("team", f"创建新任务: {task_title}", {"task_id": task_id})
    return {"status": "ok", "task_id": task_id, "title": task_title}


def select_chat_task(task_id: str) -> dict[str, Any]:
    """切换当前选中任务"""
    global _CURRENT_TASK_ID
    with _chat_room_lock:
        if task_id not in _CHAT_TASKS:
            return {"error": f"任务不存在: {task_id}"}
        _CURRENT_TASK_ID = task_id
    return {"status": "ok", "current_task_id": task_id}


def delete_chat_task(task_id: str) -> dict[str, Any]:
    """删除任务"""
    global _CURRENT_TASK_ID
    with _chat_room_lock:
        if task_id not in _CHAT_TASKS:
            return {"error": f"任务不存在: {task_id}"}
        del _CHAT_TASKS[task_id]
        # 如果删除的是当前任务，切换到第一个
        if _CURRENT_TASK_ID == task_id:
            _CURRENT_TASK_ID = list(_CHAT_TASKS.keys())[0] if _CHAT_TASKS else ""
    _add_activity("team", f"删除任务: {task_id}", {})
    return {"status": "ok", "current_task_id": _CURRENT_TASK_ID}


def rename_chat_task(task_id: str, title: str) -> dict[str, Any]:
    """重命名任务"""
    with _chat_room_lock:
        t = _CHAT_TASKS.get(task_id)
        if not t:
            return {"error": f"任务不存在: {task_id}"}
        t["title"] = title.strip()[:60] if title.strip() else t["title"]
    return {"status": "ok", "title": t["title"]}


def get_chat_history(limit: int = 100) -> list[dict[str, Any]]:
    """获取当前任务的消息历史"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id, {})
        return list(task.get("messages", [])[-limit:])


def _add_chat_message(role: str, sender: str, content: str, mention: str = "", duration_ms: int = 0, status: str = "done") -> dict[str, Any]:
    """添加一条消息到当前任务（线程安全）"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id, {})
        if not task:
            return {}
        msg = {
            "id": f"msg-{int(time.time() * 1000)}-{len(task['messages'])}",
            "role": role,        # user / worker / leader / system
            "sender": sender,     # 发送者名称
            "content": content,
            "mention": mention,   # 如果是 @worker 消息，记录被 @ 的 worker
            "duration_ms": duration_ms,
            "status": status,     # done / error / thinking
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        task["messages"].append(msg)
        # 保留最近 200 条
        if len(task["messages"]) > 200:
            task["messages"] = task["messages"][-200:]
        task["last_active_at"] = time.time()
        return msg


def _get_worker_status_snapshot() -> dict[str, str]:
    """线程安全地获取当前任务的 worker 状态快照"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id, {})
        return dict(task.get("worker_status", {}))


def _get_message_count() -> int:
    """线程安全地获取当前任务的消息数量"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id, {})
        return len(task.get("messages", []))


def _set_worker_status(worker: str, status: str) -> None:
    """线程安全地设置当前任务的 worker 状态"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id)
        if task:
            task["worker_status"][worker] = status


async def _send_chat_message_async(message: str, on_event=None) -> dict[str, Any]:
    """异步处理用户发送的聊天消息

    逻辑:
      0. 如果当前任务已有消息，自动创建新任务（每个问题独立一个任务）
      1. 记录用户消息到聊天室
      2. 解析 @mention，确定要路由到哪些 worker
      3. 如果 @oa_team_leader 或没有 @mention → 组长接收 (分发 + 汇总)
      4. 如果 @task_analyzer / @change_executor / @result_verifier → 对应 worker 回复
      5. worker 执行完毕后回复到聊天室

    on_event: 可选回调函数 (Callable[[dict], None])，收到每个事件时调用
              事件类型: message / worker_status / done
    """
    from sub_agent import run_sub_agent

    # ── 取消检查函数 ──
    def _is_cancelled() -> bool:
        return _CHAT_CANCEL_FLAGS.get(task_id, False) if 'task_id' in dir() else False

    # ── SSE 事件辅助函数 ──
    def _emit(msg: dict) -> None:
        if on_event:
            on_event({"type": "message", "message": msg})

    def _add_and_emit(role, sender, content, mention="", duration_ms=0, status="done"):
        msg = _add_chat_message(role, sender, content, mention=mention, duration_ms=duration_ms, status=status)
        _emit(msg)
        return msg

    def _set_status_and_emit(worker: str, status: str) -> None:
        _set_worker_status(worker, status)
        if on_event:
            on_event({"type": "worker_status", "worker": worker, "status": status})

    # 0. 自动创建新任务：如果当前任务已有消息历史，创建新任务
    task_id = _init_default_task()
    # 清除取消标志
    _CHAT_CANCEL_FLAGS.pop(task_id, None)
    with _chat_room_lock:
        cur_task = _CHAT_TASKS.get(task_id, {})
        has_messages = len(cur_task.get("messages", [])) > 0

    if has_messages:
        # 从消息内容截取标题 (去掉 @mention 前缀，取前 40 字)
        import re as _re
        title_raw = message.strip()
        title_clean = _re.sub(r'^@\w+\s*', '', title_raw)
        task_title = (title_clean or title_raw)[:40]
        if not task_title:
            task_title = f"任务-{len(_CHAT_TASKS) + 1}"
        create_chat_task(task_title)

    # 1. 记录用户消息
    _add_and_emit("user", "user", message)
    mentions = _parse_mentions(message)

    _add_activity("team", f"聊天室收到消息 — mentions: {mentions or '(无)'}", {
        "mentions": mentions,
        "message_preview": message[:100],
    })

    context = {
        "_lifecycle": None,
        "_sub_agent_depth": 0,
        "_run_id": f"chat-{int(time.time())}",
    }

    # 2. 如果没有 @mention，默认 @oa_team_leader
    if not mentions:
        mentions = ["oa_team_leader"]

    results = []

    for mention in mentions:
        # 3. 路由到对应 worker
        if mention == "oa_team_leader":
            # 组长接收 → 串行 Pipeline 协作 (task_analyzer → change_executor → result_verifier)
            _set_status_and_emit("oa_team_leader", "thinking")
            _add_and_emit("system", "system", f"📋 oa_team_leader 正在分析任务，准备串行分派给 3 个 Worker...", mention="oa_team_leader", status="thinking")

            t0 = time.time()
            team_def = OA_TEAM
            worker_specs = team_def["worker_specs"]

            # ── 组长先分析任务 ──
            leader_plan_task = (
                f"你是 oa-team 聊天室的 TeamLeader (oa-team-leader)。\n"
                f"你是运维 SRE 组长。收到以下消息:\n{message}\n\n"
                f"请快速分析并输出:\n"
                f"1. 事故摘要\n2. 严重等级 (P0/P1/P2)\n3. 排查优先级\n"
                f"4. 对三个 Worker 的分派建议\n请简洁输出，不超过 500 字。"
            )
            try:
                leader_plan = await run_sub_agent("oa_team_leader", leader_plan_task, {**context, "_sub_agent_depth": 0}, 0)
            except Exception as e:
                leader_plan = f"组长分析失败，使用原始消息: {e}"

            _set_status_and_emit("oa_team_leader", "dispatching")
            _add_and_emit("system", "system", f"📋 组长分析完成，开始串行分派:\n{leader_plan[:300]}", mention="oa_team_leader", status="thinking")

            # ── 取消检查 ──
            if _is_cancelled():
                _add_and_emit("system", "system", "⛔ 任务已被用户停止", mention="oa_team_leader", status="error")
                _set_status_and_emit("oa_team_leader", "idle")
                break

            # ── 串行 Pipeline: task_analyzer → change_executor → result_verifier ──
            worker_chain = [
                ("task_analyzer", "根因分析"),
                ("change_executor", "修复方案"),
                ("result_verifier", "验证计划"),
            ]

            worker_results = []
            prev_result = ""

            for idx, (worker_name, phase_label) in enumerate(worker_chain):
                # ── 取消检查 ──
                if _is_cancelled():
                    _add_and_emit("system", "system", f"⛔ 任务已停止 — 已完成 {idx}/{len(worker_chain)} 步", mention="oa_team_leader", status="error")
                    # 将所有未执行的 worker 状态设为 idle
                    for w, _ in worker_chain[idx:]:
                        _set_status_and_emit(w, "idle")
                    _set_status_and_emit("oa_team_leader", "idle")
                    break

                spec = worker_specs.get(worker_name, {})
                role = spec.get("role", "")
                mission = spec.get("mission", "")
                skills_list = spec.get("skills", [])
                skills_str = "\n".join(f"  - {s}" for s in skills_list) if skills_list else "  (无)"

                prev_context_str = ""
                if prev_result:
                    prev_context_str = f"\n上一阶段 Worker 报告:\n{prev_result}\n\n"

                worker_task = (
                    f"你是 oa-team 聊天室的 {worker_name} ({role})。\n"
                    f"Mission: {mission}\n\n"
                    f"Skills:\n{skills_str}\n\n"
                    f"组长分派建议:\n{leader_plan}\n\n"
                    f"{prev_context_str}"
                    f"用户消息:\n{message}\n\n"
                    f"请执行你的专业职责，完成{phase_label}，输出结构化报告。"
                )

                _set_status_and_emit(worker_name, "working")
                _add_and_emit("system", "system", f"⏳ [Step {idx+1}/3] {worker_name} 正在执行 — {phase_label}...", mention=worker_name, status="thinking")

                wt0 = time.time()
                try:
                    result = await run_sub_agent(worker_name, worker_task, {**context, "_sub_agent_depth": 0}, 0)
                    wdt = int((time.time() - wt0) * 1000)
                    _set_status_and_emit(worker_name, "done")
                    _add_and_emit("worker", worker_name, result, mention=worker_name, duration_ms=wdt, status="done")
                    worker_results.append({"agent_type": worker_name, "step": idx + 1, "phase": phase_label, "result": result, "duration_ms": wdt, "status": "done", "received_prev": bool(prev_result)})
                    prev_result = result
                except Exception as e:
                    wdt = int((time.time() - wt0) * 1000)
                    err = str(e)
                    _set_status_and_emit(worker_name, "error")
                    _add_and_emit("worker", worker_name, f"❌ 执行失败: {err}", mention=worker_name, duration_ms=wdt, status="error")
                    worker_results.append({"agent_type": worker_name, "step": idx + 1, "phase": phase_label, "result": f"错误: {err}", "duration_ms": wdt, "status": "error", "received_prev": bool(prev_result)})
                    prev_result = f"错误: {err}"

            # 组长汇总
            # ── 取消检查 ──
            if _is_cancelled():
                _set_status_and_emit("oa_team_leader", "idle")
                break

            _set_status_and_emit("oa_team_leader", "summarizing")
            _add_and_emit("system", "system", "📋 oa_team_leader 正在汇总串行协作报告...", mention="oa_team_leader", status="thinking")

            summary_parts = []
            for wr in worker_results:
                # 截断 worker 结果，避免 prompt 过长
                wr_text = wr['result'] or ''
                if len(wr_text) > 800:
                    wr_text = wr_text[:800] + '...(已截断)'
                summary_parts.append(
                    f"### Step {wr['step']}: {wr['agent_type']} — {wr['phase']}\n"
                    f"状态: {wr['status']} | 耗时: {wr['duration_ms']}ms | 收到上步结果: {'是' if wr.get('received_prev') else '否'}\n"
                    f"结果摘要:\n{wr_text}"
                )
            combined = "\n\n---\n\n".join(summary_parts)

            leader_task = (
                f"你是 oa-team 聊天室的 TeamLeader (oa-team-leader)。\n"
                f"以下是 3 个 Worker 串行协作的报告摘要 (每个 Worker 都收到了上一个 Worker 的输出):\n\n"
                f"组长分析计划:\n{leader_plan[:500]}\n\n"
                f"用户消息:\n{message[:500]}\n\n"
                f"{combined}\n\n"
                f"请汇总为完整的故障处理报告:\n"
                f"1. 故障概况\n2. 根因分析\n3. 修复方案\n4. 验证结论\n5. 协作链路总结\n6. 后续建议\n"
            )

            try:
                summary_result = await run_sub_agent("oa_team_leader", leader_task, {**context, "_sub_agent_depth": 0}, 0)
            except Exception as e:
                try:
                    summary_result = await run_sub_agent("writer", leader_task, {**context, "_sub_agent_depth": 0}, 0)
                except Exception as e2:
                    summary_result = f"汇总失败: {e}; 降级失败: {e2}"

            total_ms = int((time.time() - t0) * 1000)
            _set_status_and_emit("oa_team_leader", "done")
            _add_and_emit("leader", "oa_team_leader", summary_result, mention="oa_team_leader", duration_ms=total_ms, status="done")

            results.append({
                "mention": "oa_team_leader",
                "status": "done",
                "pipeline_mode": "serial",
                "leader_plan": leader_plan,
                "workers": worker_results,
                "summary": summary_result,
                "duration_ms": total_ms,
            })

        elif mention in OA_TEAM["workers"]:
            # 禁止直接给业务 Worker 派任务 — 必须通过组长 oa_team_leader 分派
            _add_and_emit("system", "system",
                f"⚠️ 禁止直接 @{mention} 分派任务。\n"
                f"业务 Worker (task_analyzer / change_executor / result_verifier) "
                f"只能由组长 @oa_team_leader 调度，不能直接接受用户任务。\n"
                f"请使用 @oa_team_leader 来提交任务，组长会串行分派给 3 个 Worker 协作执行。",
                mention=mention, status="error")
            results.append({
                "mention": mention,
                "status": "rejected",
                "reason": "业务 Worker 不能直接接受用户任务，请通过 @oa_team_leader 提交",
            })

        else:
            _add_and_emit("system", "system", f"⚠️ 未知 worker: @{mention}", mention=mention, status="error")

    # 发射 done 事件
    if on_event:
        on_event({"type": "done"})

    return {
        "status": "done",
        "mentions": mentions,
        "results": results,
        "worker_status": _get_worker_status_snapshot(),
        "message_count": _get_message_count(),
    }


def send_chat_message_async(message: str) -> dict[str, Any]:
    """同步包装 — 处理用户发送的聊天消息

    参数:
        message: 用户消息 (可包含 @worker_name 来路由)

    返回:
        处理结果 (含各 worker 的回复状态)
    """
    if not message.strip():
        return {"status": "error", "error": "消息不能为空"}

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _send_chat_message_async(message)
            )
        finally:
            loop.close()
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "results": [],
        }


def send_chat_message_async_bg(message: str) -> None:
    """后台异步执行 — 不阻塞调用方

    在独立线程中运行 worker 处理流程，立即返回。
    前端通过轮询获取最新消息和 worker 状态。
    """
    if not message.strip():
        return

    def _bg_run():
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_send_chat_message_async(message))
            finally:
                loop.close()
        except Exception as e:
            _add_chat_message("system", "system", f"❌ 后台处理失败: {e}", status="error")
            _add_activity("team", f"聊天室后台处理异常: {e}", {})

    import threading as _th
    _th.Thread(target=_bg_run, daemon=True).start()


async def send_chat_message(message: str) -> dict[str, Any]:
    """异步处理用户发送的聊天消息 — 直接在调用方的事件循环中运行

    自动分任务逻辑已移至 _send_chat_message_async 中。
    """
    if not message.strip():
        return {"status": "error", "error": "消息不能为空"}

    try:
        return await _send_chat_message_async(message)
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "results": [],
        }


def stop_chat_task() -> dict[str, Any]:
    """停止当前正在执行的任务

    设置取消标志，正在执行的 worker 链会在下一个检查点中断。
    已完成的结果保留，未执行的 worker 状态重置为 idle。
    """
    task_id = _init_default_task()
    _CHAT_CANCEL_FLAGS[task_id] = True
    _add_activity("team", f"用户请求停止任务: {task_id}", {"task_id": task_id})
    return {"status": "ok", "task_id": task_id, "message": "任务停止信号已发送"}


def clear_chat_history() -> dict[str, Any]:
    """清空当前任务的消息历史"""
    task_id = _init_default_task()
    with _chat_room_lock:
        task = _CHAT_TASKS.get(task_id)
        if task:
            task["messages"] = []
            for w in task.get("worker_status", {}):
                task["worker_status"][w] = "idle"
    _add_activity("team", "当前任务消息已清空", {"task_id": task_id})
    return {"status": "ok", "task_id": task_id}


# ─── SSE 流式聊天室 ───────────────────────────────────

import asyncio as _asyncio
from typing import AsyncIterator as _AsyncIterator

async def stream_chat_room_message(message: str) -> _AsyncIterator[str]:
    """SSE 流式处理聊天室消息 — 实时推送 worker 状态和消息

    返回 SSE 格式的字符串流 (data: {...}\\n\\n)
    前端使用 EventSource 或 fetch + ReadableStream 消费
    """
    import json as _json

    if not message.strip():
        yield f"data: {_json.dumps({'type': 'error', 'error': '消息不能为空'}, ensure_ascii=False)}\n\n"
        return

    # 立即推送 start 事件，让浏览器收到响应头 + 首个数据块
    yield f"data: {_json.dumps({'type': 'start'}, ensure_ascii=False)}\n\n"

    # 事件队列 — 在后台任务中执行 _send_chat_message_async，事件通过队列推送
    queue: _asyncio.Queue = _asyncio.Queue()

    def _on_event(event: dict) -> None:
        queue.put_nowait(event)

    async def _run_worker():
        try:
            await _send_chat_message_async(message, on_event=_on_event)
        except Exception as e:
            queue.put_nowait({"type": "error", "error": str(e)})
        finally:
            queue.put_nowait(None)  # 哨兵，标记结束

    # 启动后台任务
    task = _asyncio.create_task(_run_worker())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass
