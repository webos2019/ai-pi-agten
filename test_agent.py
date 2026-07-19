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
    9. SteerQueue + ActiveStreamRegistry — 流式插话核心
    10. stream/protocol.py — chunk 工厂完整验证 (含 steer chunk 边界)
    11. stream/lifecycle.py — 真流式实时性 + StreamLifecycle (含 steer_queue_id 传递)
    12. _check_steer — Agent 步骤边界 steer 消费
    13. _generate_tasklist_draft — steer_history 注入 prompt (mock 模型)

  API 端到端测试:
    14. steer API 端点 — POST/GET (TestClient, 不需服务器运行)
    15. /tasklist 无版本方案引用 → 应返回提示 (需服务器运行)
    16. /tasklist 引用不存在的版本方案 → 应报错并列出可用方案
    17. 完整 Agent 链路 — v0.1.0 (7+ 步)
    18. 完整 Agent 链路 — v0.2.0
    19. 无 /tasklist 命令 → 不应触发 Agent

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

# Windows 控制台 GBK 编码无法打印 emoji，强制 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, Exception):
        pass

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

    # 6e: should_compact (token 感知: 需消息数超上限 + token 估算达标)
    state2 = ThreadState(thread_id="test-thread-002")
    for i in range(MAX_RECENT_MESSAGES):
        state2.append("user", f"消息 {i}")
    if not state2.should_compact():
        ok(f"未超上限时不需压缩 ({MAX_RECENT_MESSAGES} 条)")
    else:
        fail(f"{MAX_RECENT_MESSAGES} 条不应触发压缩")

    # 超上限但 token 不足时不应触发
    state2.append("user", f"消息 {MAX_RECENT_MESSAGES}")
    if not state2.should_compact():
        ok(f"超上限但 token 不足时不触发 ({MAX_RECENT_MESSAGES + 1} 条短消息)")
    else:
        fail(f"短消息不应触发压缩 (token 不足)")

    # 超上限且 token 达标时触发 (需总字符 ≈ 4800 * 3.5 ≈ 16800)
    long_msg = "这是一段较长的对话内容用于测试压缩阈值。" * 900
    state2.append("user", long_msg)
    if state2.should_compact():
        ok(f"超上限且 token 达标时触发压缩 ({MAX_RECENT_MESSAGES + 2} 条)")
    else:
        fail(f"长消息应触发压缩")

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


def test_steer_queue():
    """测试 9: SteerQueue + ActiveStreamRegistry 流式插话机制"""
    section("单元测试 9: SteerQueue + ActiveStreamRegistry")
    import asyncio
    from steer_queue import SteerQueue, ActiveStreamRegistry, SteerEntry

    # 9a: SteerQueue 基本入队
    queue = SteerQueue("test-steer-001")
    success, msg, entry = queue.enqueue("调整方向：增加测试覆盖")
    if success and entry and entry.text == "调整方向：增加测试覆盖":
        ok("SteerQueue 入队成功", f"id={entry.id[:8]}...")
    else:
        fail("入队应成功", msg)

    # 9b: 空文本不入队
    success2, msg2, entry2 = queue.enqueue("")
    if not success2 and entry2 is None:
        ok("空文本不入队")
    else:
        fail("空文本应被拒绝")

    success3, msg3, entry3 = queue.enqueue("   ")
    if not success3:
        ok("纯空白文本不入队")
    else:
        fail("纯空白文本应被拒绝")

    # 9c: drain 消费
    async def _drain_test():
        return await queue.drain()
    drained = asyncio.new_event_loop().run_until_complete(_drain_test())
    if len(drained) == 1 and drained[0].applied:
        ok("drain 消费 steer 成功", f"applied={drained[0].applied}")
    else:
        fail("drain 应消费 1 条", f"drained={len(drained)}")

    # 9d: drain 后队列为空
    async def _drain_empty():
        return await queue.drain()
    drained2 = asyncio.new_event_loop().run_until_complete(_drain_empty())
    if len(drained2) == 0:
        ok("drain 后队列为空")
    else:
        fail("drain 后队列应为空")

    # 9e: mark_applied
    queue2 = SteerQueue("test-steer-002")
    queue2.enqueue("测试 steer")
    async def _drain_q2():
        return await queue2.drain()
    entries = asyncio.new_event_loop().run_until_complete(_drain_q2())
    queue2.mark_applied(entries[0], step_index=3, action_type="draft_tasklist")
    if entries[0].applied_at_step == 3 and entries[0].applied_at_action == "draft_tasklist":
        ok("mark_applied 正确标记步骤和动作")
    else:
        fail("mark_applied 应标记步骤和动作")

    # 9f: reject_pending — 流结束后拒绝未处理的 steer
    queue3 = SteerQueue("test-steer-003")
    queue3.enqueue("steer 1")
    queue3.enqueue("steer 2")
    rejected = queue3.reject_pending("流已结束")
    if len(rejected) == 2 and all(r.rejected for r in rejected):
        ok("reject_pending 拒绝所有未处理 steer", f"{len(rejected)} 条")
    else:
        fail("reject_pending 应拒绝 2 条", f"{len(rejected)} 条")

    # 9g: 流结束后入队被拒绝
    success4, msg4, _ = queue3.enqueue("不应入队")
    if not success4 and "结束" in msg4:
        ok("流结束后入队被拒绝")
    else:
        fail("流结束后应拒绝入队", msg4)

    # 9h: ActiveStreamRegistry 注册/获取/注销
    registry = ActiveStreamRegistry()
    q = registry.register("stream-001")
    if registry.get("stream-001") is q:
        ok("ActiveStreamRegistry 注册并获取成功")
    else:
        fail("注册后应能获取")

    # 9i: enqueue 到活跃流
    success5, msg5, entry5 = registry.enqueue("stream-001", "调整草稿方向")
    if success5 and entry5:
        ok("向活跃流入队 steer 成功")
    else:
        fail("应能向活跃流入队", msg5)

    # 9j: enqueue 到不存在的流
    success6, msg6, _ = registry.enqueue("nonexistent", "测试")
    if not success6:
        ok("向不存在的流入队返回失败")
    else:
        fail("不存在的流应返回失败")

    # 9k: unregister 拒绝未处理的 steer
    registry.enqueue("stream-001", "未处理的 steer")
    rejected_streams = registry.unregister("stream-001")
    if len(rejected_streams) == 2 and all(r.rejected for r in rejected_streams):
        ok("unregister 拒绝未处理的 steer", f"{len(rejected_streams)} 条")
    else:
        fail("unregister 应拒绝未处理的 steer", f"{len(rejected_streams)} 条")

    # 9l: unregister 后流不再活跃
    if not registry.is_active("stream-001"):
        ok("unregister 后流不再活跃")
    else:
        fail("unregister 后流不应活跃")

    # 9m: SteerEntry to_dto
    entry_dto = entry5.to_dto()
    if entry_dto.get("text") == "调整草稿方向" and entry_dto.get("applied") is False:
        ok("SteerEntry.to_dto 返回正确结构")
    else:
        fail("SteerEntry.to_dto 结构不正确")

    # 9n: AgentState.steer_history 字段
    from agent_runtime import AgentState
    state = AgentState(run_id="test", version_plan_uri="docs://versions/test.md")
    if state.steer_history == []:
        ok("AgentState.steer_history 初始化为空列表")
    else:
        fail("steer_history 应初始化为空列表")

    state.steer_history.append("调整方向 1")
    state.steer_history.append("调整方向 2")
    if len(state.steer_history) == 2:
        ok("AgentState.steer_history 可追加", f"{len(state.steer_history)} 条")
    else:
        fail("steer_history 追加异常")

    # 9o: steer chunk 工厂函数
    from stream import (
        create_steer_queued_chunk,
        create_steer_applied_chunk,
        create_steer_rejected_chunk,
        create_start_chunk,
    )

    queued_chunk = create_steer_queued_chunk("s1", "测试文本", 1)
    if queued_chunk["type"] == "steer_queued" and queued_chunk["steerId"] == "s1":
        ok("create_steer_queued_chunk 正确")
    else:
        fail("steer_queued chunk 结构错误")

    applied_chunk = create_steer_applied_chunk("s1", "测试文本", 3, "draft_tasklist")
    if applied_chunk["type"] == "steer_applied" and applied_chunk["appliedAtStep"] == 3:
        ok("create_steer_applied_chunk 正确")
    else:
        fail("steer_applied chunk 结构错误")

    rejected_chunk = create_steer_rejected_chunk("s1", "测试文本", "流已结束")
    if rejected_chunk["type"] == "steer_rejected" and rejected_chunk["reason"] == "流已结束":
        ok("create_steer_rejected_chunk 正确")
    else:
        fail("steer_rejected chunk 结构错误")

    # 9p: start chunk 携带 steerQueueId
    start_chunk = create_start_chunk("msg-001", "steer-queue-001")
    if start_chunk.get("steerQueueId") == "steer-queue-001":
        ok("start chunk 携带 steerQueueId")
    else:
        fail("start chunk 应携带 steerQueueId")

    # 9q: start chunk 不带 steerQueueId 时字段不存在
    start_chunk2 = create_start_chunk("msg-002")
    if "steerQueueId" not in start_chunk2:
        ok("start chunk 无 steerQueueId 时字段不存在")
    else:
        fail("无 steerQueueId 时不应有该字段")


def test_stream_protocol():
    """测试 10: stream/protocol.py chunk 工厂完整验证"""
    section("单元测试 10: stream/protocol.py chunk 工厂")
    from stream import (
        create_id, create_start_chunk, create_text_chunk, create_reasoning_chunk,
        create_steer_queued_chunk, create_steer_applied_chunk, create_steer_rejected_chunk,
        create_agent_step_start_chunk, create_agent_step_end_chunk,
        create_tool_call_chunk, create_tool_result_chunk,
        create_resource_start_chunk, create_resource_end_chunk, create_resource_error_chunk,
        create_error_chunk, create_recovering_chunk, create_recovery_fallback_chunk,
        create_done_chunk, AGENT_STEP_ACTIONS,
    )

    # 10a: create_id 唯一性
    ids = [create_id() for _ in range(100)]
    if len(set(ids)) == 100:
        ok("create_id 生成 100 个唯一 ID")
    else:
        fail("create_id 应生成唯一 ID")

    # 10b: start chunk 无 steer_queue_id
    c = create_start_chunk("msg-001")
    if c["type"] == "start" and c["messageId"] == "msg-001" and "steerQueueId" not in c:
        ok("start chunk 无 steer_queue_id 时不含该字段")
    else:
        fail("start chunk 无 steer_queue_id 字段处理错误")

    # 10c: start chunk 有 steer_queue_id
    c = create_start_chunk("msg-002", "steer-123")
    if c.get("steerQueueId") == "steer-123":
        ok("start chunk 携带 steer_queue_id")
    else:
        fail("start chunk 应携带 steer_queue_id")

    # 10d: steer_queued chunk 完整字段
    c = create_steer_queued_chunk("s1", "调整方向", 3)
    if (c["type"] == "steer_queued" and c["steerId"] == "s1"
            and c["steerText"] == "调整方向" and c["queueSize"] == 3):
        ok("steer_queued chunk 完整字段正确")
    else:
        fail("steer_queued chunk 字段错误")

    # 10e: steer_queued chunk queueSize=0 边界
    c = create_steer_queued_chunk("s2", "", 0)
    if c["queueSize"] == 0 and c["steerText"] == "":
        ok("steer_queued chunk queueSize=0 边界正确")
    else:
        fail("steer_queued chunk queueSize=0 应正确")

    # 10f: steer_applied chunk 完整字段
    c = create_steer_applied_chunk("s1", "调整方向", 5, "draft_tasklist")
    if (c["type"] == "steer_applied" and c["appliedAtStep"] == 5
            and c["actionType"] == "draft_tasklist" and c["steerText"] == "调整方向"):
        ok("steer_applied chunk 完整字段正确")
    else:
        fail("steer_applied chunk 字段错误")

    # 10g: steer_rejected chunk 完整字段
    c = create_steer_rejected_chunk("s1", "调整方向", "流已结束")
    if (c["type"] == "steer_rejected" and c["reason"] == "流已结束"
            and c["steerText"] == "调整方向"):
        ok("steer_rejected chunk 完整字段正确")
    else:
        fail("steer_rejected chunk 字段错误")

    # 10h: agent_step_start chunk 含可选 partId
    c = create_agent_step_start_chunk("run-1", 0, "read_resource", "读取资源", part_id="p1")
    if (c["type"] == "agent_step_start" and c["actionType"] == "read_resource"
            and c.get("partId") == "p1" and c["agentName"] == "pi-agent"):
        ok("agent_step_start chunk 含 partId 正确")
    else:
        fail("agent_step_start chunk 错误")

    # 10i: agent_step_end chunk 含可选字段
    c = create_agent_step_end_chunk("run-1", 0, "success", summary="ok", duration_ms=100, part_id="p1")
    if (c["status"] == "success" and c["summary"] == "ok"
            and c["durationMs"] == 100 and c.get("partId") == "p1"):
        ok("agent_step_end chunk 含可选字段正确")
    else:
        fail("agent_step_end chunk 可选字段错误")

    # 10j: agent_step_end chunk 无可选字段
    c = create_agent_step_end_chunk("run-1", 1, "error")
    if c["status"] == "error" and "summary" not in c and "durationMs" not in c:
        ok("agent_step_end chunk 无可选字段正确")
    else:
        fail("agent_step_end chunk 无可选字段应不含")

    # 10k: AGENT_STEP_ACTIONS 包含所有预期动作
    expected_actions = ["read_resource", "plan_extract", "plan_readiness",
                        "draft_tasklist", "validate_tasklist", "revise_tasklist",
                        "revision_eval", "final_answer"]
    if all(a in AGENT_STEP_ACTIONS for a in expected_actions) and len(AGENT_STEP_ACTIONS) == 8:
        ok("AGENT_STEP_ACTIONS 包含所有 8 个预期动作")
    else:
        fail("AGENT_STEP_ACTIONS 缺少动作", str(AGENT_STEP_ACTIONS))

    # 10l: tool_call chunk 含可选 serverId/source
    c = create_tool_call_chunk("tc-1", "calculator", {"expr": "1+1"}, server_id="s1", source="agent")
    if (c["toolCallId"] == "tc-1" and c.get("serverId") == "s1"
            and c.get("source") == "agent" and c["toolArgs"] == {"expr": "1+1"}):
        ok("tool_call chunk 含可选字段正确")
    else:
        fail("tool_call chunk 可选字段错误")

    # 10m: tool_result chunk 含 isAuthoritative
    c = create_tool_result_chunk("tc-1", "calculator", "2", is_valid=True, is_authoritative=True, server_id="s1")
    if c["isValid"] and c["isAuthoritative"] and c.get("serverId") == "s1":
        ok("tool_result chunk 正确")
    else:
        fail("tool_result chunk 错误")

    # 10n: error chunk 含重试字段
    c = create_error_chunk("出错了", retryable=True, retry_delay=5)
    if c["retryable"] is True and c["retryDelay"] == 5:
        ok("error chunk 含重试字段正确")
    else:
        fail("error chunk 重试字段错误")

    # 10o: resource chunks 含 serverId 和预览
    c1 = create_resource_start_chunk("plan", "docs://versions/v0.1.0.md", "server-1")
    c2 = create_resource_end_chunk("plan", "docs://versions/v0.1.0.md",
                                   content_preview="...", is_truncated=True, preview_chars=100)
    c3 = create_resource_error_chunk("plan", "docs://versions/v0.1.0.md", "不存在")
    if (c1["resourceName"] == "plan" and c1.get("serverId") == "server-1"
            and c2["isTruncated"] is True and c2.get("contentPreview") == "..."
            and c3["error"] == "不存在"):
        ok("resource chunks 完整字段正确")
    else:
        fail("resource chunks 错误")

    # 10p: recovering / recovery_fallback chunks
    c1 = create_recovering_chunk("恢复中", 1, 3)
    c2 = create_recovery_fallback_chunk("降级", "direct-answer")
    if c1["attempt"] == 1 and c1["maxAttempts"] == 3 and c2["fallbackMethod"] == "direct-answer":
        ok("recovering/recovery_fallback chunks 正确")
    else:
        fail("recovering/recovery_fallback chunks 错误")

    # 10q: done / reasoning / text chunks 基础验证
    if (create_done_chunk()["type"] == "done"
            and create_reasoning_chunk("思考")["type"] == "reasoning"
            and create_text_chunk("文本")["content"] == "文本"):
        ok("done/reasoning/text chunks 正确")
    else:
        fail("done/reasoning/text chunks 错误")


def test_stream_lifecycle():
    """测试 11: stream/lifecycle.py 真流式实时性 + StreamLifecycle"""
    section("单元测试 11: stream/lifecycle.py 真流式 + StreamLifecycle")
    import asyncio
    import json as _json
    from stream import (
        StreamWriter, StreamLifecycle, create_ndjson_stream,
        create_text_chunk, create_done_chunk,
    )

    # 11a: StreamWriter write_chunk 立即可消费（实时性）
    async def _test_realtime_consume():
        writer = StreamWriter()
        writer.write_chunk(create_text_chunk("chunk-1"))
        # 直接从 queue 取，验证不需 close 即可消费
        chunk = await writer._queue.get()
        if chunk["content"] == "chunk-1":
            ok("StreamWriter write_chunk 立即可消费（实时性）")
        else:
            fail("write_chunk 应立即可消费")
    asyncio.new_event_loop().run_until_complete(_test_realtime_consume())

    # 11b: StreamWriter close 后 chunks() 正确结束
    async def _test_close_ends():
        writer = StreamWriter()
        writer.write_chunk(create_text_chunk("a"))
        writer.write_chunk(create_text_chunk("b"))
        writer.close()
        chunks = []
        async for c in writer.chunks():
            chunks.append(c)
        if len(chunks) == 2 and chunks[0]["content"] == "a" and chunks[1]["content"] == "b":
            ok("StreamWriter close 后 chunks() 正确结束", f"{len(chunks)} 个 chunk")
        else:
            fail("close 后 chunks() 应正确结束", f"len={len(chunks)}")
    asyncio.new_event_loop().run_until_complete(_test_close_ends())

    # 11c: StreamWriter get_chunks 快照不消费 queue
    writer = StreamWriter()
    writer.write_chunk(create_text_chunk("x"))
    writer.write_chunk(create_text_chunk("y"))
    if len(writer.get_chunks()) == 2 and writer._queue.qsize() == 2:
        ok("StreamWriter get_chunks 返回快照且不消费 queue")
    else:
        fail("get_chunks 应返回快照不消费 queue", f"queue_size={writer._queue.qsize()}")

    # 11d: StreamWriter is_closed 状态
    writer = StreamWriter()
    if not writer.is_closed():
        writer.close()
        if writer.is_closed():
            ok("StreamWriter is_closed 状态正确")
        else:
            fail("close 后 is_closed 应为 True")
    else:
        fail("初始 is_closed 应为 False")

    # 11e: StreamWriter close 幂等
    writer = StreamWriter()
    writer.close()
    writer.close()  # 第二次不应报错
    if writer.is_closed():
        ok("StreamWriter close 幂等（二次调用不报错）")
    else:
        fail("close 应幂等")

    # 11f: StreamWriter write_chunk_async 异步写入
    async def _test_async_write():
        writer = StreamWriter()
        await writer.write_chunk_async(create_text_chunk("async-chunk"))
        writer.close()
        chunks = []
        async for c in writer.chunks():
            chunks.append(c)
        if len(chunks) == 1 and chunks[0]["content"] == "async-chunk":
            ok("StreamWriter write_chunk_async 异步写入正确")
        else:
            fail("write_chunk_async 应正确写入")
    asyncio.new_event_loop().run_until_complete(_test_async_write())

    # 11g: StreamLifecycle emit_start_once 携带 steer_queue_id
    writer = StreamWriter()
    lifecycle = StreamLifecycle(writer)
    result = lifecycle.emit_start_once("msg-001", "steer-queue-abc")
    chunks = writer.get_chunks()
    if result and chunks[0].get("steerQueueId") == "steer-queue-abc":
        ok("StreamLifecycle emit_start_once 携带 steer_queue_id")
    else:
        fail("emit_start_once 应携带 steer_queue_id")

    # 11h: emit_start_once 幂等（第二次返回 False）
    result2 = lifecycle.emit_start_once("msg-002", "steer-queue-2")
    if not result2 and len(writer.get_chunks()) == 1:
        ok("emit_start_once 幂等（第二次返回 False）")
    else:
        fail("emit_start_once 应幂等")

    # 11i: emit_done_once 幂等
    writer2 = StreamWriter()
    lc2 = StreamLifecycle(writer2)
    lc2.emit_start_once("msg-003")
    r1 = lc2.emit_done_once()
    r2 = lc2.emit_done_once()
    if r1 and not r2:
        ok("emit_done_once 幂等")
    else:
        fail("emit_done_once 应幂等")

    # 11j: emit_error_once 幂等
    writer3 = StreamWriter()
    lc3 = StreamLifecycle(writer3)
    r1 = lc3.emit_error_once("出错了")
    r2 = lc3.emit_error_once("再次出错")
    if r1 and not r2:
        ok("emit_error_once 幂等")
    else:
        fail("emit_error_once 应幂等")

    # 11k: terminated 后 write_chunk 丢弃
    writer4 = StreamWriter()
    lc4 = StreamLifecycle(writer4)
    lc4.emit_done_once()
    lc4.write_chunk(create_text_chunk("不应出现"))
    if len(writer4.get_chunks()) == 1:  # 只有 done chunk
        ok("terminated 后 write_chunk 被丢弃")
    else:
        fail("terminated 后 write_chunk 应被丢弃", f"len={len(writer4.get_chunks())}")

    # 11l: close 后所有写入被丢弃
    writer5 = StreamWriter()
    lc5 = StreamLifecycle(writer5)
    lc5.close()
    lc5.emit_start_once("msg")
    lc5.write_chunk(create_text_chunk("不应"))
    if len(writer5.get_chunks()) == 0:
        ok("close 后所有写入被丢弃")
    else:
        fail("close 后不应有写入", f"len={len(writer5.get_chunks())}")

    # 11m: StreamLifecycle is_closed
    writer6 = StreamWriter()
    lc6 = StreamLifecycle(writer6)
    if not lc6.is_closed():
        lc6.close()
        if lc6.is_closed():
            ok("StreamLifecycle is_closed 状态正确")
        else:
            fail("close 后 is_closed 应为 True")
    else:
        fail("初始 is_closed 应为 False")

    # 11n: create_ndjson_stream 实时 yield（按写入顺序）
    async def _test_realtime_stream():
        async def on_start(writer):
            writer.write_chunk(create_text_chunk("first"))
            await asyncio.sleep(0.05)
            writer.write_chunk(create_text_chunk("second"))

        chunks = []
        async for line in create_ndjson_stream(on_start):
            parsed = _json.loads(line.strip())
            chunks.append(parsed)

        texts = [c["content"] for c in chunks if c["type"] == "text"]
        if texts == ["first", "second"]:
            ok("create_ndjson_stream 实时 yield 顺序正确")
        else:
            fail("create_ndjson_stream 应实时 yield", str(texts))
    asyncio.new_event_loop().run_until_complete(_test_realtime_stream())

    # 11o: create_ndjson_stream on_start 异常写入 error chunk
    async def _test_error_stream():
        async def on_start(writer):
            writer.write_chunk(create_text_chunk("before error"))
            raise RuntimeError("测试异常")

        chunks = []
        async for line in create_ndjson_stream(on_start):
            chunks.append(_json.loads(line.strip()))

        error_chunks = [c for c in chunks if c["type"] == "error"]
        text_chunks = [c for c in chunks if c["type"] == "text"]
        if error_chunks and error_chunks[0]["error"] == "测试异常" and text_chunks:
            ok("create_ndjson_stream 异常时写入 error chunk", error_chunks[0]["error"][:30])
        else:
            fail("异常应写入 error chunk",
                 f"errors={len(error_chunks)}, texts={len(text_chunks)}")
    asyncio.new_event_loop().run_until_complete(_test_error_stream())

    # 11p: create_ndjson_stream 正常完成无多余 chunk
    async def _test_normal_stream():
        async def on_start(writer):
            writer.write_chunk(create_text_chunk("hello"))

        chunks = []
        async for line in create_ndjson_stream(on_start):
            chunks.append(_json.loads(line.strip()))

        if len(chunks) == 1 and chunks[0]["content"] == "hello":
            ok("create_ndjson_stream 正常完成（无多余 chunk）")
        else:
            fail("正常流应只有 1 个 chunk", f"len={len(chunks)}")
    asyncio.new_event_loop().run_until_complete(_test_normal_stream())


def test_check_steer():
    """测试 12: agent_runtime._check_steer 步骤边界 steer 消费"""
    section("单元测试 12: _check_steer 步骤边界 steer 消费")
    import asyncio
    from agent_runtime import AgentState, _check_steer
    from stream import StreamWriter, StreamLifecycle
    from steer_queue import SteerQueue

    # 12a: 无 steer_queue → 返回空列表，不报错
    async def _test_no_queue():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        result = await _check_steer(lc, state, None, 3, "draft_tasklist")
        if result == [] and len(state.steer_history) == 0:
            ok("无 steer_queue 时返回空列表")
        else:
            fail("无 steer_queue 应返回空列表")
    asyncio.new_event_loop().run_until_complete(_test_no_queue())

    # 12b: 空队列 → 返回空列表
    async def _test_empty_queue():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        sq = SteerQueue("test-sq-empty")
        result = await _check_steer(lc, state, sq, 3, "draft_tasklist")
        if result == [] and len(state.steer_history) == 0:
            ok("空 steer 队列时返回空列表")
        else:
            fail("空队列应返回空列表")
    asyncio.new_event_loop().run_until_complete(_test_empty_queue())

    # 12c: 有 steer → 消费 + mark_applied + steer_applied chunk + steer_history + 文本提示
    async def _test_consume_steer():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        sq = SteerQueue("test-sq-consume")
        sq.enqueue("调整方向：增加测试覆盖")

        result = await _check_steer(lc, state, sq, 5, "validate_tasklist")

        chunks = writer.get_chunks()
        steer_applied_chunks = [c for c in chunks if c["type"] == "steer_applied"]
        text_chunks = [c for c in chunks if c["type"] == "text"]

        checks = [
            len(result) == 1,
            result[0].applied,
            result[0].applied_at_step == 5,
            result[0].applied_at_action == "validate_tasklist",
            len(steer_applied_chunks) == 1,
            steer_applied_chunks[0]["steerText"] == "调整方向：增加测试覆盖",
            steer_applied_chunks[0]["appliedAtStep"] == 5,
            steer_applied_chunks[0]["actionType"] == "validate_tasklist",
            len(state.steer_history) == 1,
            state.steer_history[0] == "调整方向：增加测试覆盖",
            len(text_chunks) == 1,
            "已接收转向指令" in text_chunks[0]["content"],
            "调整方向：增加测试覆盖" in text_chunks[0]["content"],
        ]
        if all(checks):
            ok("_check_steer 消费 steer 并发送 chunk + 注入 history + 文本提示")
        else:
            fail("_check_steer 消费逻辑错误",
                 f"result={len(result)}, applied_chunks={len(steer_applied_chunks)}, "
                 f"history={len(state.steer_history)}, texts={len(text_chunks)}")
    asyncio.new_event_loop().run_until_complete(_test_consume_steer())

    # 12d: 多条 steer → 全部消费
    async def _test_multiple_steer():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        sq = SteerQueue("test-sq-multi")
        sq.enqueue("steer 1")
        sq.enqueue("steer 2")
        sq.enqueue("steer 3")

        result = await _check_steer(lc, state, sq, 2, "plan_extract")

        chunks = writer.get_chunks()
        steer_applied_chunks = [c for c in chunks if c["type"] == "steer_applied"]
        text_chunks = [c for c in chunks if c["type"] == "text"]

        if (len(result) == 3
                and len(steer_applied_chunks) == 3
                and len(text_chunks) == 3
                and len(state.steer_history) == 3
                and state.steer_history == ["steer 1", "steer 2", "steer 3"]
                and sq.pending_count() == 0):
            ok("_check_steer 消费多条 steer", f"{len(result)} 条全部消费")
        else:
            fail("应消费 3 条 steer",
                 f"result={len(result)}, chunks={len(steer_applied_chunks)}, "
                 f"history={len(state.steer_history)}")
    asyncio.new_event_loop().run_until_complete(_test_multiple_steer())

    # 12e: 消费后队列清空（再次调用返回空）
    async def _test_drain_clears():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        sq = SteerQueue("test-sq-clear")
        sq.enqueue("steer once")

        await _check_steer(lc, state, sq, 1, "read_resource")

        # 再次调用，队列应空
        writer2 = StreamWriter()
        lc2 = StreamLifecycle(writer2)
        result2 = await _check_steer(lc2, state, sq, 2, "plan_extract")

        if result2 == [] and len(writer2.get_chunks()) == 0:
            ok("_check_steer 消费后队列清空（二次调用无 chunk）")
        else:
            fail("消费后队列应清空", f"len={len(result2)}, chunks={len(writer2.get_chunks())}")
    asyncio.new_event_loop().run_until_complete(_test_drain_clears())

    # 12f: steer 已结束的队列 → drain 返回空
    async def _test_ended_queue():
        writer = StreamWriter()
        lc = StreamLifecycle(writer)
        state = AgentState(version_plan_uri="docs://versions/test.md")
        sq = SteerQueue("test-sq-ended")
        sq.reject_pending("流已结束")

        result = await _check_steer(lc, state, sq, 3, "draft_tasklist")

        if result == [] and len(state.steer_history) == 0:
            ok("_check_steer 对已结束队列返回空列表")
        else:
            fail("已结束队列应返回空列表")
    asyncio.new_event_loop().run_until_complete(_test_ended_queue())


def test_steer_prompt_injection():
    """测试 13: _generate_tasklist_draft steer_history 注入 prompt"""
    section("单元测试 13: _generate_tasklist_draft steer_history 注入")
    import asyncio
    import agent_runtime
    from agent_runtime import AgentState, _generate_tasklist_draft

    # 保存原始函数，测试后恢复
    original_completion = agent_runtime.chat_completion
    captured_messages: list[list] = []

    async def mock_completion(messages=None, **kwargs):
        captured_messages.append(list(messages) if messages else [])

        class MockMessage:
            content = (
                "# Tasklist\n来源: docs://versions/test.md\n"
                "- [ ] 步骤一\n- [ ] 步骤二\n- [ ] 步骤三\n验证: ok"
            )

        class MockChoice:
            message = MockMessage()

        class MockResponse:
            choices = [MockChoice()]

        return MockResponse()

    agent_runtime.chat_completion = mock_completion
    try:
        # 13a: 无 steer_history → prompt 不含 steer 注入
        captured_messages.clear()
        state = AgentState(version_plan_uri="docs://versions/test.md")
        asyncio.new_event_loop().run_until_complete(
            _generate_tasklist_draft(state, "plan text", is_revision=False)
        )
        if captured_messages:
            user_content = captured_messages[-1][-1]["content"]
            if "转向指令" not in user_content and "插话" not in user_content:
                ok("无 steer_history 时 prompt 不含 steer 注入")
            else:
                fail("无 steer_history 时 prompt 不应含 steer 注入", user_content[:80])
        else:
            fail("应调用 chat_completion")

        # 13b: 有 steer_history → prompt 包含 steer 注入文本
        captured_messages.clear()
        state2 = AgentState(version_plan_uri="docs://versions/test.md")
        state2.steer_history = ["增加测试覆盖", "简化步骤"]
        asyncio.new_event_loop().run_until_complete(
            _generate_tasklist_draft(state2, "plan text", is_revision=False)
        )
        if captured_messages:
            user_content = captured_messages[-1][-1]["content"]
            if ("转向指令" in user_content
                    and "增加测试覆盖" in user_content
                    and "简化步骤" in user_content
                    and "调整草稿" in user_content):
                ok("有 steer_history 时 prompt 包含 steer 注入")
            else:
                fail("有 steer_history 时 prompt 应包含 steer 注入", user_content[:100])
        else:
            fail("应调用 chat_completion")

        # 13c: 修正模式 + steer_history → prompt 同时包含修正指令和 steer 注入
        captured_messages.clear()
        state3 = AgentState(version_plan_uri="docs://versions/test.md")
        state3.steer_history = ["调整方向"]
        asyncio.new_event_loop().run_until_complete(
            _generate_tasklist_draft(state3, "plan text", is_revision=True, issues="缺少标题")
        )
        if captured_messages:
            user_content = captured_messages[-1][-1]["content"]
            if ("阻断性问题" in user_content
                    and "转向指令" in user_content
                    and "调整方向" in user_content
                    and "缺少标题" in user_content):
                ok("修正模式 + steer_history 时 prompt 同时包含两者")
            else:
                fail("修正模式 + steer 应同时包含修正和 steer 注入")
        else:
            fail("应调用 chat_completion")

        # 13d: system prompt 始终包含格式要求
        captured_messages.clear()
        state4 = AgentState(version_plan_uri="docs://versions/test.md")
        asyncio.new_event_loop().run_until_complete(
            _generate_tasklist_draft(state4, "plan text")
        )
        if captured_messages:
            sys_content = captured_messages[-1][0]["content"]
            if "任务清单生成助手" in sys_content and "格式要求" in sys_content:
                ok("system prompt 包含格式要求")
            else:
                fail("system prompt 应包含格式要求")
        else:
            fail("应调用 chat_completion")

        # 13e: 版本方案 URI 注入 user prompt
        captured_messages.clear()
        state5 = AgentState(version_plan_uri="docs://versions/v0.3.0-test.md")
        asyncio.new_event_loop().run_until_complete(
            _generate_tasklist_draft(state5, "plan text")
        )
        if captured_messages:
            user_content = captured_messages[-1][-1]["content"]
            if "docs://versions/v0.3.0-test.md" in user_content:
                ok("版本方案 URI 注入 user prompt")
            else:
                fail("user prompt 应包含版本方案 URI")
        else:
            fail("应调用 chat_completion")
    finally:
        agent_runtime.chat_completion = original_completion


def test_steer_api_endpoints():
    """测试 14: app.py steer API 端点 (TestClient)"""
    section("API 测试 14: steer API 端点 (TestClient)")
    try:
        from fastapi.testclient import TestClient
        import app as app_module
    except ImportError as e:
        skip("steer API 测试", f"导入失败: {e}")
        return

    client = TestClient(app_module.app)
    from steer_queue import active_streams

    # 14a: POST steer 缺少 steerQueueId → 400
    resp = client.post("/api/chat/steer", json={"steerText": "测试"})
    if resp.status_code == 400:
        ok("POST steer 缺少 steerQueueId 返回 400")
    else:
        fail("缺少 steerQueueId 应返回 400", f"status={resp.status_code}")

    # 14b: POST steer 缺少 steerText → 400
    resp = client.post("/api/chat/steer", json={"steerQueueId": "test-api-001"})
    if resp.status_code == 400:
        ok("POST steer 缺少 steerText 返回 400")
    else:
        fail("缺少 steerText 应返回 400", f"status={resp.status_code}")

    # 14c: POST steer 空白文本 → 400
    resp = client.post("/api/chat/steer", json={"steerQueueId": "test-api-001", "steerText": "   "})
    if resp.status_code == 400:
        ok("POST steer 空白文本返回 400")
    else:
        fail("空白文本应返回 400", f"status={resp.status_code}")

    # 14d: POST steer 到不存在的流 → 409
    resp = client.post("/api/chat/steer", json={"steerQueueId": "nonexistent-stream", "steerText": "测试"})
    if resp.status_code == 409:
        body = resp.json()
        if body.get("ok") is False and body.get("queued") is False:
            ok("POST steer 到不存在流返回 409", body.get("error", "")[:40])
        else:
            fail("409 响应体应 ok=False, queued=False", str(body)[:80])
    else:
        fail("不存在的流应返回 409", f"status={resp.status_code}")

    # 14e: POST steer 到活跃流 → 200
    test_id = "test-api-active-001"
    active_streams.register(test_id)
    try:
        resp = client.post("/api/chat/steer", json={"steerQueueId": test_id, "steerText": "调整方向"})
        if resp.status_code == 200:
            body = resp.json()
            if (body.get("ok") is True
                    and body.get("queued") is True
                    and body.get("steerId")
                    and body.get("queueSize") == 1):
                ok("POST steer 到活跃流成功", f"steerId={body['steerId'][:8]}...")
            else:
                fail("活跃流 steer 响应体错误", str(body)[:80])
        else:
            fail("活跃流应返回 200", f"status={resp.status_code}")
    finally:
        active_streams.unregister(test_id)

    # 14f: POST 多条 steer → queueSize 递增
    test_id2 = "test-api-multi-001"
    active_streams.register(test_id2)
    try:
        resp1 = client.post("/api/chat/steer", json={"steerQueueId": test_id2, "steerText": "steer 1"})
        resp2 = client.post("/api/chat/steer", json={"steerQueueId": test_id2, "steerText": "steer 2"})
        if (resp1.status_code == 200 and resp2.status_code == 200
                and resp1.json().get("queueSize") == 1
                and resp2.json().get("queueSize") == 2):
            ok("POST 多条 steer → queueSize 递增", f"1 → 2")
        else:
            fail("queueSize 应递增",
                 f"r1={resp1.json().get('queueSize')}, r2={resp2.json().get('queueSize')}")
    finally:
        active_streams.unregister(test_id2)

    # 14g: GET steer 状态 — 不存在 → 404
    resp = client.get("/api/chat/steer/nonexistent-id-999")
    if resp.status_code == 404:
        ok("GET steer 不存在返回 404")
    else:
        fail("不存在的 steerQueueId 应返回 404", f"status={resp.status_code}")

    # 14h: GET steer 状态 — 存在 → 200 + dto
    test_id3 = "test-api-status-001"
    active_streams.register(test_id3)
    active_streams.enqueue(test_id3, "测试 steer")
    try:
        resp = client.get(f"/api/chat/steer/{test_id3}")
        if resp.status_code == 200:
            body = resp.json()
            if (body.get("steerQueueId") == test_id3
                    and body.get("totalCount") == 1
                    and body.get("pendingCount") == 1
                    and len(body.get("entries", [])) == 1
                    and body.get("ended") is False):
                ok("GET steer 状态返回正确 dto", f"pending={body['pendingCount']}")
            else:
                fail("GET steer dto 结构错误", str(body)[:80])
        else:
            fail("存在的流应返回 200", f"status={resp.status_code}")
    finally:
        active_streams.unregister(test_id3)

    # 14i: POST steer 无效 JSON → 400
    resp = client.post("/api/chat/steer", content="not json",
                       headers={"Content-Type": "application/json"})
    if resp.status_code == 400:
        ok("POST steer 无效 JSON 返回 400")
    else:
        fail("无效 JSON 应返回 400", f"status={resp.status_code}")

    # 14j: 流结束后 steer → 409
    test_id4 = "test-api-ended-001"
    active_streams.register(test_id4)
    active_streams.unregister(test_id4)  # 立即注销
    resp = client.post("/api/chat/steer", json={"steerQueueId": test_id4, "steerText": "应被拒绝"})
    if resp.status_code == 409:
        ok("流结束后 steer 返回 409")
    else:
        fail("流结束后 steer 应返回 409", f"status={resp.status_code}")


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
    test_steer_queue()
    test_stream_protocol()
    test_stream_lifecycle()
    test_check_steer()
    test_steer_prompt_injection()


def run_api_tests():
    test_steer_api_endpoints()
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
