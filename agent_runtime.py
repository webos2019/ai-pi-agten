"""
Pi Agent Runtime — 受控 Tasklist Agent 状态机

实现受控 Agent 链路:
  入口检测 -> read_resource -> plan_extract -> 方案就绪度评估 ->
  draft v1 -> validate -> warning 分流 -> (revise v2 -> validate) ->
  修正效果评估 -> final_answer

关键设计:
- 入口必须显式: /tasklist + @docs://versions/*.md 二者缺一不可
- 状态机: AgentState 保存草稿/校验/修正次数, 仅本轮内存
- 确定性质量门: validate_tasklist_structure 用规则检查结构完整性
- Warning 分流: 区分 blocking（阻断）和 warning（警告），仅 blocking 触发修正
- 修正效果评估: 比较 v1/v2 校验结果，选择更好的草稿输出
- 最多一次自动修正: 不会无限循环
- 最终输出: 可复制草稿 + 校验结论 + 修正效果 + 人工确认点
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from stream import (
    StreamWriter,
    StreamLifecycle,
    create_id,
    create_text_chunk,
    create_resource_start_chunk,
    create_resource_end_chunk,
    create_agent_step_start_chunk,
    create_agent_step_end_chunk,
    create_steer_applied_chunk,
)
from steer_queue import SteerQueue, SteerEntry
from deepseek import chat_completion


# ─── 资源路径 ─────────────────────────────────────────
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs", "versions")
VERSION_PLAN_URI_PATTERN = re.compile(r"^docs://versions/([^/\\]+\.md)$", re.IGNORECASE)
# 用于在文本中搜索 URI 的模式（无锚点，匹配嵌入文本中的 URI）
VERSION_PLAN_URI_SEARCH_PATTERN = re.compile(r"docs://versions/([^/\\]+\.md)", re.IGNORECASE)


def resolve_version_plan_uri(uri: str) -> tuple[str | None, str | None]:
    """将 docs://versions/xxx.md 解析为 (文件名, 文件路径)"""
    match = VERSION_PLAN_URI_PATTERN.match(uri)
    if not match:
        return None, None
    filename = match.group(1)
    filepath = os.path.join(DOCS_DIR, filename)
    if os.path.isfile(filepath):
        return filename, filepath
    return filename, None


def list_available_version_plans() -> list[dict[str, str]]:
    """列出所有可用版本方案"""
    plans = []
    if os.path.isdir(DOCS_DIR):
        for f in sorted(os.listdir(DOCS_DIR)):
            if f.endswith(".md"):
                plans.append({
                    "uri": f"docs://versions/{f}",
                    "name": f,
                })
    return plans


# ─── AgentState ────────────────────────────────────────

@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "blocking"  # "blocking"（阻断，需修正）或 "warning"（警告，可忽略）


@dataclass
class ValidationResult:
    is_valid: bool
    should_revise: bool = False  # 是否值得触发模型修正（仅 blocking issues 为 True）
    issues: list[ValidationIssue] = field(default_factory=list)
    summary: str = ""
    blocking_count: int = 0
    warning_count: int = 0


@dataclass
class AgentState:
    """Agent 本轮状态 — 仅存在于本轮请求内，不持久化"""
    run_id: str = ""
    version_plan_uri: str = ""
    version_plan_content: str = ""
    version_plan_filename: str = ""

    # planExtract 结果
    plan_extract: dict[str, Any] = field(default_factory=dict)

    # 草稿
    tasklist_draft_v1: str = ""
    tasklist_draft_v2: str = ""
    current_draft: str = ""  # 指向当前版本

    # 校验
    validation_v1: ValidationResult | None = None
    validation_v2: ValidationResult | None = None
    revision_count: int = 0  # 0 或 1
    revision_effect: str = ""  # 修正效果评估结果

    # 最终
    final_answer: str = ""
    step_index: int = 0

    # steer 历史 — 用户中途插话的转向指令文本列表
    steer_history: list[str] = field(default_factory=list)


# ─── 确定性质量门: validate_tasklist_structure ─────────

def validate_tasklist_structure(draft: str, state: AgentState) -> ValidationResult:
    """
    确定性结构校验 — 不依赖模型判断，用规则检查 tasklist 草稿结构完整性。

    校验项（severity 分流）:
    - missing_title [blocking]: 是否有标题
    - missing_plan_uri [blocking]: 是否标明来源版本方案
    - missing_steps [blocking/warning]: 0 步阻断，1-2 步警告
    - missing_checklist [blocking/warning]: 0 项阻断，1 项警告
    - missing_verification [warning]: 是否有验证内容
    """
    issues: list[ValidationIssue] = []

    if not draft or not draft.strip():
        return ValidationResult(
            is_valid=False,
            should_revise=True,
            issues=[ValidationIssue("empty_draft", "草稿为空", severity="blocking")],
            summary="草稿为空",
            blocking_count=1,
        )

    lines = draft.strip().split("\n")

    # 1. 标题检查 — 前 5 行应有 # 开头的 Markdown 标题
    has_title = any(line.strip().startswith("#") for line in lines[:5])
    if not has_title:
        issues.append(ValidationIssue("missing_title", "缺少标题（# 开头）", severity="blocking"))

    # 2. 来源版本方案检查
    has_plan_uri = bool(re.search(r"docs://versions/", draft))
    if not has_plan_uri:
        issues.append(ValidationIssue("missing_plan_uri", "未标明来源版本方案 URI", severity="blocking"))

    # 3. 主要步骤检查 — 0 步阻断，1-2 步警告
    step_items = [line for line in lines if re.match(r"^\s*([-*]|\d+\.)\s", line)]
    if len(step_items) < 3:
        sev = "blocking" if len(step_items) == 0 else "warning"
        issues.append(ValidationIssue(
            "missing_steps", f"主要步骤不足（仅 {len(step_items)} 项，需至少 3 项）", severity=sev,
        ))

    # 4. 勾选项检查 — 0 项阻断，1 项警告
    checklist_items = re.findall(r"\[[ x]\]", draft)
    if len(checklist_items) < 2:
        sev = "blocking" if len(checklist_items) == 0 else "warning"
        issues.append(ValidationIssue(
            "missing_checklist", f"勾选项不足（仅 {len(checklist_items)} 项，需至少 2 项）", severity=sev,
        ))

    # 5. 验证内容检查 — 警告（内容质量，非结构阻断）
    verification_keywords = ["验收", "验证", "测试", "确认", "verify", "test", "acceptance"]
    has_verification = any(kw in draft.lower() for kw in verification_keywords)
    if not has_verification:
        issues.append(ValidationIssue("missing_verification", "缺少验证/验收内容", severity="warning"))

    # ── Warning 分流 ──
    blocking_issues = [i for i in issues if i.severity == "blocking"]
    warning_issues = [i for i in issues if i.severity == "warning"]
    is_valid = len(blocking_issues) == 0
    should_revise = len(blocking_issues) > 0

    summary = "结构完整" if is_valid else "；".join(i.message for i in issues)

    return ValidationResult(
        is_valid=is_valid,
        should_revise=should_revise,
        issues=issues,
        summary=summary,
        blocking_count=len(blocking_issues),
        warning_count=len(warning_issues),
    )


# ─── planExtract: 版本方案结构提取 ────────────────────

def extract_plan_structure(content: str) -> dict[str, Any]:
    """
    从版本方案 Markdown 中提取结构化依据:
    version / goals / non_goals / key_changes / test_plan / deliverables
    """
    lines = content.strip().split("\n")
    result: dict[str, Any] = {
        "version": "",
        "goals": [],
        "non_goals": [],
        "key_changes": [],
        "test_plan": [],
        "deliverables": [],
    }

    # 提取版本号（从标题）
    title_match = re.search(r"v(\d+\.\d+\.\d+)", content)
    if title_match:
        result["version"] = f"v{title_match.group(1)}"

    # 按段落提取 — 按中文段名长度降序，确保 "非目标" 优先于 "目标" 匹配
    current_section = None
    section_map = sorted([
        ("非目标", "non_goals"),
        ("目标", "goals"),
        ("关键变更", "key_changes"),
        ("关键改动", "key_changes"),
        ("测试计划", "test_plan"),
        ("交付结果", "deliverables"),
        ("交付物", "deliverables"),
    ], key=lambda x: len(x[0]), reverse=True)

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#"):
            header_text = stripped.lstrip("#").strip()
            matched = False
            for cn_name, key in section_map:
                if cn_name in header_text and len(cn_name) >= len(header_text) - 2:
                    current_section = key
                    matched = True
                    break
            if not matched:
                current_section = None
            continue

        if current_section and re.match(r"^\s*([-*]|\d+\.)\s", stripped):
            item_text = re.sub(r"^\s*([-*]|\d+\.)\s+", "", stripped).strip()
            if item_text:
                result[current_section].append(item_text)

    return result


def format_plan_extract_for_prompt(plan: dict[str, Any]) -> str:
    """将 planExtract 结果格式化为模型 prompt"""
    parts = []
    if plan.get("version"):
        parts.append(f"版本: {plan['version']}")
    if plan.get("goals"):
        parts.append("目标:\n" + "\n".join(f"  - {g}" for g in plan["goals"]))
    if plan.get("non_goals"):
        parts.append("非目标:\n" + "\n".join(f"  - {g}" for g in plan["non_goals"]))
    if plan.get("key_changes"):
        parts.append("关键变更:\n" + "\n".join(f"  - {c}" for c in plan["key_changes"]))
    if plan.get("test_plan"):
        parts.append("测试计划:\n" + "\n".join(f"  - {t}" for t in plan["test_plan"]))
    if plan.get("deliverables"):
        parts.append("交付结果:\n" + "\n".join(f"  - {d}" for d in plan["deliverables"]))
    return "\n\n".join(parts) if parts else "未能提取到有效结构"


# ─── Agent Runner ──────────────────────────────────────

async def run_tasklist_agent(
    state: AgentState,
    writer: StreamWriter,
    lifecycle: StreamLifecycle,
    steer_queue: SteerQueue | None = None,
) -> None:
    """
    Agent 主流程:
    read_resource -> plan_extract -> 方案就绪度评估 ->
    draft v1 -> validate -> warning 分流 ->
    (revise v2 -> validate) -> 修正效果评估 -> final_answer

    steer 集成: 每个步骤边界检查 steer_queue，消费排队的转向指令，
    注入到后续步骤的模型 prompt (state.steer_history)。
    """
    step = 0

    # ── Step 1: read_resource ──
    _emit_step_start(lifecycle, state.run_id, step, "read_resource", "读取版本方案")
    t0 = time.time()

    filename, filepath = resolve_version_plan_uri(state.version_plan_uri)
    if not filepath:
        _emit_step_end(lifecycle, state.run_id, step, "error",
                        f"版本方案 {state.version_plan_uri} 不存在")
        lifecycle.write_chunk(create_text_chunk(
            f"❌ 版本方案 `{state.version_plan_uri}` 不存在。\n\n"
            f"可用的版本方案:\n" +
            "\n".join(f"  - `{p['uri']}`" for p in list_available_version_plans())
        ))
        return

    with open(filepath, "r", encoding="utf-8") as f:
        state.version_plan_content = f.read()
    state.version_plan_filename = filename

    lifecycle.write_chunk(create_resource_start_chunk(filename, state.version_plan_uri))
    preview = state.version_plan_content[:500]
    is_truncated = len(state.version_plan_content) > 500
    lifecycle.write_chunk(create_resource_end_chunk(
        filename, state.version_plan_uri,
        content_preview=preview,
        is_truncated=is_truncated,
        preview_chars=500 if is_truncated else None,
    ))

    _emit_step_end(lifecycle, state.run_id, step, "success",
                    f"已读取 {filename} ({len(state.version_plan_content)} 字符)",
                    duration_ms=int((time.time() - t0) * 1000))
    step += 1
    await _check_steer(lifecycle, state, steer_queue, step, "plan_extract")

    # ── Step 2: plan_extract ──
    _emit_step_start(lifecycle, state.run_id, step, "plan_extract", "提取版本方案结构")
    t0 = time.time()

    state.plan_extract = extract_plan_structure(state.version_plan_content)
    plan_text = format_plan_extract_for_prompt(state.plan_extract)

    field_count = sum(len(v) if isinstance(v, list) else (1 if v else 0) for v in state.plan_extract.values())
    _emit_step_end(lifecycle, state.run_id, step, "success",
                    f"提取到 {field_count} 个结构字段",
                    duration_ms=int((time.time() - t0) * 1000))
    step += 1
    await _check_steer(lifecycle, state, steer_queue, step, "plan_readiness")

    # ── Step 2.5: 方案就绪度评估 ──
    _emit_step_start(lifecycle, state.run_id, step, "plan_readiness", "方案就绪度评估")
    t0 = time.time()

    plan = state.plan_extract
    has_goals = bool(plan.get("goals"))
    has_key_changes = bool(plan.get("key_changes"))

    if not has_goals and not has_key_changes:
        _emit_step_end(lifecycle, state.run_id, step, "error",
                        "方案就绪度不足：未提取到目标和关键变更",
                        duration_ms=int((time.time() - t0) * 1000))
        lifecycle.write_chunk(create_text_chunk(
            "⚠️ 版本方案信息不足，未提取到「目标」和「关键变更」章节。\n\n"
            "请确保方案包含以下章节：\n"
            "- `## 目标` 或 `## 版本目标`\n"
            "- `## 关键变更` 或 `## 关键改动`\n\n"
            f"提取结果: version={plan.get('version', '无')}, "
            f"goals={len(plan.get('goals', []))} 项, "
            f"key_changes={len(plan.get('key_changes', []))} 项\n\n"
            "无法基于不完整的方案生成高质量草稿，请补充方案内容后重试。"
        ))
        return

    readiness_summary = (
        f"就绪度通过: goals={len(plan.get('goals', []))} 项, "
        f"key_changes={len(plan.get('key_changes', []))} 项"
    )
    _emit_step_end(lifecycle, state.run_id, step, "success",
                    readiness_summary,
                    duration_ms=int((time.time() - t0) * 1000))
    step += 1
    await _check_steer(lifecycle, state, steer_queue, step, "draft_tasklist")

    # ── Step 3: draft_tasklist v1 ──
    _emit_step_start(lifecycle, state.run_id, step, "draft_tasklist", "生成任务清单草稿 v1")
    t0 = time.time()

    state.tasklist_draft_v1 = await _generate_tasklist_draft(state, plan_text, is_revision=False)
    state.current_draft = state.tasklist_draft_v1

    lifecycle.write_chunk(create_text_chunk("## 任务清单草稿 v1\n\n"))
    lifecycle.write_chunk(create_text_chunk(state.tasklist_draft_v1 + "\n\n"))

    _emit_step_end(lifecycle, state.run_id, step, "success",
                    "草稿 v1 已生成",
                    duration_ms=int((time.time() - t0) * 1000))
    step += 1
    await _check_steer(lifecycle, state, steer_queue, step, "validate_tasklist")

    # ── Step 4: validate_tasklist v1（含 Warning 分流）──
    _emit_step_start(lifecycle, state.run_id, step, "validate_tasklist", "结构校验 v1（Warning 分流）")
    t0 = time.time()

    state.validation_v1 = validate_tasklist_structure(state.tasklist_draft_v1, state)

    v1_summary = state.validation_v1.summary
    if state.validation_v1.warning_count > 0 and not state.validation_v1.should_revise:
        v1_summary += f"（{state.validation_v1.warning_count} 个 warning 已忽略，不触发修正）"

    _emit_step_end(lifecycle, state.run_id, step,
                    "success" if state.validation_v1.is_valid else ("error" if state.validation_v1.should_revise else "success"),
                    v1_summary,
                    duration_ms=int((time.time() - t0) * 1000))
    step += 1
    await _check_steer(lifecycle, state, steer_queue, step, "revise_tasklist")

    # ── Step 5: revise_tasklist（仅阻断性问题才修正，warning 跳过）──
    if state.validation_v1.should_revise and state.revision_count < 1:
        _emit_step_start(lifecycle, state.run_id, step, "revise_tasklist", "自动修正草稿")
        t0 = time.time()

        state.revision_count = 1
        blocking_issues = [i for i in state.validation_v1.issues if i.severity == "blocking"]
        issues_text = "；".join(i.message for i in blocking_issues)
        state.tasklist_draft_v2 = await _generate_tasklist_draft(state, plan_text, is_revision=True, issues=issues_text)
        state.current_draft = state.tasklist_draft_v2

        lifecycle.write_chunk(create_text_chunk("## 任务清单草稿 v2（修正后）\n\n"))
        lifecycle.write_chunk(create_text_chunk(state.tasklist_draft_v2 + "\n\n"))

        _emit_step_end(lifecycle, state.run_id, step, "success",
                        "修正版 v2 已生成",
                        duration_ms=int((time.time() - t0) * 1000))
        step += 1
        await _check_steer(lifecycle, state, steer_queue, step, "validate_tasklist_v2")

        # ── Step 6: validate_tasklist v2 ──
        _emit_step_start(lifecycle, state.run_id, step, "validate_tasklist", "结构校验 v2")
        t0 = time.time()

        state.validation_v2 = validate_tasklist_structure(state.tasklist_draft_v2, state)

        _emit_step_end(lifecycle, state.run_id, step,
                        "success" if state.validation_v2.is_valid else "error",
                        state.validation_v2.summary,
                        duration_ms=int((time.time() - t0) * 1000))
        step += 1
        await _check_steer(lifecycle, state, steer_queue, step, "revision_eval")

        # ── Step 6.5: 修正效果评估 ──
        _emit_step_start(lifecycle, state.run_id, step, "revision_eval", "修正效果评估")
        t0 = time.time()

        best_draft, best_validation, effect_note = _select_best_draft(state)
        state.current_draft = best_draft
        state.revision_effect = effect_note

        if "有效" in effect_note or "通过" in effect_note:
            eval_status = "success"
        elif "无改善" in effect_note:
            eval_status = "success"  # 没变好但也没变差
        else:
            eval_status = "error"  # 越修越差，回退

        _emit_step_end(lifecycle, state.run_id, step, eval_status,
                        effect_note,
                        duration_ms=int((time.time() - t0) * 1000))
        step += 1

    # ── Step 7: final_answer ──
    await _check_steer(lifecycle, state, steer_queue, step, "final_answer")
    _emit_step_start(lifecycle, state.run_id, step, "final_answer", "生成最终输出")
    t0 = time.time()

    final = _build_final_answer(state)
    state.final_answer = final
    lifecycle.write_chunk(create_text_chunk(final))

    _emit_step_end(lifecycle, state.run_id, step, "success",
                    "最终输出已生成",
                    duration_ms=int((time.time() - t0) * 1000))


# ─── 内部辅助 ─────────────────────────────────────────

async def _check_steer(
    lifecycle: StreamLifecycle,
    state: AgentState,
    steer_queue: SteerQueue | None,
    step_index: int,
    action_type: str,
) -> list[SteerEntry]:
    """在步骤边界检查 steer 队列，消费并应用所有排队的转向指令。

    对每条 steer:
    1. 标记为已应用 (steer_queue.mark_applied)
    2. 发送 steer_applied chunk 通知前端
    3. 追加到 state.steer_history，供后续模型调用注入 prompt
    4. 发送一条可见文本提示用户 steer 已接收
    """
    if not steer_queue:
        return []

    steers = await steer_queue.drain()
    if not steers:
        return []

    for s in steers:
        steer_queue.mark_applied(s, step_index, action_type)
        lifecycle.write_chunk(create_steer_applied_chunk(
            steer_id=s.id,
            steer_text=s.text,
            applied_at_step=step_index,
            action_type=action_type,
        ))
        state.steer_history.append(s.text)
        lifecycle.write_chunk(create_text_chunk(
            f"\n> 🔄 **已接收转向指令**: {s.text}\n\n"
        ))

    return steers


def _emit_step_start(
    lifecycle: StreamLifecycle, run_id: str, step_index: int,
    action_type: str, title: str,
) -> None:
    lifecycle.write_chunk(create_agent_step_start_chunk(
        run_id=run_id,
        step_index=step_index,
        action_type=action_type,
        title=title,
    ))


def _emit_step_end(
    lifecycle: StreamLifecycle, run_id: str, step_index: int,
    status: str, summary: str | None = None,
    duration_ms: int | None = None,
) -> None:
    lifecycle.write_chunk(create_agent_step_end_chunk(
        run_id=run_id,
        step_index=step_index,
        status=status,
        summary=summary,
        duration_ms=duration_ms,
    ))


async def _generate_tasklist_draft(
    state: AgentState,
    plan_text: str,
    is_revision: bool = False,
    issues: str | None = None,
) -> str:
    """调用模型生成 tasklist 草稿

    steer 集成: 如果 state.steer_history 非空，把用户中途插话的转向指令
    注入到 user prompt，引导模型据此调整草稿内容。
    """
    system_prompt = (
        "你是一个任务清单生成助手。根据版本方案的结构化依据，生成一份可执行的任务清单（tasklist）草稿。\n\n"
        "格式要求:\n"
        "- 以 # 标题开头\n"
        "- 在标题后注明来源版本方案 URI（如 docs://versions/xxx.md）\n"
        "- 包含主要步骤（至少 3 项，用 - 或数字列表）\n"
        "- 每个步骤下有勾选项（[ ] 或 [x]，至少 2 个）\n"
        "- 包含验证/验收内容\n"
        "- 包含非目标确认、风险点和暂停确认点\n"
    )

    user_content = f"版本方案结构化依据:\n\n{plan_text}\n\n"
    user_content += f"来源版本方案: {state.version_plan_uri}\n"

    if is_revision and issues:
        user_content += (
            f"\n\n上一版草稿存在以下阻断性问题，请修正:\n{issues}\n"
            "请确保修正后的草稿通过结构校验。"
        )

    # ── steer 注入: 把用户中途插话的转向指令注入 prompt ──
    if state.steer_history:
        steer_lines = "\n".join(f"  - {s}" for s in state.steer_history)
        user_content += (
            f"\n\n⚠️ 用户中途插话的转向指令（请据此调整草稿内容和侧重点）:\n{steer_lines}\n"
            "请在草稿中体现这些调整方向，不必在输出中重复指令本身。"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    response = await chat_completion(messages=messages, tools=[])
    choice = response.choices[0]
    return choice.message.content or ""


def _select_best_draft(state: AgentState) -> tuple[str, ValidationResult, str]:
    """
    修正效果评估 — 比较 v1 和 v2 的校验结果，选择更好的草稿。

    返回: (best_draft, best_validation, effect_note)
    """
    v1 = state.validation_v1
    v2 = state.validation_v2

    if not v2:
        return state.tasklist_draft_v1, v1 or ValidationResult(is_valid=False, summary="无校验"), ""

    # v2 通过校验 → 直接用 v2
    if v2.is_valid:
        return state.tasklist_draft_v2, v2, "修正有效：v2 通过校验"

    v1_blocking = v1.blocking_count if v1 else 99
    v2_blocking = v2.blocking_count if v2 else 99

    # v2 阻断问题更少 → 用 v2
    if v2_blocking < v1_blocking:
        return state.tasklist_draft_v2, v2, f"修正部分有效：阻断问题 {v1_blocking} → {v2_blocking}"

    # v2 阻断问题没变 → 用 v2（至少 warning 可能更少）
    if v2_blocking == v1_blocking:
        v1_warnings = v1.warning_count if v1 else 99
        v2_warnings = v2.warning_count if v2 else 99
        if v2_warnings < v1_warnings:
            return state.tasklist_draft_v2, v2, f"修正有限改善：阻断问题不变，warning {v1_warnings} → {v2_warnings}"
        return state.tasklist_draft_v2, v2, "修正无明显改善：阻断问题数未变"

    # v2 阻断问题更多 → 越修越差，回退 v1
    return state.tasklist_draft_v1, v1 or ValidationResult(is_valid=False), \
        f"修正无效：v2 阻断问题更多（{v1_blocking} → {v2_blocking}），回退 v1"


def _build_final_answer(state: AgentState) -> str:
    """构建最终输出: 最优草稿 + 校验结论 + 修正效果 + 人工确认点"""
    best_draft, best_validation, effect_note = _select_best_draft(state)
    state.current_draft = best_draft

    revision_note = ""
    if state.revision_count > 0:
        revision_note = f"\n- 自动修正次数: {state.revision_count}"

    parts = [
        "---\n",
        "## 📋 最终输出\n",
        f"**来源版本方案**: `{state.version_plan_uri}`\n",
        f"**结构校验状态**: {'✓ 通过' if (best_validation and best_validation.is_valid) else '✗ 未通过'}",
        revision_note + "\n",
    ]

    if effect_note:
        parts.append(f"**修正效果**: {effect_note}\n")

    parts.extend([
        "\n### 可复制 Tasklist 草稿\n",
        "```markdown\n",
        best_draft,
        "\n```\n",
    ])

    if best_validation and best_validation.issues:
        parts.append("\n### 校验详情\n")
        if best_validation.is_valid:
            parts.append("结构完整，无阻断性问题。\n")
        else:
            parts.append(f"阻断性问题（{best_validation.blocking_count} 项）:\n")
            for issue in best_validation.issues:
                if issue.severity == "blocking":
                    parts.append(f"- 🔴 `{issue.code}`: {issue.message}\n")

        if best_validation.warning_count > 0:
            parts.append(f"\n警告（{best_validation.warning_count} 项，已忽略不触发修正）:\n")
            for issue in best_validation.issues:
                if issue.severity == "warning":
                    parts.append(f"- 🟡 `{issue.code}`: {issue.message}\n")

    parts.append("\n### ⚠️ 人工确认点\n")
    parts.append("- 以上草稿由 Agent 生成，**未自动写入任何文件**\n")
    parts.append("- 请人工 review 后再决定是否落地\n")
    parts.append(f"- 本轮修正次数: {state.revision_count} / 1（上限）\n")
    if state.revision_effect:
        parts.append(f"- 修正效果: {state.revision_effect}\n")

    return "".join(parts)
