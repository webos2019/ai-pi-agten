"""
Pi Agent 完整测试套件

测试覆盖:
  单元测试 (不需服务器):
    1. validate_tasklist_structure — 确定性质量门 (含 Warning 分流)
    2. extract_plan_structure + format_plan_extract_for_prompt (planExtract)
    3. resolve_version_plan_uri + list_available_version_plans (资源解析)
    4. AgentState 初始化 + 状态流转
    5. _select_best_draft — 修正效果评估 (v1/v2 最优选择)
    6. ThreadState — 追加/去重/截断/压缩判断/上下文构建/hydration
    7. ConversationRegistry — 创建/切换/重命名/删除/裁剪
    8. TextCollectingWriter — 文本收集 + 错误追踪

  API 端到端测试 (需服务器运行):
    9.  /tasklist 无版本方案引用 → 应返回提示
    10. /tasklist 引用不存在的版本方案 → 应报错并列出可用方案
    11. 完整 Agent 链路 — v0.1.0 (7+ 步)
    12. 完整 Agent 链路 — v0.2.0
    13. 无 /tasklist 命令 → 不应触发 Agent

运行方式:
  python test_agent.py            # 全部测试
  python test_agent.py --unit    # 仅单元测试 (不需服务器)
  python test_agent.py --api     # 仅 API 端到端测试
"""

import json
import sys
import os
import asyncio
import urllib.request

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── 测试结果统计 ───────────────────────────────────────

passed = 0
failed = 0
skipped = 0


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"  [PASS] {name}" + (f" -- {detail}" if detail else ""))


def fail(name, detail=""):
    global failed
    failed += 1
    print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))


def skip(name, reason=""):
    global skipped
    skipped += 1
    print(f"  [SKIP] {name}" + (f" -- {reason}" if reason else ""))


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ════════════════════════════════════════════════════════
#  单元测试 (不需要服务器)
# ════════════════════════════════════════════════════════

def test_validate_tasklist_structure():
    """测试 1: 确定性质量门 validate_tasklist_structure (含 Warning 分流)"""
    section("单元测试 1: validate_tasklist_structure")
    from agent_runtime import validate_tasklist_structure, AgentState

    state = AgentState(version_plan_uri="docs://versions/demo.md")

    # 1a: 完整草稿 → 应通过
    valid_draft = """# Tasklist v0.1.0

来源: docs://versions/v0.1.0.md

## 主要步骤

- [ ] 步骤一: 实现入口检测
- [ ] 步骤二: 实现状态机
- [ ] 步骤三: 实现质量门
- [ ] 步骤四: 实现最终输出

## 验证

- [ ] 验收: 入口检测正确命中
- [ ] 验收: 状态机完整流转
"""
    result = validate_tasklist_structure(valid_draft, state)
    if result.is_valid:
        ok("完整草稿通过校验", f"summary={result.summary}")
    else:
        fail("完整草稿应通过校验", result.summary)

    # 1b: 空草稿 → 应失败 (blocking)
    result_empty = validate_tasklist_structure("", state)
    if not result_empty.is_valid and result_empty.issues[0].code == "empty_draft":
        ok("空草稿被拒绝", f"issue={result_empty.issues[0].code}, should_revise={result_empty.should_revise}")
    else:
        fail("空草稿应被拒绝")

    # 1c: 缺少标题 → blocking
    no_title = """来源: docs://versions/test.md

- [ ] 步骤一
- [ ] 步骤二
- [ ] 步骤三

验证: 测试通过
"""
    result_no_title = validate_tasklist_structure(no_title, state)
    codes = [i.code for i in result_no_title.issues]
    if "missing_title" in codes and result_no_title.should_revise:
        ok("缺少标题被检测到 (blocking)", f"should_revise={result_no_title.should_revise}")
    else:
        fail("应检测到缺少标题 (blocking)", f"issues={codes}")

    # 1d: 缺少来源 URI → blocking
    no_uri = """# Tasklist

- [ ] 步骤一
- [ ] 步骤二
- [ ] 步骤三

验证: 测试通过
"""
    result_no_uri = validate_tasklist_structure(no_uri, state)
    codes = [i.code for i in result_no_uri.issues]
    if "missing_plan_uri" in codes and result_no_uri.should_revise:
        ok("缺少来源 URI 被检测到 (blocking)")
    else:
        fail("应检测到缺少来源 URI", f"issues={codes}")

    # 1e: 步骤为 0 → blocking
    zero_steps = """# Tasklist
来源: docs://versions/test.md

验证: 测试通过
"""
    result_zero = validate_tasklist_structure(zero_steps, state)
    codes = [i.code for i in result_zero.issues]
    zero_step_issue = [i for i in result_zero.issues if i.code == "missing_steps"]
    if zero_step_issue and zero_step_issue[0].severity == "blocking":
        ok("0 步骤被检测为 blocking")
    else:
        fail("0 步骤应为 blocking", f"issues={codes}")

    # 1f: 步骤 1-2 项 → warning (不触发修正)
    few_steps = """# Tasklist
来源: docs://versions/test.md

- [ ] 只有一个步骤

验证: 测试通过
"""
    result_few = validate_tasklist_structure(few_steps, state)
    few_step_issue = [i for i in result_few.issues if i.code == "missing_steps"]
    if few_step_issue and few_step_issue[0].severity == "warning":
        ok("1-2 步骤被检测为 warning (不触发修正)")
    else:
        fail("1-2 步骤应为 warning", f"issues={[i.code for i in result_few.issues]}")

    # 1g: 缺少勾选项 0 → blocking
    no_checklist = """# Tasklist
来源: docs://versions/test.md

- 步骤一
- 步骤二
- 步骤三

验证: 测试通过
"""
    result_no_check = validate_tasklist_structure(no_checklist, state)
    check_issue = [i for i in result_no_check.issues if i.code == "missing_checklist"]
    if check_issue and check_issue[0].severity == "blocking":
        ok("0 勾选项被检测为 blocking")
    else:
        fail("0 勾选项应为 blocking", f"issues={[i.code for i in result_no_check.issues]}")

    # 1h: 缺少验证内容 → warning (不触发修正)
    no_verify = """# Tasklist
来源: docs://versions/demo.md

- [ ] 步骤一
- [ ] 步骤二
- [ ] 步骤三
"""
    result_no_verify = validate_tasklist_structure(no_verify, state)
    verify_issue = [i for i in result_no_verify.issues if i.code == "missing_verification"]
    if verify_issue and verify_issue[0].severity == "warning":
        ok("缺少验证内容被检测为 warning (不触发修正)")
    else:
        fail("缺少验证内容应为 warning", f"issues={[i.code for i in result_no_verify.issues]}")

    # 1i: Warning 分流 — 只有 warning 时 is_valid=True, should_revise=False
    only_warning = """# Tasklist
来源: docs://versions/demo.md

- [ ] 步骤一
- [ ] 步骤二
- [ ] 步骤三
"""
    result_warning = validate_tasklist_structure(only_warning, state)
    if result_warning.is_valid and not result_warning.should_revise and result_warning.warning_count > 0:
        ok("Warning 分流: 只有 warning 时通过且不修正",
           f"blocking={result_warning.blocking_count}, warning={result_warning.warning_count}")
    else:
        fail("Warning 分流逻辑错误",
             f"is_valid={result_warning.is_valid}, should_revise={result_warning.should_revise}")

    # 1j: blocking + warning 混合 → should_revise=True
    mixed = """步骤一
步骤二
"""
    result_mixed = validate_tasklist_structure(mixed, state)
    if not result_mixed.is_valid and result_mixed.should_revise and result_mixed.blocking_count > 0:
        ok("混合问题: blocking 触发修正", f"blocking={result_mixed.blocking_count}, warning={result_mixed.warning_count}")
    else:
        fail("混合问题应触发修正", f"blocking={result_mixed.blocking_count}")


def test_extract_plan_structure():
    """测试 2: planExtract 版本方案结构提取"""
    section("单元测试 2: extract_plan_structure (planExtract)")
    from agent_runtime import extract_plan_structure, format_plan_extract_for_prompt

    # 使用真实版本方案文件
    plan_path = os.path.join(os.path.dirname(__file__), "docs", "versions", "v0.1.0-controlled-tasklist-agent.md")
    if not os.path.isfile(plan_path):
        skip("planExtract 测试", "版本方案文件不存在")
        return

    with open(plan_path, "r", encoding="utf-8") as f:
        content = f.read()

    plan = extract_plan_structure(content)

    # 2a: 版本号提取
    if plan.get("version"):
        ok(f"版本号提取: {plan['version']}")
    else:
        fail("应提取到版本号")

    # 2b: 目标提取
    if len(plan.get("goals", [])) > 0:
        ok(f"目标提取: {len(plan['goals'])} 项")
    else:
        fail("应提取到目标列表")

    # 2c: 非目标提取
    if len(plan.get("non_goals", [])) > 0:
        ok(f"非目标提取: {len(plan['non_goals'])} 项")
    else:
        fail("应提取到非目标列表")

    # 2d: 关键变更提取
    if len(plan.get("key_changes", [])) > 0:
        ok(f"关键变更提取: {len(plan['key_changes'])} 项")
    else:
        fail("应提取到关键变更列表")

    # 2e: 测试计划提取
    if len(plan.get("test_plan", [])) > 0:
        ok(f"测试计划提取: {len(plan['test_plan'])} 项")
    else:
        fail("应提取到测试计划列表")

    # 2f: 交付结果提取
    if len(plan.get("deliverables", [])) > 0:
        ok(f"交付结果提取: {len(plan['deliverables'])} 项")
    else:
        fail("应提取到交付结果列表")

    # 2g: 格式化输出
    formatted = format_plan_extract_for_prompt(plan)
    if "版本" in formatted and "目标" in formatted:
        ok("格式化输出包含关键字段", f"{len(formatted)} 字符")
    else:
        fail("格式化输出应包含关键字段", formatted[:100])

    # 2h: 空内容
    empty_plan = extract_plan_structure("")
    if not empty_plan.get("version") and not empty_plan.get("goals"):
        ok("空内容正确返回空结构")
    else:
        fail("空内容应返回空结构")

    # 2i: 非目标优先于目标匹配 (中文段名长度排序)
    tricky_content = """# v0.1.0 测试

## 非目标
- 不做 A
- 不做 B

## 目标
- 做成 C
- 做成 D
"""
    tricky_plan = extract_plan_structure(tricky_content)
    if len(tricky_plan.get("non_goals", [])) == 2 and len(tricky_plan.get("goals", [])) == 2:
        ok("非目标与目标正确分流 (中文段名长度排序)")
    else:
        fail("非目标/目标分流错误",
             f"non_goals={len(tricky_plan.get('non_goals', []))}, goals={len(tricky_plan.get('goals', []))}")


def test_resolve_version_plan_uri():
    """测试 3: 资源 URI 解析"""
    section("单元测试 3: resolve_version_plan_uri + list_available_version_plans")

    from agent_runtime import resolve_version_plan_uri, list_available_version_plans

    # 3a: 有效 URI
    filename, filepath = resolve_version_plan_uri("docs://versions/v0.1.0-controlled-tasklist-agent.md")
    if filename and filepath and os.path.isfile(filepath):
        ok(f"有效 URI 解析成功: {filename}")
    else:
        fail("有效 URI 应解析成功", f"filename={filename}, filepath={filepath}")

    # 3b: 无效 URI (不存在的文件)
    filename2, filepath2 = resolve_version_plan_uri("docs://versions/nonexistent.md")
    if filename2 and not filepath2:
        ok("不存在的文件返回文件名但路径为 None")
    else:
        fail("不存在的文件应返回 (filename, None)", f"filename={filename2}, filepath={filepath2}")

    # 3c: 格式错误的 URI
    filename3, filepath3 = resolve_version_plan_uri("http://example.com/test.md")
    if not filename3 and not filepath3:
        ok("格式错误的 URI 返回 (None, None)")
    else:
        fail("格式错误的 URI 应返回 (None, None)")

    # 3d: 列出可用方案
    plans = list_available_version_plans()
    if len(plans) >= 2:
        ok(f"列出可用方案: {len(plans)} 个")
        for p in plans:
            print(f"       - {p['uri']}")
    else:
        fail("应至少列出 2 个可用方案", f"实际 {len(plans)} 个")

    # 3e: URI 大小写不敏感
    filename4, _ = resolve_version_plan_uri("docs://versions/V0.1.0-CONTROLLED-TASKLIST-AGENT.MD")
    if filename4:
        ok("URI 大小写不敏感匹配")
    else:
        fail("URI 应大小写不敏感")


def test_agent_state():
    """测试 4: AgentState 初始化 + 状态流转"""
    section("单元测试 4: AgentState 状态管理")
    from agent_runtime import AgentState, ValidationResult, ValidationIssue

    # 4a: 初始化默认值
    state = AgentState(
        run_id="test-run-001",
        version_plan_uri="docs://versions/v0.1.0-controlled-tasklist-agent.md",
    )
    if (state.revision_count == 0
            and not state.tasklist_draft_v1
            and not state.validation_v1
            and not state.plan_extract
            and state.step_index == 0):
        ok("AgentState 初始化默认值正确", f"revision_count={state.revision_count}")
    else:
        fail("AgentState 初始化默认值不正确")

    # 4b: 模拟状态流转 — 草稿 v1
    state.tasklist_draft_v1 = (
        "# Draft v1\n来源: docs://versions/v0.1.0.md\n"
        "- [ ] step1\n- [ ] step2\n- [ ] step3\n验证: ok"
    )
    state.current_draft = state.tasklist_draft_v1
    if state.current_draft == state.tasklist_draft_v1:
        ok("草稿 v1 设置后 current_draft 指向 v1")
    else:
        fail("current_draft 应指向 v1")

    # 4c: 模拟状态流转 — 校验 v1 未通过 (blocking)
    state.validation_v1 = ValidationResult(
        is_valid=False,
        should_revise=True,
        issues=[ValidationIssue("missing_checklist", "勾选项不足", severity="blocking")],
        summary="勾选项不足",
        blocking_count=1,
    )
    if not state.validation_v1.is_valid and state.revision_count == 0:
        ok("校验 v1 未通过, revision_count=0 (允许修正)")
    else:
        fail("校验 v1 未通过时应 revision_count=0")

    # 4d: 模拟状态流转 — 修正后
    state.revision_count = 1
    state.tasklist_draft_v2 = (
        "# Draft v2\n来源: docs://versions/v0.1.0.md\n"
        "- [ ] step1\n- [ ] step2\n- [ ] step3\n验证: ok"
    )
    state.current_draft = state.tasklist_draft_v2
    state.validation_v2 = ValidationResult(is_valid=True, summary="结构完整")

    if state.revision_count == 1 and state.current_draft == state.tasklist_draft_v2:
        ok("修正后 revision_count=1, current_draft 指向 v2")
    else:
        fail("修正后状态不正确")

    # 4e: 最终输出选择正确的草稿和校验结果
    final_validation = state.validation_v2 or state.validation_v1
    final_draft = state.current_draft or state.tasklist_draft_v1
    if final_validation == state.validation_v2 and final_draft == state.tasklist_draft_v2:
        ok("最终输出正确选择 v2 草稿和 v2 校验")
    else:
        fail("最终输出应选择 v2")

    # 4f: 修正上限 — revision_count 不应超过 1
    if state.revision_count <= 1:
        ok("修正次数不超过上限 (1/1)")
    else:
        fail("修正次数不应超过上限")

    # 4g: plan_extract 字段
    state.plan_extract = {"version": "v0.1.0", "goals": ["goal1"], "key_changes": ["change1"]}
    if state.plan_extract.get("version") == "v0.1.0":
        ok("plan_extract 字段可正确存取")
    else:
        fail("plan_extract 字段存取异常")

    # 4h: revision_effect 字段
    state.revision_effect = "修正有效：v2 通过校验"
    if "有效" in state.revision_effect:
        ok("revision_effect 字段可正确存取")
    else:
        fail("revision_effect 字段存取异常")


def test_select_best_draft():
    """测试 5: _select_best_draft 修正效果评估"""
    section("单元测试 5: _select_best_draft (修正效果评估)")
    from agent_runtime import (
        AgentState, ValidationResult, ValidationIssue, _select_best_draft,
    )

    # 5a: v2 通过校验 → 选 v2
    state_a = AgentState()
    state_a.tasklist_draft_v1 = "draft v1"
    state_a.validation_v1 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[ValidationIssue("missing_title", "缺标题", severity="blocking")],
        summary="缺标题", blocking_count=1,
    )
    state_a.tasklist_draft_v2 = "draft v2"
    state_a.validation_v2 = ValidationResult(is_valid=True, summary="结构完整")
    best_draft, best_val, note = _select_best_draft(state_a)
    if best_draft == "draft v2" and best_val.is_valid and "有效" in note:
        ok("v2 通过校验 → 选 v2", note)
    else:
        fail("v2 通过时应选 v2", f"note={note}")

    # 5b: v2 阻断更少 → 选 v2
    state_b = AgentState()
    state_b.tasklist_draft_v1 = "draft v1"
    state_b.validation_v1 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[
            ValidationIssue("a", "issue a", severity="blocking"),
            ValidationIssue("b", "issue b", severity="blocking"),
            ValidationIssue("c", "issue c", severity="blocking"),
        ],
        summary="3 blocking", blocking_count=3,
    )
    state_b.tasklist_draft_v2 = "draft v2"
    state_b.validation_v2 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[ValidationIssue("a", "issue a", severity="blocking")],
        summary="1 blocking", blocking_count=1,
    )
    best_draft_b, _, note_b = _select_best_draft(state_b)
    if best_draft_b == "draft v2" and "部分有效" in note_b:
        ok("v2 阻断更少 → 选 v2", note_b)
    else:
        fail("v2 阻断更少应选 v2", f"note={note_b}")

    # 5c: v2 阻断数不变但 warning 更少 → 选 v2
    state_c = AgentState()
    state_c.tasklist_draft_v1 = "draft v1"
    state_c.validation_v1 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[
            ValidationIssue("a", "blocking", severity="blocking"),
            ValidationIssue("w1", "warning1", severity="warning"),
            ValidationIssue("w2", "warning2", severity="warning"),
        ],
        summary="1 blocking 2 warning", blocking_count=1, warning_count=2,
    )
    state_c.tasklist_draft_v2 = "draft v2"
    state_c.validation_v2 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[
            ValidationIssue("a", "blocking", severity="blocking"),
            ValidationIssue("w1", "warning1", severity="warning"),
        ],
        summary="1 blocking 1 warning", blocking_count=1, warning_count=1,
    )
    best_draft_c, _, note_c = _select_best_draft(state_c)
    if best_draft_c == "draft v2" and "有限改善" in note_c:
        ok("v2 warning 更少 → 选 v2", note_c)
    else:
        fail("v2 warning 更少应选 v2", f"note={note_c}")

    # 5d: v2 阻断数不变 warning 也不变 → 选 v2 (无改善)
    state_d = AgentState()
    state_d.tasklist_draft_v1 = "draft v1"
    state_d.validation_v1 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[ValidationIssue("a", "blocking", severity="blocking")],
        summary="1 blocking", blocking_count=1, warning_count=0,
    )
    state_d.tasklist_draft_v2 = "draft v2"
    state_d.validation_v2 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[ValidationIssue("a", "blocking", severity="blocking")],
        summary="1 blocking", blocking_count=1, warning_count=0,
    )
    best_draft_d, _, note_d = _select_best_draft(state_d)
    if best_draft_d == "draft v2" and ("无明显改善" in note_d or "未变" in note_d):
        ok("v2 无改善 → 仍选 v2", note_d)
    else:
        fail("v2 无改善应仍选 v2", f"note={note_d}")

    # 5e: v2 阻断更多 → 越修越差，回退 v1
    state_e = AgentState()
    state_e.tasklist_draft_v1 = "draft v1"
    state_e.validation_v1 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[ValidationIssue("a", "blocking", severity="blocking")],
        summary="1 blocking", blocking_count=1,
    )
    state_e.tasklist_draft_v2 = "draft v2"
    state_e.validation_v2 = ValidationResult(
        is_valid=False, should_revise=True,
        issues=[
            ValidationIssue("a", "blocking", severity="blocking"),
            ValidationIssue("b", "blocking", severity="blocking"),
            ValidationIssue("c", "blocking", severity="blocking"),
        ],
        summary="3 blocking", blocking_count=3,
    )
    best_draft_e, _, note_e = _select_best_draft(state_e)
    if best_draft_e == "draft v1" and "无效" in note_e and "回退" in note_e:
        ok("v2 越修越差 → 回退 v1", note_e)
    else:
        fail("v2 越修越差应回退 v1", f"note={note_e}")

    # 5f: 无 v2 → 直接返回 v1
    state_f = AgentState()
    state_f.tasklist_draft_v1 = "draft v1"
    state_f.validation_v1 = ValidationResult(is_valid=True, summary="结构完整")
    best_draft_f, _, note_f = _select_best_draft(state_f)
    if best_draft_f == "draft v1" and note_f == "":
        ok("无 v2 → 直接返回 v1")
    else:
        fail("无 v2 应返回 v1", f"note={note_f}")


def test_thread_state():
    """测试 6: ThreadState 短期记忆"""
    section("单元测试 6: ThreadState")
    from thread_state import (
        ThreadState, ThreadMessage, MAX_RECENT_MESSAGES,
        MAX_ASSISTANT_TEXT_CHARS, MAX_PINNED_DECISIONS,
    )
    from stream import create_id
    import time as _time

    # 6a: 追加消息
    state = ThreadState(thread_id="test-thread-001")
    added = state.append("user", "你好")
    if added and len(state.messages) == 1:
        ok("追加用户消息成功")
    else:
        fail("应成功追加用户消息")

    added2 = state.append("assistant", "你好！有什么可以帮你的？")
    if added2 and len(state.messages) == 2:
        ok("追加助手消息成功")
    else:
        fail("应成功追加助手消息")

    # 6b: 去重 — 同角色同内容不重复写入
    added_dup = state.append("assistant", "你好！有什么可以帮你的？")
    if not added_dup and len(state.messages) == 2:
        ok("去重: 同角色同内容不重复写入")
    else:
        fail("应去重")

    # 6c: 空文本不写入
    added_empty = state.append("user", "")
    if not added_empty and len(state.messages) == 2:
        ok("空文本不写入")
    else:
        fail("空文本不应写入")

    added_whitespace = state.append("user", "   ")
    if not added_whitespace and len(state.messages) == 2:
        ok("纯空白文本不写入")
    else:
        fail("纯空白文本不应写入")

    # 6d: 助手超长文本截断
    long_text = "A" * (MAX_ASSISTANT_TEXT_CHARS + 1000)
    state.append("assistant", long_text)
    last_msg = state.messages[-1]
    if len(last_msg.text) < len(long_text) and "[...内容已截断]" in last_msg.text:
        ok("助手超长文本被截断", f"原 {len(long_text)} → 截断后 {len(last_msg.text)}")
    else:
        fail("助手超长文本应被截断", f"len={len(last_msg.text)}")

    # 6e: should_compact
    state2 = ThreadState(thread_id="test-thread-002")
    for i in range(MAX_RECENT_MESSAGES):
        state2.append("user", f"消息 {i}")
    if not state2.should_compact():
        ok(f"未超上限时不需压缩 ({MAX_RECENT_MESSAGES} 条)")
    else:
        fail(f"{MAX_RECENT_MESSAGES} 条不应触发压缩")

    state2.append("user", f"消息 {MAX_RECENT_MESSAGES}")
    if state2.should_compact():
        ok(f"超过上限时触发压缩 ({MAX_RECENT_MESSAGES + 1} 条)")
    else:
        fail(f"{MAX_RECENT_MESSAGES + 1} 条应触发压缩")

    # 6f: build_model_context
    state3 = ThreadState(thread_id="test-thread-003")
    state3.summary = "这是早期对话摘要"
    state3.pinned_decisions = ["决策一", "决策二"]
    state3.append("user", "最新问题")
    state3.append("assistant", "最新回答")
    ctx = state3.build_model_context()
    # 应包含: summary system + pinned system + 2 条消息 = 4 条
    if len(ctx) == 4 and ctx[0]["role"] == "system" and "对话背景摘要" in ctx[0]["content"]:
        ok("build_model_context 包含 summary + pinned + recent", f"{len(ctx)} 条")
    else:
        fail("build_model_context 结构不正确", f"{len(ctx)} 条")

    if "关键决策" in ctx[1]["content"]:
        ok("pinned_decisions 注入为 system 消息")
    else:
        fail("pinned_decisions 应注入为 system 消息")

    # 6g: to_hydration_dto
    dto = state3.to_hydration_dto()
    if (dto["threadId"] == "test-thread-003"
            and len(dto["messages"]) == 2
            and dto["summary"] == "这是早期对话摘要"
            and dto["pinnedDecisions"] == ["决策一", "决策二"]
            and dto["restored"] is True):
        ok("to_hydration_dto 返回正确结构")
    else:
        fail("to_hydration_dto 结构不正确", str(dto)[:100])

    # 6h: 空 ThreadState hydration
    empty_state = ThreadState(thread_id="empty")
    empty_dto = empty_state.to_hydration_dto()
    if empty_dto["restored"] is False and len(empty_dto["messages"]) == 0:
        ok("空 ThreadState hydration restored=False")
    else:
        fail("空 ThreadState hydration 应 restored=False")


def test_conversation_registry():
    """测试 7: ConversationRegistry 多会话管理"""
    section("单元测试 7: ConversationRegistry")
    from thread_state import (
        ConversationRegistry, MAX_CONVERSATIONS, TITLE_MAX_CHARS,
    )

    registry = ConversationRegistry(session_id="test-session-001")

    # 7a: 创建会话
    conv1 = registry.create("第一个会话")
    conv2 = registry.create("第二个会话")
    if len(registry.conversations) == 2 and conv1.title == "第一个会话":
        ok("创建会话成功", f"{len(registry.conversations)} 个")
    else:
        fail("应创建 2 个会话")

    # 7b: 获取会话
    found = registry.get(conv1.conversation_id)
    if found and found.conversation_id == conv1.conversation_id:
        ok("获取会话成功")
    else:
        fail("应能获取会话")

    not_found = registry.get("nonexistent-id")
    if not not_found:
        ok("获取不存在的会话返回 None")
    else:
        fail("不存在的会话应返回 None")

    # 7c: 切换选中
    if registry.select(conv2.conversation_id):
        ok("切换选中会话成功")
    else:
        fail("应能切换选中会话")

    if registry.selected_conversation_id == conv2.conversation_id:
        ok("selected_conversation_id 正确更新")
    else:
        fail("selected_conversation_id 应更新")

    # 7d: 切换不存在的会话
    if not registry.select("nonexistent-id"):
        ok("切换不存在的会话返回 False")
    else:
        fail("切换不存在的会话应返回 False")

    # 7e: 重命名
    long_title = "A" * (TITLE_MAX_CHARS + 100)
    if registry.rename(conv1.conversation_id, long_title):
        if len(conv1.title) == TITLE_MAX_CHARS:
            ok("重命名并截断超长标题", f"截断到 {TITLE_MAX_CHARS} 字符")
        else:
            fail("重命名应截断超长标题", f"len={len(conv1.title)}")
    else:
        fail("应能重命名")

    # 7f: touch
    old_time = conv1.last_active_at
    import time as _time
    _time.sleep(0.01)
    registry.touch(conv1.conversation_id)
    if conv1.last_active_at > old_time and conv1.has_messages:
        ok("touch 更新活跃时间 + has_messages=True")
    else:
        fail("touch 应更新活跃时间")

    # 7g: 删除
    deleted_thread_id = registry.delete(conv1.conversation_id)
    if deleted_thread_id and len(registry.conversations) == 1:
        ok("删除会话成功", f"返回 thread_id={deleted_thread_id[:8]}...")
    else:
        fail("应删除会话")

    # 7h: 删除后选中切换
    registry.select(conv2.conversation_id)
    registry.delete(conv2.conversation_id)
    if len(registry.conversations) == 0:
        ok("删除最后一个会话后列表为空")
    else:
        fail("删除后应无会话")

    # 7i: 裁剪上限
    registry2 = ConversationRegistry(session_id="test-session-002")
    created = []
    for i in range(MAX_CONVERSATIONS + 3):
        created.append(registry2.create(f"会话 {i}"))
    if len(registry2.conversations) == MAX_CONVERSATIONS:
        ok(f"会话上限裁剪: 保留 {MAX_CONVERSATIONS} 个")
    else:
        fail("应裁剪到上限", f"实际 {len(registry2.conversations)} 个")

    # 7j: list_conversations 按活跃时间倒序
    convs = registry2.list_conversations()
    is_sorted = all(
        convs[i].last_active_at >= convs[i + 1].last_active_at
        for i in range(len(convs) - 1)
    )
    if is_sorted:
        ok("list_conversations 按活跃时间倒序")
    else:
        fail("list_conversations 应按活跃时间倒序")

    # 7k: to_dto
    dto = registry2.to_dto()
    if (dto["sessionId"] == "test-session-002"
            and "selectedConversationId" in dto
            and len(dto["conversations"]) == MAX_CONVERSATIONS):
        ok("to_dto 返回正确结构")
    else:
        fail("to_dto 结构不正确")


def test_text_collecting_writer():
    """测试 8: TextCollectingWriter 文本收集 + 错误追踪"""
    section("单元测试 8: TextCollectingWriter")
    from thread_state import TextCollectingWriter
    from stream import StreamWriter, create_text_chunk, create_error_chunk, create_done_chunk

    # 8a: 收集 text chunk
    inner = StreamWriter()
    wrapper = TextCollectingWriter(inner)
    wrapper.write_chunk(create_text_chunk("Hello "))
    wrapper.write_chunk(create_text_chunk("World"))
    collected = wrapper.get_collected_text()
    if collected == "Hello World":
        ok("收集 text chunk 成功", f"text='{collected}'")
    else:
        fail("应收集 text chunk", f"collected='{collected}'")

    # 8b: 非文本 chunk 不影响收集
    wrapper.write_chunk(create_done_chunk())
    if wrapper.get_collected_text() == "Hello World":
        ok("非文本 chunk 不影响收集")
    else:
        fail("非文本 chunk 不应影响收集")

    # 8c: 错误追踪
    wrapper.write_chunk(create_error_chunk("出错了"))
    if wrapper.has_error():
        ok("错误被追踪到")
    else:
        fail("应追踪到错误")

    # 8d: 无错误时 has_error=False
    inner2 = StreamWriter()
    wrapper2 = TextCollectingWriter(inner2)
    wrapper2.write_chunk(create_text_chunk("正常文本"))
    if not wrapper2.has_error():
        ok("无错误时 has_error=False")
    else:
        fail("无错误时 has_error 应为 False")

    # 8e: 透传 chunk 到 inner writer
    if len(inner.get_chunks()) == 4:
        ok("chunk 透传到 inner writer", f"{len(inner.get_chunks())} 个")
    else:
        fail("应透传 chunk 到 inner writer", f"{len(inner.get_chunks())} 个")

    # 8f: 空 content 不收集
    inner3 = StreamWriter()
    wrapper3 = TextCollectingWriter(inner3)
    wrapper3.write_chunk(create_text_chunk(""))
    if wrapper3.get_collected_text() == "":
        ok("空 content 不收集")
    else:
        fail("空 content 不应收集")


# ════════════════════════════════════════════════════════
#  API 端到端测试
# ════════════════════════════════════════════════════════

def make_request(body_dict, timeout=60):
    """发送 API 请求, 返回 NDJSON 行列表"""
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    data = r.read().decode("utf-8")
    lines = data.strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


def is_server_running():
    """检查服务器是否运行"""
    try:
        urllib.request.urlopen("http://localhost:8000/api/health", timeout=5)
        return True
    except Exception:
        return False


def test_missing_version_plan():
    """测试 9: /tasklist 无版本方案引用 → 应返回提示"""
    section("API 测试 9: 缺少版本方案引用")
    if not is_server_running():
        skip("服务器未运行")
        return

    chunks = make_request({
        "messages": [{
            "role": "user",
            "content": "/tasklist",
            "structured": {
                "rawText": "/tasklist",
                "segments": [{"type": "text", "content": "/tasklist"}],
                "chips": [],
            }
        }],
        "skill": "utility-skill",
    }, timeout=15)

    text_chunks = [c for c in chunks if c.get("type") == "text"]
    if text_chunks:
        content = text_chunks[0].get("content", "")
        if "docs://versions/" in content or "版本方案" in content:
            ok("返回版本方案引用提示", content[:80])
        else:
            fail("应返回版本方案引用提示", content[:80])
    else:
        fail("应有 text chunk")

    done_chunks = [c for c in chunks if c.get("type") == "done"]
    if done_chunks:
        ok("流正常结束 (done)")
    else:
        fail("应有 done chunk")


def test_nonexistent_version_plan():
    """测试 10: 引用不存在的版本方案 → 应报错"""
    section("API 测试 10: 引用不存在的版本方案")
    if not is_server_running():
        skip("服务器未运行")
        return

    chunks = make_request({
        "messages": [{
            "role": "user",
            "content": "/tasklist @docs://versions/nonexistent.md",
            "structured": {
                "rawText": "/tasklist",
                "segments": [
                    {"type": "chip", "chipType": "skill", "label": "tasklist", "data": {"skillName": "tasklist"}},
                    {"type": "chip", "chipType": "doc", "label": "docs://versions/nonexistent.md", "data": {"uri": "docs://versions/nonexistent.md"}},
                ],
                "chips": [
                    {"type": "skill", "label": "tasklist", "data": {"skillName": "tasklist"}},
                    {"type": "doc", "label": "docs://versions/nonexistent.md", "data": {"uri": "docs://versions/nonexistent.md"}},
                ],
            }
        }],
        "skill": "utility-skill",
    }, timeout=15)

    agent_steps = [c for c in chunks if c.get("type") == "agent_step_start"]
    step_ends = [c for c in chunks if c.get("type") == "agent_step_end"]
    text_chunks = [c for c in chunks if c.get("type") == "text"]

    if agent_steps:
        ok("Agent 步骤被触发 (read_resource)")
    else:
        fail("应触发 read_resource 步骤")

    if step_ends and step_ends[0].get("status") == "error":
        ok("read_resource 步骤报错", step_ends[0].get("summary", "")[:60])
    else:
        fail("read_resource 应报错")

    if text_chunks:
        content = text_chunks[0].get("content", "")
        if "不存在" in content or "可用" in content:
            ok("返回不存在的错误提示 + 可用方案列表")
        else:
            fail("应返回不存在提示", content[:80])
    else:
        fail("应有错误提示文本")


def test_full_agent_chain(version_uri, version_name):
    """测试 11/12: 完整 Agent 链路"""
    chunks = make_request({
        "messages": [{
            "role": "user",
            "content": f"/tasklist @{version_uri}",
            "structured": {
                "rawText": "/tasklist",
                "segments": [
                    {"type": "chip", "chipType": "skill", "label": "tasklist", "data": {"skillName": "tasklist"}},
                    {"type": "chip", "chipType": "doc", "label": version_uri, "data": {"uri": version_uri}},
                ],
                "chips": [
                    {"type": "skill", "label": "tasklist", "data": {"skillName": "tasklist"}},
                    {"type": "doc", "label": version_uri, "data": {"uri": version_uri}},
                ],
            }
        }],
        "skill": "utility-skill",
    }, timeout=120)

    step_starts = [c for c in chunks if c.get("type") == "agent_step_start"]
    step_ends = [c for c in chunks if c.get("type") == "agent_step_end"]
    resource_starts = [c for c in chunks if c.get("type") == "resource_start"]
    resource_ends = [c for c in chunks if c.get("type") == "resource_end"]
    done_chunks = [c for c in chunks if c.get("type") == "done"]
    error_chunks = [c for c in chunks if c.get("type") == "error"]
    text_chunks = [c for c in chunks if c.get("type") == "text"]

    # 验证 Agent 步骤序列 (Pi Agent 包含 plan_readiness 和 revision_eval)
    expected_actions = ["read_resource", "plan_extract", "plan_readiness", "draft_tasklist", "validate_tasklist"]
    actual_actions = [s.get("actionType") for s in step_starts]

    for action in expected_actions:
        if action in actual_actions:
            ok(f"步骤存在: {action}")
        else:
            fail(f"缺少步骤: {action}")

    # 验证 read_resource 成功
    read_end = [e for e in step_ends if step_starts and e.get("stepIndex") == 0]
    if read_end and read_end[0].get("status") == "success":
        ok("read_resource 成功", read_end[0].get("summary", "")[:50])
    elif read_end and read_end[0].get("status") == "error":
        fail("read_resource 失败", read_end[0].get("summary", ""))

    # 验证 plan_extract 成功
    extract_end = [e for e in step_ends if step_starts and e.get("stepIndex") == 1]
    if extract_end and extract_end[0].get("status") == "success":
        ok("plan_extract 成功", extract_end[0].get("summary", "")[:50])
    elif extract_end:
        fail("plan_extract 失败", extract_end[0].get("summary", ""))

    # 验证 plan_readiness 成功
    readiness_end = [e for e in step_ends if step_starts and e.get("stepIndex") == 2]
    if readiness_end and readiness_end[0].get("status") == "success":
        ok("plan_readiness 通过", readiness_end[0].get("summary", "")[:50])
    elif readiness_end:
        fail("plan_readiness 未通过", readiness_end[0].get("summary", ""))

    # 验证 draft 成功
    draft_end = [e for e in step_ends if step_starts and e.get("stepIndex") == 3]
    if draft_end and draft_end[0].get("status") == "success":
        ok("draft_tasklist 成功")
    elif draft_end:
        fail("draft_tasklist 失败", draft_end[0].get("summary", ""))

    # 验证 validate
    validate_start = [s for s in step_starts if s.get("actionType") == "validate_tasklist"]
    validate_end = [e for e in step_ends if e.get("stepIndex") == 4]
    if validate_start and validate_end:
        status = validate_end[0].get("status")
        if status in ("success", "error"):
            ok(f"validate_tasklist 执行 (status={status})", validate_end[0].get("summary", "")[:50])
        else:
            fail("validate_tasklist 状态异常", status)

    # 验证修正步骤 (如果有)
    revise_starts = [s for s in step_starts if s.get("actionType") == "revise_tasklist"]
    if revise_starts:
        ok("自动修正被触发 (revise_tasklist)")
        # 应有 revision_eval
        eval_starts = [s for s in step_starts if s.get("actionType") == "revision_eval"]
        if eval_starts:
            ok("修正效果评估被触发 (revision_eval)")
        else:
            fail("修正后应有 revision_eval")
    else:
        ok("无需自动修正 (草稿 v1 已通过或仅 warning)")

    # 验证 final_answer
    final_starts = [s for s in step_starts if s.get("actionType") == "final_answer"]
    if final_starts:
        ok("final_answer 步骤存在")

    # 验证资源读取
    if resource_starts:
        ok(f"资源读取: {resource_starts[0].get('resourceName')}")
    else:
        fail("应读取版本方案资源")

    if resource_ends:
        ok(f"资源读取完成 (truncated={resource_ends[0].get('isTruncated')})")

    # 验证最终输出文本
    all_text = "".join(c.get("content", "") for c in text_chunks)
    if "最终输出" in all_text or "Tasklist" in all_text or "tasklist" in all_text.lower():
        ok("最终输出包含 tasklist 草稿")
    else:
        fail("最终输出应包含 tasklist", all_text[:100])

    if "人工确认" in all_text or "未自动写入" in all_text:
        ok("最终输出包含人工确认点")
    else:
        fail("最终输出应包含人工确认点")

    # 验证流正常结束
    if done_chunks:
        ok("流正常结束 (done)")
    else:
        fail("应有 done chunk")

    if error_chunks:
        fail("不应有 error chunk", error_chunks[0].get("error", "")[:80])

    # 打印步骤总览
    print(f"\n  ── Agent 步骤总览 ({version_name}) ──")
    for s in step_starts:
        idx = s.get("stepIndex", 0)
        end = [e for e in step_ends if e.get("stepIndex") == idx]
        status = end[0].get("status", "?") if end else "?"
        summary = end[0].get("summary", "")[:40] if end else ""
        dur = end[0].get("durationMs", "?") if end else "?"
        print(f"    [{idx}] {s.get('actionType'):25s} | {status:8s} | {dur:>5}ms | {summary}")


def test_no_tasklist_command():
    """测试 13: 无 /tasklist 命令 → 不应触发 Agent"""
    section("API 测试 13: 无 /tasklist 命令 (不应触发 Agent)")
    if not is_server_running():
        skip("服务器未运行")
        return

    chunks = make_request({
        "messages": [{
            "role": "user",
            "content": "帮我计算 1+1",
            "structured": None,
        }],
        "skill": "utility-skill",
    }, timeout=30)

    agent_steps = [c for c in chunks if c.get("type") in ("agent_step_start", "agent_step_end")]
    if not agent_steps:
        ok("未触发 Agent 路径 (正确)")
    else:
        fail("不应触发 Agent 路径")

    text_chunks = [c for c in chunks if c.get("type") == "text"]
    if text_chunks:
        ok("走了普通问答路径")
    else:
        fail("应有普通回答")

    done_chunks = [c for c in chunks if c.get("type") == "done"]
    if done_chunks:
        ok("流正常结束 (done)")


# ════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════

def run_unit_tests():
    test_validate_tasklist_structure()
    test_extract_plan_structure()
    test_resolve_version_plan_uri()
    test_agent_state()
    test_select_best_draft()
    test_thread_state()
    test_conversation_registry()
    test_text_collecting_writer()


def run_api_tests():
    test_missing_version_plan()
    test_nonexistent_version_plan()

    section("API 测试 11: 完整 Agent 链路 — v0.1.0")
    if is_server_running():
        test_full_agent_chain(
            "docs://versions/v0.1.0-controlled-tasklist-agent.md",
            "v0.1.0",
        )
    else:
        skip("完整 Agent 链路测试", "服务器未运行")

    section("API 测试 12: 完整 Agent 链路 — v0.2.0")
    if is_server_running():
        test_full_agent_chain(
            "docs://versions/v0.2.0-agent-trace-panel.md",
            "v0.2.0",
        )
    else:
        skip("完整 Agent 链路测试", "服务器未运行")

    test_no_tasklist_command()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Pi Agent 完整测试套件")
    print("=" * 60)

    args = set(sys.argv[1:])
    run_unit = "--unit" in args or not args
    run_api = "--api" in args or not args

    if run_unit:
        run_unit_tests()

    if run_api:
        run_api_tests()

    # ─── 汇总 ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  测试汇总")
    print(f"{'='*60}")
    total = passed + failed + skipped
    print(f"  通过: {passed} / {total}")
    print(f"  失败: {failed} / {total}")
    print(f"  跳过: {skipped} / {total}")
    print(f"  Result: {'ALL PASS' if failed == 0 else 'HAS FAILURES'}")
    print()

    sys.exit(0 if failed == 0 else 1)
