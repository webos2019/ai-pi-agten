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
import time
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
#  Agent Loop 单元测试 (pi.dev 风格钩子化循环)
# ════════════════════════════════════════════════════════

def test_agent_loop_basic():
    """测试 15: agent_loop 基本循环 — 无工具的单轮对话"""
    section("单元测试 15: agent_loop 基本循环 (无工具)")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig,
        AgentEvent,
    )

    call_count = 0

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        return AgentMessage(
            role="assistant",
            content="你好，我是 Agent",
            stop_reason="stop",
        )

    config = AgentLoopConfig(stream_fn=mock_stream_fn)
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[],
    )
    prompts = [AgentMessage(role="user", content="你好")]

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # 应该只调用 1 次 LLM（无工具调用，无 follow-up）
    if call_count == 1:
        ok("单轮对话 LLM 只调用 1 次")
    else:
        fail("单轮对话应只调用 1 次 LLM", f"实际 {call_count} 次")

    # 应有 agent_start / message_start / message_end / turn_end / agent_end
    event_types = [e.type for e in events]
    if "agent_start" in event_types and "agent_end" in event_types:
        ok("事件流包含 agent_start 和 agent_end")
    else:
        fail("事件流缺少必要事件", str(event_types))

    # messages 应包含 user prompt + assistant 响应
    if len(messages) == 2 and messages[1].content == "你好，我是 Agent":
        ok("messages 包含 user prompt 和 assistant 响应")
    else:
        fail("messages 结构异常", f"len={len(messages)}")


def test_agent_loop_tool_calls():
    """测试 16: agent_loop 工具调用 — 内层循环（工具 → 二次响应）"""
    section("单元测试 16: agent_loop 工具调用循环")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition,
    )

    call_count = 0

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮：请求工具调用
            return AgentMessage(
                role="assistant",
                content="让我计算一下",
                tool_calls=[{
                    "id": "tc-001",
                    "name": "calculator",
                    "arguments": {"expression": "1+1"},
                }],
                stop_reason="tool_use",
            )
        else:
            # 第二轮：工具返回后给出最终答案
            return AgentMessage(
                role="assistant",
                content="1+1=2",
                stop_reason="stop",
            )

    async def calc_handler(args):
        expr = args.get("expression", "")
        if expr == "1+1":
            return "2"
        return "unknown"

    calc_tool = ToolDefinition(
        name="calculator",
        description="计算器",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
        handler=calc_handler,
    )

    config = AgentLoopConfig(stream_fn=mock_stream_fn)
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[calc_tool],
    )
    prompts = [AgentMessage(role="user", content="1+1=?")]

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # LLM 应被调用 2 次（工具调用 + 最终回答）
    if call_count == 2:
        ok("工具调用触发 2 次 LLM 调用")
    else:
        fail("应调用 2 次 LLM", f"实际 {call_count} 次")

    # messages 应包含: user, assistant(tool_call), tool_result, assistant(final)
    if len(messages) == 4:
        ok("messages 包含 4 条消息 (user→assistant→tool_result→assistant)")
    else:
        fail("messages 结构异常", f"len={len(messages)}")

    # tool_result 消息内容应为 "2"
    tool_results = [m for m in messages if m.role == "tool_result"]
    if tool_results and tool_results[0].content == "2":
        ok("工具返回结果正确")
    else:
        fail("工具返回结果异常")


def test_transform_context_hook():
    """测试 17: transformContext 钩子 — 上下文转换（RAG 注入/过滤）"""
    section("单元测试 17: transformContext 钩子")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig,
    )

    transformed_messages = None
    original_count = 0

    async def mock_stream_fn(ctx, cfg):
        # 记录转换后的消息
        nonlocal transformed_messages
        transformed_messages = list(ctx.messages)
        return AgentMessage(role="assistant", content="ok", stop_reason="stop")

    async def transform_context(messages):
        # 模拟 RAG 注入：在消息列表前加一条系统注入消息
        nonlocal original_count
        original_count = len(messages)
        rag_msg = AgentMessage(role="user", content="[RAG] 相关文档: ...")
        return [rag_msg] + messages

    config = AgentLoopConfig(
        stream_fn=mock_stream_fn,
        transform_context=transform_context,
    )
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[],
    )
    prompts = [AgentMessage(role="user", content="问题")]

    asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # transform_context 应被调用，LLM 看到的消息应比原始多 1 条
    if transformed_messages and len(transformed_messages) == original_count + 1:
        ok("transformContext 注入了 1 条 RAG 消息")
    else:
        fail("transformContext 未正确工作",
             f"transformed={len(transformed_messages) if transformed_messages else 0}, original={original_count}")

    # 第一条应是 RAG 注入消息
    if transformed_messages and "[RAG]" in str(transformed_messages[0].content):
        ok("RAG 注入消息在首位")
    else:
        fail("RAG 消息应在首位")


def test_prepare_next_turn_hook():
    """测试 18: prepareNextTurn 钩子 — 模型热切换"""
    section("单元测试 18: prepareNextTurn 钩子 (模型热切换)")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig,
        ToolDefinition, NextTurnSnapshot,
    )

    call_count = 0
    models_seen = []

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        models_seen.append(ctx.model)
        if call_count == 1:
            return AgentMessage(
                role="assistant",
                content="调用工具",
                tool_calls=[{"id": "tc-1", "name": "echo", "arguments": {"text": "hi"}}],
                stop_reason="tool_use",
            )
        return AgentMessage(role="assistant", content="完成", stop_reason="stop")

    async def echo_handler(args):
        return args.get("text", "")

    echo_tool = ToolDefinition(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler,
    )

    async def prepare_next_turn(ctx, msg, tool_results):
        # 第一轮后切换到更强模型
        return NextTurnSnapshot(model="deepseek-reasoner", thinking_level="high")

    config = AgentLoopConfig(
        stream_fn=mock_stream_fn,
        prepare_next_turn=prepare_next_turn,
    )
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[echo_tool],
        model="deepseek-chat",
        thinking_level="medium",
    )
    prompts = [AgentMessage(role="user", content="测试")]

    asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # 第一次用 deepseek-chat，第二次应切换到 deepseek-reasoner
    if len(models_seen) == 2 and models_seen[0] == "deepseek-chat" and models_seen[1] == "deepseek-reasoner":
        ok("prepareNextTurn 正确切换模型", f"{models_seen[0]} → {models_seen[1]}")
    else:
        fail("模型切换异常", str(models_seen))


def test_should_stop_after_turn_hook():
    """测试 19: shouldStopAfterTurn 钩子 — 自定义停止策略"""
    section("单元测试 19: shouldStopAfterTurn 钩子 (自定义停止)")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition,
    )

    call_count = 0

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return AgentMessage(
                role="assistant",
                content="调用工具",
                tool_calls=[{"id": "tc-1", "name": "echo", "arguments": {"text": "hi"}}],
                stop_reason="tool_use",
            )
        return AgentMessage(role="assistant", content="继续", stop_reason="tool_use",
                            tool_calls=[{"id": "tc-2", "name": "echo", "arguments": {"text": "again"}}])

    async def echo_handler(args):
        return args.get("text", "")

    echo_tool = ToolDefinition(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler,
    )

    stop_called = False

    async def should_stop(msg, tool_results, ctx):
        nonlocal stop_called
        stop_called = True
        # 工具执行 1 次后就停止
        return True

    config = AgentLoopConfig(
        stream_fn=mock_stream_fn,
        should_stop_after_turn=should_stop,
    )
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[echo_tool],
    )
    prompts = [AgentMessage(role="user", content="测试")]

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    if stop_called:
        ok("shouldStopAfterTurn 钩子被调用")
    else:
        fail("shouldStopAfterTurn 应被调用")

    # LLM 只应被调用 1 次（工具执行后即停止）
    if call_count == 1:
        ok("shouldStopAfterTurn 正确停止循环 (仅 1 次 LLM 调用)")
    else:
        fail("应在 1 次后停止", f"实际调用 {call_count} 次")


def test_fail_tool_calls_truncated():
    """测试 20: failToolCallsFromTruncatedMessage — 截断容错"""
    section("单元测试 20: failToolCallsFromTruncatedMessage (截断容错)")

    from agent_loop import (
        fail_tool_calls_from_truncated_message, AgentEvent,
    )

    async def noop_emit(event):
        pass

    # 20a: 完整的 tool_call arguments
    complete_tc = {"id": "tc-1", "name": "calc", "arguments": {"expr": "1+1"}}
    batch = asyncio.new_event_loop().run_until_complete(
        fail_tool_calls_from_truncated_message([complete_tc], noop_emit)
    )
    if len(batch.results) == 1 and batch.results[0].is_error:
        ok("完整 tool_call 也被标记为错误 (截断场景)", f"result={batch.results[0].result[:50]}...")
    else:
        fail("截断容错应标记错误")

    # 20b: 不完整的 JSON arguments (字符串形式)
    truncated_tc = {"id": "tc-2", "name": "calc", "arguments": '{"expr": "1+'}  # 不完整 JSON
    from agent_loop import _is_tool_call_truncated
    is_trunc = _is_tool_call_truncated(truncated_tc)
    if is_trunc:
        ok("检测到不完整 JSON arguments (截断)")
    else:
        fail("应检测到截断")

    # 20c: 空 arguments
    empty_tc = {"id": "tc-3", "name": "calc", "arguments": None}
    is_trunc2 = _is_tool_call_truncated(empty_tc)
    if is_trunc2:
        ok("检测到空 arguments (截断)")
    else:
        fail("空 arguments 应判为截断")

    # 20d: 完整 dict arguments
    dict_tc = {"id": "tc-4", "name": "calc", "arguments": {"expr": "1+1"}}
    is_trunc3 = _is_tool_call_truncated(dict_tc)
    if not is_trunc3:
        ok("dict arguments 不被判定为截断")
    else:
        fail("dict arguments 不应判为截断")

    # 20e: 多个 tool_calls 批量处理
    multi_tcs = [
        {"id": "tc-5", "name": "calc", "arguments": {"a": 1}},
        {"id": "tc-6", "name": "echo", "arguments": {"b": 2}},
    ]
    batch2 = asyncio.new_event_loop().run_until_complete(
        fail_tool_calls_from_truncated_message(multi_tcs, noop_emit)
    )
    if len(batch2.results) == 2 and all(r.is_error for r in batch2.results):
        ok("批量截断容错处理 2 个 tool_calls")
    else:
        fail("批量处理异常")

    # 20f: 生成的 tool_result 消息有关联 ID
    if batch2.messages and batch2.messages[0].tool_call_id == "tc-5":
        ok("截断容错生成 tool_result 消息关联正确的 tool_call_id")
    else:
        fail("tool_result 消息关联 ID 异常")


def test_followup_queue():
    """测试 21: FollowUpQueue — 流后追加机制"""
    section("单元测试 21: FollowUpQueue (流后追加)")

    from agent_loop import FollowUpQueue

    # 21a: 基本入队
    fq = FollowUpQueue()
    success = fq.enqueue("继续处理下一步")
    if success:
        ok("FollowUpQueue 入队成功")
    else:
        fail("入队应成功")

    # 21b: 空文本不入队
    success2 = fq.enqueue("")
    if not success2:
        ok("空文本不入队")
    else:
        fail("空文本应被拒绝")

    success3 = fq.enqueue("   ")
    if not success3:
        ok("纯空白文本不入队")
    else:
        fail("纯空白文本应被拒绝")

    # 21c: drain 消费
    drained = asyncio.new_event_loop().run_until_complete(fq.drain())
    if len(drained) == 1 and drained[0].content == "继续处理下一步":
        ok("drain 消费 follow-up 成功")
    else:
        fail("drain 异常", f"len={len(drained)}")

    # 21d: drain 后队列为空
    drained2 = asyncio.new_event_loop().run_until_complete(fq.drain())
    if len(drained2) == 0:
        ok("drain 后队列为空")
    else:
        fail("drain 后队列应为空")

    # 21e: reject_pending — 流结束后拒绝未处理
    fq2 = FollowUpQueue()
    fq2.enqueue("follow-up 1")
    fq2.enqueue("follow-up 2")
    count = fq2.reject_pending("流已结束")
    if count == 2 and fq2.is_ended():
        ok("reject_pending 拒绝 2 条", f"count={count}")
    else:
        fail("reject_pending 异常", f"count={count}")

    # 21f: 流结束后入队被拒绝
    success4 = fq2.enqueue("不应入队")
    if not success4:
        ok("流结束后入队被拒绝")
    else:
        fail("流结束后应拒绝入队")


def test_followup_double_loop():
    """测试 22: 双层循环 — follow-up 驱动外层循环"""
    section("单元测试 22: 双层循环 (follow-up 外层循环)")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig, FollowUpQueue,
    )

    call_count = 0
    fq = FollowUpQueue()

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        return AgentMessage(role="assistant", content=f"回答 {call_count}", stop_reason="stop")

    async def get_follow_up_messages():
        return await fq.drain()

    config = AgentLoopConfig(
        stream_fn=mock_stream_fn,
        get_follow_up_messages=get_follow_up_messages,
    )
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[],
    )
    prompts = [AgentMessage(role="user", content="第一问")]

    # 预入队 2 条 follow-up
    fq.enqueue("第二问")
    fq.enqueue("第三问")

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # LLM 应被调用 2 次：第一问 + 2 条 follow-up 批量注入后 1 次
    # （pi.dev 设计: 所有 pending 消息在下一轮 assistant 响应前批量注入）
    if call_count == 2:
        ok("follow-up 驱动 2 次 LLM 调用 (1 初始 + 1 批量 follow-up)")
    else:
        fail("应调用 2 次 LLM", f"实际 {call_count} 次")

    # messages 应有 5 条：2 user(初始+follow-up1+follow-up2) + 2 assistant
    # 实际: user("第一问"), assistant("回答 1"), user("第二问"), user("第三问"), assistant("回答 2")
    if len(messages) == 5:
        ok("messages 包含 5 条 (初始 1 轮 + 批量 follow-up 1 轮)")
    else:
        fail("messages 结构异常", f"len={len(messages)}")

    # 检查 follow-up 消息内容
    user_msgs = [m for m in messages if m.role == "user"]
    if len(user_msgs) == 3 and user_msgs[1].content == "第二问" and user_msgs[2].content == "第三问":
        ok("follow-up 消息按顺序注入")
    else:
        fail("follow-up 消息顺序异常", str([m.content for m in user_msgs]))


def test_steer_injection_in_loop():
    """测试 23: steer 消息在循环中注入"""
    section("单元测试 23: steer 消息注入 Agent 循环")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition,
    )

    call_count = 0
    steer_consumed = False
    steer_texts_seen = []

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        # 记录 context 中是否有 steer 消息
        for m in ctx.messages:
            if "steer" in str(m.content).lower():
                steer_texts_seen.append(str(m.content))

        if call_count == 1:
            return AgentMessage(
                role="assistant",
                content="调用工具",
                tool_calls=[{"id": "tc-1", "name": "echo", "arguments": {"text": "hi"}}],
                stop_reason="tool_use",
            )
        return AgentMessage(role="assistant", content="完成", stop_reason="stop")

    async def echo_handler(args):
        return args.get("text", "")

    echo_tool = ToolDefinition(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler,
    )

    steer_calls = 0

    async def get_steering_messages():
        nonlocal steer_calls
        steer_calls += 1
        # 第一次调用返回 steer 消息，之后返回空
        if steer_calls == 1:
            return [AgentMessage(role="user", content="[STEER] 调整方向：更详细")]
        return []

    config = AgentLoopConfig(
        stream_fn=mock_stream_fn,
        get_steering_messages=get_steering_messages,
    )
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[echo_tool],
    )
    prompts = [AgentMessage(role="user", content="开始")]

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    if steer_texts_seen:
        ok("steer 消息被注入到 LLM context")
    else:
        fail("steer 消息未被注入")

    # messages 中应包含 steer 消息
    has_steer = any("STEER" in str(m.content) for m in messages)
    if has_steer:
        ok("steer 消息出现在最终 messages 列表中")
    else:
        fail("steer 消息未出现在 messages 中")


def test_agent_loop_continue():
    """测试 24: agent_loop_continue — 从现有上下文继续"""
    section("单元测试 24: agent_loop_continue (从现有上下文继续)")

    from agent_loop import (
        agent_loop_continue, AgentContext, AgentMessage, AgentLoopConfig,
    )

    async def mock_stream_fn(ctx, cfg):
        return AgentMessage(role="assistant", content="继续回答", stop_reason="stop")

    config = AgentLoopConfig(stream_fn=mock_stream_fn)

    # 上下文已有 user 消息（模拟之前的状态）
    context = AgentContext(
        system_prompt="你是助手",
        messages=[AgentMessage(role="user", content="之前的问题")],
        tools=[],
    )

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop_continue(context, config)
    )

    # 应生成 1 条 assistant 消息
    if len(messages) == 1 and messages[0].role == "assistant":
        ok("agent_loop_continue 正确生成 assistant 消息")
    else:
        fail("agent_loop_continue 异常", f"len={len(messages)}")

    # context.messages 应有 2 条（user + assistant）
    if len(context.messages) == 2:
        ok("context.messages 包含原有 + 新消息")
    else:
        fail("context.messages 异常", f"len={len(context.messages)}")

    # 24b: 从 assistant 消息继续应报错
    context2 = AgentContext(
        system_prompt="",
        messages=[AgentMessage(role="assistant", content="前一条")],
    )
    try:
        asyncio.new_event_loop().run_until_complete(
            agent_loop_continue(context2, config)
        )
        fail("从 assistant 消息继续应报错")
    except ValueError:
        ok("从 assistant 消息继续正确报错")

    # 24c: 空上下文继续应报错
    context3 = AgentContext(system_prompt="", messages=[])
    try:
        asyncio.new_event_loop().run_until_complete(
            agent_loop_continue(context3, config)
        )
        fail("空上下文继续应报错")
    except ValueError:
        ok("空上下文继续正确报错")


def test_followup_chunks():
    """测试 25: follow-up chunk 工厂函数"""
    section("单元测试 25: follow-up chunk 工厂函数")

    from stream import (
        create_followup_queued_chunk,
        create_followup_applied_chunk,
        create_followup_rejected_chunk,
    )

    # 25a: followup_queued
    queued = create_followup_queued_chunk("f1", "继续处理", 1)
    if queued["type"] == "followup_queued" and queued["followupId"] == "f1" and queued["queueSize"] == 1:
        ok("create_followup_queued_chunk 正确")
    else:
        fail("followup_queued chunk 结构错误")

    # 25b: followup_applied
    applied = create_followup_applied_chunk("f1", "继续处理", 2)
    if applied["type"] == "followup_applied" and applied["turnIndex"] == 2:
        ok("create_followup_applied_chunk 正确")
    else:
        fail("followup_applied chunk 结构错误")

    # 25c: followup_rejected
    rejected = create_followup_rejected_chunk("f1", "继续处理", "流已结束")
    if rejected["type"] == "followup_rejected" and rejected["reason"] == "流已结束":
        ok("create_followup_rejected_chunk 正确")
    else:
        fail("followup_rejected chunk 结构错误")


def test_tool_execution_error_handling():
    """测试 26: 工具执行错误处理 — 工具不存在/超时/异常"""
    section("单元测试 26: 工具执行错误处理")

    from agent_loop import (
        execute_tool_calls, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition,
    )

    async def noop_emit(event):
        pass

    # 26a: 工具不存在
    context = AgentContext(
        system_prompt="",
        messages=[],
        tools=[],  # 没有注册任何工具
    )
    msg = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[{"id": "tc-1", "name": "nonexistent", "arguments": {}}],
        stop_reason="tool_use",
    )
    config = AgentLoopConfig(stream_fn=None)  # 不会调用到
    batch = asyncio.new_event_loop().run_until_complete(
        execute_tool_calls(context, msg, config, None, noop_emit)
    )
    if len(batch.results) == 1 and batch.results[0].is_error and "not found" in batch.results[0].result:
        ok("工具不存在时返回错误结果")
    else:
        fail("工具不存在应返回错误")

    # 26b: 工具执行异常
    async def error_handler(args):
        raise RuntimeError("故意出错")

    error_tool = ToolDefinition(
        name="error_tool",
        description="总会出错的工具",
        parameters={"type": "object", "properties": {}},
        handler=error_handler,
    )
    context2 = AgentContext(
        system_prompt="",
        messages=[],
        tools=[error_tool],
    )
    msg2 = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[{"id": "tc-2", "name": "error_tool", "arguments": {}}],
        stop_reason="tool_use",
    )
    batch2 = asyncio.new_event_loop().run_until_complete(
        execute_tool_calls(context2, msg2, config, None, noop_emit)
    )
    if len(batch2.results) == 1 and batch2.results[0].is_error and "故意出错" in batch2.results[0].result:
        ok("工具异常时返回错误结果")
    else:
        fail("工具异常应返回错误")

    # 26c: 工具超时
    async def slow_handler(args):
        await asyncio.sleep(10)
        return "不应到达"

    slow_tool = ToolDefinition(
        name="slow_tool",
        description="慢工具",
        parameters={"type": "object", "properties": {}},
        handler=slow_handler,
    )
    context3 = AgentContext(
        system_prompt="",
        messages=[],
        tools=[slow_tool],
    )
    msg3 = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[{"id": "tc-3", "name": "slow_tool", "arguments": {}}],
        stop_reason="tool_use",
    )
    config_timeout = AgentLoopConfig(stream_fn=None, tool_timeout=0.1)
    batch3 = asyncio.new_event_loop().run_until_complete(
        execute_tool_calls(context3, msg3, config_timeout, None, noop_emit)
    )
    if len(batch3.results) == 1 and batch3.results[0].is_error and "timed out" in batch3.results[0].result:
        ok("工具超时返回错误结果")
    else:
        fail("工具超时应返回错误")

    # 26d: 并发执行多个工具
    async def fast_handler(args):
        return args.get("text", "")

    fast_tool = ToolDefinition(
        name="fast_tool",
        description="快工具",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=fast_handler,
    )
    context4 = AgentContext(
        system_prompt="",
        messages=[],
        tools=[fast_tool],
    )
    msg4 = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[
            {"id": "tc-4", "name": "fast_tool", "arguments": {"text": "a"}},
            {"id": "tc-5", "name": "fast_tool", "arguments": {"text": "b"}},
            {"id": "tc-6", "name": "fast_tool", "arguments": {"text": "c"}},
        ],
        stop_reason="tool_use",
    )
    batch4 = asyncio.new_event_loop().run_until_complete(
        execute_tool_calls(context4, msg4, config, None, noop_emit)
    )
    if len(batch4.results) == 3:
        results_text = sorted([r.result for r in batch4.results])
        if results_text == ["a", "b", "c"]:
            ok("并发执行 3 个工具全部成功")
        else:
            fail("并发工具结果异常", str(results_text))
    else:
        fail("应并发执行 3 个工具", f"实际 {len(batch4.results)}")

    # 26e: 真实并发性验证 — 3 个 sleep(0.3) 工具，并发 < 0.6s，串行 > 0.8s
    import time as _time
    from agent_loop import execute_tool_calls, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition

    async def slow_io_handler(args):
        """模拟 I/O 密集型工具（网络请求/数据库查询）"""
        await asyncio.sleep(0.3)
        return args.get("id", "?")

    slow_io_tool = ToolDefinition(
        name="slow_io",
        description="慢 I/O 工具",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}},
        handler=slow_io_handler,
    )
    context_conc = AgentContext(
        system_prompt="",
        messages=[],
        tools=[slow_io_tool],
    )
    msg_conc = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[
            {"id": "tc-a", "name": "slow_io", "arguments": {"id": "a"}},
            {"id": "tc-b", "name": "slow_io", "arguments": {"id": "b"}},
            {"id": "tc-c", "name": "slow_io", "arguments": {"id": "c"}},
        ],
        stop_reason="tool_use",
    )
    config_conc = AgentLoopConfig(stream_fn=None, tool_timeout=5.0)

    async def noop_emit_conc(event):
        pass

    start = _time.monotonic()
    batch_conc = asyncio.new_event_loop().run_until_complete(
        execute_tool_calls(context_conc, msg_conc, config_conc, None, noop_emit_conc)
    )
    elapsed = _time.monotonic() - start

    # 并发: 3 * 0.3s ≈ 0.3s（允许调度开销到 0.6s）
    # 串行: 3 * 0.3s = 0.9s
    if len(batch_conc.results) == 3 and elapsed < 0.6:
        ok(f"真实并发: 3 个 sleep(0.3) 工具总耗时 {elapsed:.2f}s (< 0.6s)")
    elif len(batch_conc.results) == 3 and elapsed >= 0.8:
        fail(f"工具疑似串行执行: 总耗时 {elapsed:.2f}s (>= 0.8s)")
    else:
        fail("并发性测试异常", f"results={len(batch_conc.results)}, elapsed={elapsed:.2f}s")


def test_truncated_tool_call_in_loop():
    """测试 27: 截断容错在完整循环中的表现"""
    section("单元测试 27: 截断容错在完整循环中")

    from agent_loop import (
        agent_loop, AgentContext, AgentMessage, AgentLoopConfig, ToolDefinition,
    )

    call_count = 0

    async def mock_stream_fn(ctx, cfg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮：输出被截断（stop_reason=length）
            return AgentMessage(
                role="assistant",
                content="让我调用工",
                tool_calls=[{"id": "tc-1", "name": "echo", "arguments": {"text": "truncated"}}],
                stop_reason="length",
            )
        # 第二轮：模型重新生成，正常完成
        return AgentMessage(role="assistant", content="已完成", stop_reason="stop")

    async def echo_handler(args):
        return args.get("text", "")

    echo_tool = ToolDefinition(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler,
    )

    config = AgentLoopConfig(stream_fn=mock_stream_fn)
    context = AgentContext(
        system_prompt="你是助手",
        messages=[],
        tools=[echo_tool],
    )
    prompts = [AgentMessage(role="user", content="测试截断")]

    events, messages = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts, context, config)
    )

    # LLM 应被调用 2 次：第一次截断（tool_call 被标记错误），第二次正常
    if call_count == 2:
        ok("截断后模型重新生成 (2 次 LLM 调用)")
    else:
        fail("截断后应重新调用 LLM", f"实际 {call_count} 次")

    # 应有 tool_result 消息且 is_error
    tool_results = [m for m in messages if m.role == "tool_result"]
    if tool_results and "truncated" in tool_results[0].content.lower() or "truncat" in tool_results[0].content.lower():
        ok("截断 tool_call 生成错误 tool_result")
    else:
        # 检查是否包含截断相关信息
        if tool_results and "truncat" in str(tool_results[0].content).lower():
            ok("截断 tool_result 包含截断信息")
        else:
            fail("截断 tool_result 异常", str(tool_results[0].content[:80] if tool_results else "无 tool_result"))

    # 27b: 截断后 LLM 重新发出 tool_call 并被执行（完整恢复链路）
    section("单元测试 27b: 截断后重新发出工具调用")
    call_count_b = 0
    tool_executed_b = False

    async def mock_stream_fn_b(ctx, cfg):
        nonlocal call_count_b
        call_count_b += 1
        if call_count_b == 1:
            # 第一轮: 截断 — tool_call arguments 不完整
            return AgentMessage(
                role="assistant",
                content="",
                tool_calls=[{"id": "tc-trunc", "name": "echo", "arguments": '{"text": "incom'}],
                stop_reason="length",
            )
        elif call_count_b == 2:
            # 第二轮: LLM 看到错误 tool_result，重新发出完整 tool_call
            return AgentMessage(
                role="assistant",
                content="重新调用工具",
                tool_calls=[{"id": "tc-ok", "name": "echo", "arguments": {"text": "recovered"}}],
                stop_reason="tool_use",
            )
        else:
            # 第三轮: 工具执行后给出最终答案
            return AgentMessage(role="assistant", content="恢复完成", stop_reason="stop")

    async def echo_handler_b(args):
        nonlocal tool_executed_b
        tool_executed_b = True
        return args.get("text", "")

    echo_tool_b = ToolDefinition(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo_handler_b,
    )
    config_b = AgentLoopConfig(stream_fn=mock_stream_fn_b)
    context_b = AgentContext(system_prompt="你是助手", messages=[], tools=[echo_tool_b])
    prompts_b = [AgentMessage(role="user", content="测试截断恢复")]

    events_b, messages_b = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts_b, context_b, config_b)
    )

    # LLM 应被调用 3 次: 截断 → 重新发出工具 → 最终回答
    if call_count_b == 3:
        ok("截断后 LLM 重新发出 tool_call 并最终回答 (3 次调用)")
    else:
        fail("截断恢复应调用 3 次 LLM", f"实际 {call_count_b} 次")

    # 工具应被执行（第二轮的完整 tool_call）
    if tool_executed_b:
        ok("截断后重新发出的 tool_call 被成功执行")
    else:
        fail("截断后重新发出的 tool_call 应被执行")

    # messages 应包含: user, assistant(trunc), tool_result(error), assistant(retry), tool_result(ok), assistant(final)
    tool_results_b = [m for m in messages_b if m.role == "tool_result"]
    if len(tool_results_b) == 2:
        # 第一个 tool_result 应为错误（截断），第二个应为成功
        if "truncated" in tool_results_b[0].content.lower() and tool_results_b[1].content == "recovered":
            ok("截断 tool_result + 恢复 tool_result 共存且顺序正确")
        else:
            fail("tool_result 内容异常",
                 f"[0]={tool_results_b[0].content[:40]}, [1]={tool_results_b[1].content[:40]}")
    else:
        fail("应有 2 个 tool_result", f"实际 {len(tool_results_b)}")

    # 27c: 连续截断后恢复（防止无限循环或提前退出）
    section("单元测试 27c: 连续截断后恢复")
    call_count_c = 0

    async def mock_stream_fn_c(ctx, cfg):
        nonlocal call_count_c
        call_count_c += 1
        if call_count_c <= 2:
            # 前两次都截断
            return AgentMessage(
                role="assistant",
                content="",
                tool_calls=[{"id": f"tc-trunc-{call_count_c}", "name": "echo", "arguments": '{"text": "incom'}],
                stop_reason="length",
            )
        elif call_count_c == 3:
            # 第三次正常发出工具调用
            return AgentMessage(
                role="assistant",
                content="",
                tool_calls=[{"id": "tc-ok-c", "name": "echo", "arguments": {"text": "ok"}}],
                stop_reason="tool_use",
            )
        else:
            return AgentMessage(role="assistant", content="完成", stop_reason="stop")

    config_c = AgentLoopConfig(stream_fn=mock_stream_fn_c)
    context_c = AgentContext(system_prompt="", messages=[], tools=[echo_tool_b])
    prompts_c = [AgentMessage(role="user", content="测试连续截断")]

    events_c, messages_c = asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts_c, context_c, config_c)
    )

    # LLM 应被调用 4 次: 截断 → 截断 → 工具调用 → 最终回答
    if call_count_c == 4:
        ok("连续 2 次截断后成功恢复 (4 次 LLM 调用)")
    else:
        fail("连续截断恢复应调用 4 次 LLM", f"实际 {call_count_c} 次")

    # 应有 3 个 tool_result: 2 个截断错误 + 1 个成功
    tool_results_c = [m for m in messages_c if m.role == "tool_result"]
    error_count = sum(1 for r in tool_results_c if "truncated" in r.content.lower())
    ok_count = sum(1 for r in tool_results_c if r.content == "ok")
    if len(tool_results_c) == 3 and error_count == 2 and ok_count == 1:
        ok("连续截断生成 2 个错误 + 1 个成功 tool_result")
    else:
        fail("tool_result 结构异常",
             f"total={len(tool_results_c)}, errors={error_count}, ok={ok_count}")

    # 27d: 截断错误 tool_result 确实进入 context.messages 供 LLM 看到
    section("单元测试 27d: 截断错误进入 context 供 LLM 可见")
    call_count_d = 0
    llm_seen_truncation_error = False

    async def mock_stream_fn_d(ctx, cfg):
        nonlocal call_count_d, llm_seen_truncation_error
        call_count_d += 1
        if call_count_d == 1:
            return AgentMessage(
                role="assistant",
                content="",
                tool_calls=[{"id": "tc-trunc-d", "name": "echo", "arguments": '{"text": "bad'}],
                stop_reason="length",
            )
        else:
            # 第二轮: 检查 context.messages 是否包含截断错误信息
            for m in ctx.messages:
                if "truncated" in str(m.content).lower():
                    llm_seen_truncation_error = True
                    break
            return AgentMessage(role="assistant", content="看到错误了", stop_reason="stop")

    config_d = AgentLoopConfig(stream_fn=mock_stream_fn_d)
    context_d = AgentContext(system_prompt="", messages=[], tools=[echo_tool_b])
    prompts_d = [AgentMessage(role="user", content="测试")]

    asyncio.new_event_loop().run_until_complete(
        agent_loop(prompts_d, context_d, config_d)
    )

    if llm_seen_truncation_error:
        ok("截断错误 tool_result 进入 context.messages，LLM 第二轮可见")
    else:
        fail("截断错误应进入 context 供 LLM 看到")


# ════════════════════════════════════════════════════════
#  Chat Orchestrator 集成测试 (阶段一: agent_loop 迁移)
# ════════════════════════════════════════════════════════

def test_orchestrator_message_conversion():
    """测试 28: 消息格式双向转换 — OpenAI ↔ AgentMessage"""
    section("单元测试 28: 消息格式转换 (_convert_to_agent_messages / _agent_message_to_openai)")

    from chat_orchestrator import _convert_to_agent_messages, _agent_message_to_openai
    from agent_loop import AgentMessage

    # 构建 OpenAI 格式消息列表
    openai_messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！", "tool_calls": [
            {"id": "tc-1", "type": "function", "function": {"name": "calc", "arguments": '{"expr": "1+1"}'}},
        ]},
        {"role": "tool", "tool_call_id": "tc-1", "content": '{"result": 2}'},
        {"role": "assistant", "content": "1+1=2"},
    ]

    # 转为 AgentMessage
    agent_msgs = _convert_to_agent_messages(openai_messages)

    if len(agent_msgs) == 4:
        ok("转换后消息数量正确 (4 条)")
    else:
        fail("消息数量异常", f"期望 4，实际 {len(agent_msgs)}")
        return

    if agent_msgs[0].role == "user" and agent_msgs[0].content == "你好":
        ok("user 消息转换正确")
    else:
        fail("user 消息转换异常")

    if agent_msgs[1].role == "assistant" and agent_msgs[1].tool_calls:
        tc = agent_msgs[1].tool_calls[0]
        if tc.get("name") == "calc" and tc.get("arguments") == {"expr": "1+1"}:
            ok("assistant tool_calls 转换正确 (arguments 已解析为 dict)")
        else:
            fail("tool_calls 转换异常", str(tc))
    else:
        fail("assistant 消息应包含 tool_calls")

    if agent_msgs[2].role == "tool_result" and agent_msgs[2].tool_call_id == "tc-1":
        ok("tool_result 消息转换正确")
    else:
        fail("tool_result 消息转换异常")

    # 反向转换: AgentMessage → OpenAI
    openai_msg = _agent_message_to_openai(agent_msgs[1])
    if openai_msg.get("role") == "assistant" and openai_msg.get("tool_calls"):
        fn = openai_msg["tool_calls"][0]["function"]
        if fn["name"] == "calc" and isinstance(fn["arguments"], str):
            ok("反向转换: tool_calls arguments 序列化为字符串")
        else:
            fail("反向转换: arguments 应为 JSON 字符串", str(fn))
    else:
        fail("反向转换异常")

    # tool_result 反向转换
    tool_openai = _agent_message_to_openai(agent_msgs[2])
    if tool_openai.get("role") == "tool" and tool_openai.get("tool_call_id") == "tc-1":
        ok("tool_result 反向转换正确")
    else:
        fail("tool_result 反向转换异常")


def test_orchestrator_stop_reason_mapping():
    """测试 29: stop_reason 映射"""
    section("单元测试 29: stop_reason 映射 (_map_stop_reason)")

    from chat_orchestrator import _map_stop_reason

    cases = [
        ("stop", "stop"),
        ("tool_calls", "tool_use"),
        ("length", "length"),
        ("content_filter", "error"),
        (None, "stop"),
        ("unknown", "stop"),
    ]

    all_pass = True
    for finish_reason, expected in cases:
        result = _map_stop_reason(finish_reason)
        if result == expected:
            pass  # 个别 OK 不打印
        else:
            fail(f"stop_reason 映射: {finish_reason} → {result} (期望 {expected})")
            all_pass = False

    if all_pass:
        ok("所有 stop_reason 映射正确 (6 个 case)")


def test_orchestrator_build_tool_definitions():
    """测试 30: 工具定义桥接 — tool_registry → ToolDefinition"""
    section("单元测试 30: 工具定义桥接 (_build_tool_definitions)")

    from chat_orchestrator import _build_tool_definitions
    import tools  # noqa: F401 — 触发工具注册

    # 用真实注册的工具测试
    tool_names = ["calculator", "datetime"]
    ctx = {"clientIP": "127.0.0.1"}
    defs = _build_tool_definitions(tool_names, ctx)

    if len(defs) == 2:
        ok("桥接了 2 个工具定义")
    else:
        fail("工具数量异常", f"期望 2，实际 {len(defs)}")
        return

    if defs[0].name == "calculator" and defs[0].handler:
        ok("calculator 工具定义正确 (含 handler)")
    else:
        fail("calculator 工具定义异常")

    if defs[1].name == "datetime" and defs[1].handler:
        ok("datetime 工具定义正确 (含 handler)")
    else:
        fail("datetime 工具定义异常")

    # 测试 handler 可调用
    async def _test_handler():
        result = await defs[0].handler({"expression": "2+3"})
        import json as _json
        parsed = _json.loads(result)
        if parsed.get("result") == 5:
            ok("calculator handler 执行成功 (2+3=5)")
        else:
            fail("calculator handler 结果异常", result)
    asyncio.new_event_loop().run_until_complete(_test_handler())

    # 测试不存在的工具名
    defs_unknown = _build_tool_definitions(["nonexistent_tool"], ctx)
    if len(defs_unknown) == 0:
        ok("不存在的工具名被正确跳过")
    else:
        fail("不存在的工具名应被跳过")


def test_orchestrator_should_stop():
    """测试 31: should_stop_after_turn 策略"""
    section("单元测试 31: 停止策略 (_check_should_stop)")

    from chat_orchestrator import _check_should_stop, MAX_TOOL_CALLS
    from agent_loop import AgentMessage, ToolCallResult
    from stream import StreamLifecycle, StreamWriter

    # 准备 lifecycle
    writer = StreamWriter()
    lifecycle = StreamLifecycle(writer)

    # Case 1: 无 tool_results → 不停止
    tracker1 = {"count": 0}
    msg = AgentMessage(role="assistant", content="hello")
    if _check_should_stop(msg, [], None, "auto", lifecycle, tracker1) is False:
        ok("无 tool_results 时不停止")
    else:
        fail("无 tool_results 时不应停止")

    # Case 2: auto policy + 未超限 → 不停止
    tracker2 = {"count": 0}
    tr = ToolCallResult(tool_call_id="tc-1", tool_name="calc", args={}, result="{}")
    if _check_should_stop(msg, [tr], None, "auto", lifecycle, tracker2) is False:
        ok("auto policy + 未超限时不停止")
    else:
        fail("auto policy + 未超限时不应停止")

    # Case 3: 超过 MAX_TOOL_CALLS → 停止
    tracker3 = {"count": MAX_TOOL_CALLS - 1}
    if _check_should_stop(msg, [tr], None, "auto", lifecycle, tracker3) is True:
        ok("超过 MAX_TOOL_CALLS 时停止")
    else:
        fail("超过 MAX_TOOL_CALLS 时应停止")

    # Case 4: tool-first policy + 无 authoritative → 不停止
    tracker4 = {"count": 0}
    if _check_should_stop(msg, [tr], None, "tool-first", lifecycle, tracker4) is False:
        ok("tool-first policy + 无 authoritative 时不停止")
    else:
        fail("tool-first policy + 无 authoritative 时不应停止")


def test_orchestrator_emit_event():
    """测试 32: AgentEvent → NDJSON chunk 转换"""
    section("单元测试 32: 事件→chunk 转换 (_emit_event_to_stream)")

    from chat_orchestrator import _emit_event_to_stream
    from agent_loop import AgentMessage, AgentEvent, ToolCallResult
    from stream import StreamLifecycle, StreamWriter

    writer = StreamWriter()
    lifecycle = StreamLifecycle(writer)

    # Case 1: message_start 带 content → text chunk
    lifecycle._started = True  # 绕过 start 检查
    _emit_event_to_stream(AgentEvent(
        type="message_start",
        message=AgentMessage(role="assistant", content="你好"),
    ), lifecycle)
    chunks = writer.get_chunks()
    if chunks and chunks[-1].get("type") == "text" and chunks[-1].get("content") == "你好":
        ok("message_start (content) → text chunk")
    else:
        fail("message_start 应生成 text chunk")

    # Case 2: message_start 带 tool_calls → tool_call chunk
    _emit_event_to_stream(AgentEvent(
        type="message_start",
        message=AgentMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "tc-1", "name": "calc", "arguments": {"expr": "1+1"}}],
        ),
    ), lifecycle)
    chunks = writer.get_chunks()
    tc_chunks = [c for c in chunks if c.get("type") == "tool_call"]
    if tc_chunks and tc_chunks[-1].get("toolName") == "calc":
        ok("message_start (tool_calls) → tool_call chunk")
    else:
        fail("message_start 应生成 tool_call chunk")

    # Case 3: turn_end 带 tool_results → tool_result chunk
    _emit_event_to_stream(AgentEvent(
        type="turn_end",
        message=AgentMessage(role="assistant", content=""),
        tool_results=[ToolCallResult(
            tool_call_id="tc-1", tool_name="calc", args={}, result='{"result":2}',
        )],
    ), lifecycle)
    chunks = writer.get_chunks()
    tr_chunks = [c for c in chunks if c.get("type") == "tool_result"]
    if tr_chunks and tr_chunks[-1].get("toolResult") == '{"result":2}':
        ok("turn_end (tool_results) → tool_result chunk")
    else:
        fail("turn_end 应生成 tool_result chunk")

    # Case 4: agent_start / turn_start → 忽略
    before = len(writer.get_chunks())
    _emit_event_to_stream(AgentEvent(type="agent_start"), lifecycle)
    _emit_event_to_stream(AgentEvent(type="turn_start"), lifecycle)
    after = len(writer.get_chunks())
    if after == before:
        ok("agent_start / turn_start 正确忽略")
    else:
        fail("agent_start / turn_start 应被忽略")


def test_orchestrator_full_loop_mock():
    """测试 33: 完整 _do_orchestrate 流程 (mock LLM)"""
    section("单元测试 33: _do_orchestrate 完整流程 (mock stream_fn)")

    import chat_orchestrator as orch
    from agent_loop import AgentMessage
    from stream import StreamLifecycle, StreamWriter

    # Mock chat_completion — 模拟 LLM 先调用工具再回答
    call_count = 0

    class MockChoice:
        def __init__(self, content, tool_calls=None, finish_reason="stop"):
            self.message = type("M", (), {
                "content": content,
                "tool_calls": tool_calls,
            })()
            self.finish_reason = finish_reason

    class MockResponse:
        def __init__(self, choice):
            self.choices = [choice]

    class MockTC:
        def __init__(self, tc_id, name, args):
            self.id = tc_id
            self.function = type("F", (), {"name": name, "arguments": args})()

    async def mock_chat_completion(messages, tools=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一轮: LLM 调用 calculator
            return MockResponse(MockChoice(
                content="",
                tool_calls=[MockTC("tc-1", "calculator", '{"expression": "2+3"}')],
                finish_reason="tool_calls",
            ))
        else:
            # 第二轮: LLM 看到工具结果，生成总结
            return MockResponse(MockChoice(
                content="2+3 的结果是 5。",
                finish_reason="stop",
            ))

    # 临时替换 chat_completion
    original_chat = orch.chat_completion
    orch.chat_completion = mock_chat_completion

    try:
        # 构建 mock session
        class MockSkill:
            system_prompt = "你是计算助手"
            tool_names = ["calculator"]
            result_policy = "auto"
            output_policy = "concise-utility"

        class MockSession:
            def get_messages(self):
                return [{"role": "user", "content": "计算 2+3"}]
            def get_skill_id(self):
                return "utility-skill"
            def get_system_prompt(self):
                return MockSkill.system_prompt

        # Mock skill_registry
        original_get_skill = orch.skill_registry.get
        orch.skill_registry.get = lambda sid: MockSkill()

        writer = StreamWriter()
        lifecycle = StreamLifecycle(writer)
        lifecycle._started = True  # 绕过 start 检查

        asyncio.new_event_loop().run_until_complete(
            orch._do_orchestrate(MockSession(), writer, {}, lifecycle)
        )

        # 恢复
        orch.skill_registry.get = original_get_skill

        chunks = writer.get_chunks()

        # 验证: 应有 text chunk (LLM 总结)
        text_chunks = [c for c in chunks if c.get("type") == "text"]
        if text_chunks and "5" in text_chunks[-1].get("content", ""):
            ok("LLM 生成了包含工具结果的总结文本")
        else:
            fail("应有包含 '5' 的文本", str([c.get("content", "")[:40] for c in text_chunks]))

        # 验证: 应有 tool_call chunk
        tc_chunks = [c for c in chunks if c.get("type") == "tool_call"]
        if tc_chunks and tc_chunks[0].get("toolName") == "calculator":
            ok("生成了 calculator 的 tool_call chunk")
        else:
            fail("应有 calculator tool_call chunk")

        # 验证: 应有 tool_result chunk
        tr_chunks = [c for c in chunks if c.get("type") == "tool_result"]
        if tr_chunks and "5" in tr_chunks[0].get("toolResult", ""):
            ok("生成了包含结果 '5' 的 tool_result chunk")
        else:
            fail("应有包含 '5' 的 tool_result chunk")

        # 验证: 应有 done chunk
        done_chunks = [c for c in chunks if c.get("type") == "done"]
        if done_chunks:
            ok("流正常结束 (done)")
        else:
            fail("应有 done chunk")

        # 验证: LLM 被调用 2 次 (第一次工具调用, 第二次总结)
        if call_count == 2:
            ok("LLM 被调用 2 次 (工具调用 + 总结)")
        else:
            fail("LLM 调用次数异常", f"期望 2，实际 {call_count}")

        # 33b: 总结是 LLM 第二轮自然生成的，而非 _generate_summary_answer 拼接
        # 关键证据: 第二轮 LLM 调用时，context.messages 中应包含 tool_result
        # 且第二轮返回的 content 就是总结文本本身（非手动拼接）
        if not hasattr(mock_chat_completion, "_second_round_messages"):
            # 用闭包变量记录第二轮 LLM 看到的消息
            pass

    finally:
        orch.chat_completion = original_chat

    # 33b: 独立验证 — 总结文本来自 LLM 第二轮，而非手动拼接
    section("单元测试 33b: 总结自然生成 (非 _generate_summary_answer 拼接)")
    import chat_orchestrator as orch2
    from agent_loop import AgentMessage as AM2
    from stream import StreamLifecycle as SL2, StreamWriter as SW2

    # 确认旧函数 _generate_summary_answer 已从代码中彻底移除
    assert not hasattr(orch2, "_generate_summary_answer"), "_generate_summary_answer 应已被删除"
    ok("_generate_summary_answer 已从 chat_orchestrator 中移除")

    call_count_b = 0
    second_round_messages = None  # 记录第二轮 LLM 看到的消息

    class MockChoice2:
        def __init__(self, content, tool_calls=None, finish_reason="stop"):
            self.message = type("M", (), {"content": content, "tool_calls": tool_calls})()
            self.finish_reason = finish_reason

    class MockResponse2:
        def __init__(self, choice):
            self.choices = [choice]

    class MockTC2:
        def __init__(self, tc_id, name, args):
            self.id = tc_id
            self.function = type("F", (), {"name": name, "arguments": args})()

    async def mock_chat_completion_b(messages, tools=None, **kwargs):
        nonlocal call_count_b, second_round_messages
        call_count_b += 1
        if call_count_b == 2:
            # 记录第二轮 LLM 看到的完整消息列表
            second_round_messages = list(messages) if messages else []
        if call_count_b == 1:
            return MockResponse2(MockChoice2(
                content="",
                tool_calls=[MockTC2("tc-1", "calculator", '{"expression": "7*8"}')],
                finish_reason="tool_calls",
            ))
        else:
            # 第二轮: LLM 自然生成总结（模拟真实 LLM 行为）
            return MockResponse2(MockChoice2(
                content="7 乘以 8 等于 56。",
                finish_reason="stop",
            ))

    original_chat_b = orch2.chat_completion
    orch2.chat_completion = mock_chat_completion_b

    try:
        class MockSkill2:
            system_prompt = "你是计算助手"
            tool_names = ["calculator"]
            result_policy = "auto"
            output_policy = "concise-utility"

        class MockSession2:
            def get_messages(self):
                return [{"role": "user", "content": "计算 7*8"}]
            def get_skill_id(self):
                return "utility-skill"
            def get_system_prompt(self):
                return MockSkill2.system_prompt

        original_get_skill_b = orch2.skill_registry.get
        orch2.skill_registry.get = lambda sid: MockSkill2()

        writer_b = SW2()
        lc_b = SL2(writer_b)
        lc_b._started = True

        asyncio.new_event_loop().run_until_complete(
            orch2._do_orchestrate(MockSession2(), writer_b, {}, lc_b)
        )
        orch2.skill_registry.get = original_get_skill_b

        # 验证 1: 第二轮 LLM 看到的消息中包含 tool_result
        if second_round_messages:
            has_tool_result = any(
                (isinstance(m, dict) and m.get("role") == "tool")
                or (hasattr(m, "role") and m.role == "tool_result")
                for m in second_round_messages
            )
            if has_tool_result:
                ok("第二轮 LLM 调用时 context 包含 tool_result 消息")
            else:
                roles = [m.get("role") if isinstance(m, dict) else getattr(m, "role", "?") for m in second_round_messages]
                fail("第二轮 context 应包含 tool_result", f"roles={roles}")
        else:
            fail("应记录到第二轮 LLM 消息")

        # 验证 2: 最终文本就是 LLM 第二轮返回的 content（非手动拼接）
        text_chunks_b = [c for c in writer_b.get_chunks() if c.get("type") == "text"]
        if text_chunks_b:
            final_text = text_chunks_b[-1].get("content", "")
            # LLM 第二轮返回的 content 是 "7 乘以 8 等于 56。"
            # 如果是旧版 _generate_summary_answer，会是手动拼接的格式（如 "工具结果: 56"）
            if final_text == "7 乘以 8 等于 56。":
                ok("总结文本 = LLM 第二轮返回的 content (自然生成，非拼接)")
            else:
                fail("总结文本应等于 LLM 第二轮 content", f"actual='{final_text}'")
        else:
            fail("应有 text chunk")

        # 验证 3: 总结文本不是 tool_result 的原始内容（证明经过 LLM 加工）
        tr_chunks_b = [c for c in writer_b.get_chunks() if c.get("type") == "tool_result"]
        if tr_chunks_b and text_chunks_b:
            tool_raw = tr_chunks_b[0].get("toolResult", "")
            summary = text_chunks_b[-1].get("content", "")
            # tool_result 是 JSON 格式 '{"result": 56}'，总结是自然语言 "7 乘以 8 等于 56。"
            if tool_raw != summary and "等于" in summary and "result" in tool_raw:
                ok("总结是自然语言 (非 tool_result 原始 JSON)")
            else:
                fail("总结应与 tool_result 不同", f"tool='{tool_raw}', summary='{summary}'")

    finally:
        orch2.chat_completion = original_chat_b


# ════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════
#  DuckDB 持久化测试
# ════════════════════════════════════════════════════════

def test_duckdb_persistence_basic():
    """测试 34: DuckDBPersistence 基本功能"""
    section("单元测试 34: DuckDBPersistence 基本功能")
    import tempfile
    import os as _os
    from duckdb_store import DuckDBPersistence

    # 使用临时文件
    db_path = _os.path.join(tempfile.gettempdir(), f"test_duckdb_{int(time.time() * 1000)}.duckdb")
    persistence = DuckDBPersistence(db_path)

    # 34a: 初始化 + 建表
    if persistence.is_enabled:
        ok("DuckDBPersistence 初始化成功")
    else:
        fail("DuckDBPersistence 应初始化成功")
        return

    try:
        # 34b: UserMemory 写入 + 读取
        memory_data = {
            "stableKey": "mem-test-001",
            "text": "用户不吃香菜",
            "tags": ["饮食", "忌口"],
            "polarity": "avoid",
            "status": "active",
            "confidence": 0.9,
            "sourceConversationId": "conv-001",
            "reason": "用户明确表示",
            "memoryType": "preference",
            "subject": "饮食",
            "facet": "香菜",
            "semantic": {"embeddingModelId": "text-embedding-3-small", "semanticIndexVersion": "v1"},
            "embedding": [0.1, 0.2, 0.3, 0.4, 0.5],
            "createdAt": time.time(),
            "updatedAt": time.time(),
        }
        persistence.save_memory("ns-test", memory_data)

        loaded = persistence.load_memories("ns-test")
        if len(loaded) == 1 and loaded[0]["text"] == "用户不吃香菜":
            ok("UserMemory 写入 + 读取成功")
        else:
            fail("UserMemory 读写异常", str(loaded)[:80])

        # 34c: 向量持久化
        if loaded and loaded[0].get("embedding") == [0.1, 0.2, 0.3, 0.4, 0.5]:
            ok("DOUBLE[] 向量持久化正确")
        else:
            fail("向量持久化异常", str(loaded[0].get("embedding")))

        # 34d: tags 持久化 (VARCHAR[])
        if loaded and loaded[0].get("tags") == ["饮食", "忌口"]:
            ok("VARCHAR[] tags 持久化正确")
        else:
            fail("tags 持久化异常", str(loaded[0].get("tags")))

        # 34e: semantic JSON 持久化
        if loaded and loaded[0].get("semantic", {}).get("embeddingModelId") == "text-embedding-3-small":
            ok("JSON semantic 持久化正确")
        else:
            fail("semantic 持久化异常", str(loaded[0].get("semantic")))

        # 34f: 多条记忆
        for i in range(5):
            persistence.save_memory("ns-test", {
                "stableKey": f"mem-{i}",
                "text": f"记忆 {i}",
                "tags": [f"tag{i}"],
                "status": "active",
                "confidence": 0.7,
                "embedding": [float(i)] * 5,
            })
        loaded_multi = persistence.load_memories("ns-test")
        if len(loaded_multi) == 6:  # 1 + 5
            ok(f"多条记忆持久化 ({len(loaded_multi)} 条)")
        else:
            fail("多条记忆异常", f"期望 6，实际 {len(loaded_multi)}")

        # 34g: 删除记忆
        persistence.delete_memory("ns-test", "mem-test-001")
        loaded_after_del = persistence.load_memories("ns-test")
        if len(loaded_after_del) == 5:
            ok("删除单条记忆成功")
        else:
            fail("删除异常", f"期望 5，实际 {len(loaded_after_del)}")

        # 34h: ThreadState 写入 + 读取
        persistence.save_thread_state(
            thread_id="thread-001",
            summary="这是对话摘要",
            pinned_decisions=["决策一", "决策二"],
            last_compacted_at=1234567890.0,
            messages_count_at_last_compact=4,
        )
        ts_data = persistence.load_thread_state("thread-001")
        if (ts_data and ts_data["summary"] == "这是对话摘要"
                and ts_data["pinned_decisions"] == ["决策一", "决策二"]
                and ts_data["messages_count_at_last_compact"] == 4):
            ok("ThreadState 写入 + 读取成功")
        else:
            fail("ThreadState 读写异常", str(ts_data)[:80])

        # 34i: ThreadMessages 写入 + 读取
        persistence.save_thread_messages("thread-001", [
            {"id": "msg-1", "role": "user", "text": "你好", "created_at": time.time()},
            {"id": "msg-2", "role": "assistant", "text": "你好！", "created_at": time.time()},
            {"id": "msg-3", "role": "user", "text": "计算 1+1", "created_at": time.time()},
        ])
        msgs = persistence.load_thread_messages("thread-001")
        if len(msgs) == 3 and msgs[0]["text"] == "你好" and msgs[2]["text"] == "计算 1+1":
            ok("ThreadMessages 写入 + 读取成功 (按 seq 排序)")
        else:
            fail("ThreadMessages 读写异常", str(msgs)[:80])

        # 34j: Conversation 写入 + 读取
        persistence.save_conversation(
            session_id="sess-001",
            conversation_id="conv-001",
            thread_id="thread-001",
            title="测试对话",
            last_active_at=time.time(),
            has_messages=True,
        )
        convs = persistence.load_conversations("sess-001")
        if len(convs) == 1 and convs[0]["title"] == "测试对话":
            ok("Conversation 写入 + 读取成功")
        else:
            fail("Conversation 读写异常", str(convs)[:80])

        # 34k: SessionRegistry 写入 + 读取
        persistence.save_session_registry("sess-001", "conv-001")
        selected = persistence.load_session_registry("sess-001")
        if selected == "conv-001":
            ok("SessionRegistry 写入 + 读取成功")
        else:
            fail("SessionRegistry 读写异常", str(selected))

        # 34l: 统计信息
        stats = persistence.get_stats()
        if stats.get("user_memories", 0) > 0 and stats.get("thread_messages", 0) > 0:
            ok("统计信息正确", str(stats))
        else:
            fail("统计信息异常", str(stats))

        # 34m: 删除 ThreadState (级联删除消息)
        persistence.delete_thread_state("thread-001")
        ts_after_del = persistence.load_thread_state("thread-001")
        msgs_after_del = persistence.load_thread_messages("thread-001")
        if ts_after_del is None and len(msgs_after_del) == 0:
            ok("删除 ThreadState 级联删除消息成功")
        else:
            fail("删除 ThreadState 异常")

        # 34n: namespace 隔离
        persistence.save_memory("ns-other", {
            "stableKey": "mem-other",
            "text": "其他 namespace 的记忆",
            "tags": [],
            "status": "active",
        })
        ns_test_count = len(persistence.load_memories("ns-test"))
        ns_other_count = len(persistence.load_memories("ns-other"))
        if ns_other_count == 1 and ns_test_count == 5:
            ok("namespace 隔离正确")
        else:
            fail("namespace 隔离异常", f"ns-test={ns_test_count}, ns-other={ns_other_count}")

    finally:
        persistence.close()
        try:
            _os.remove(db_path)
        except Exception:
            pass


def test_user_memory_persistence():
    """测试 35: UserMemoryStore 持久化恢复"""
    section("单元测试 35: UserMemoryStore 持久化恢复")
    import tempfile
    import os as _os
    from duckdb_store import DuckDBPersistence
    from user_memory import UserMemoryStore, UserMemory, SemanticMetadata, STATUS_ACTIVE, STATUS_SUPPRESSED

    db_path = _os.path.join(tempfile.gettempdir(), f"test_mem_{int(time.time() * 1000)}.duckdb")
    persistence = DuckDBPersistence(db_path)

    if not persistence.is_enabled:
        fail("DuckDBPersistence 应启用")
        return

    try:
        # 第一个 store 实例: 写入记忆
        store1 = UserMemoryStore(persistence=persistence)
        store1._get_namespace("test-ns")  # 触发加载

        memory = UserMemory(
            stable_key="mem-001",
            text="用户喜欢深色模式",
            tags=["UI", "偏好"],
            polarity="prefer",
            status=STATUS_ACTIVE,
            confidence=0.85,
            subject="UI",
            facet="深色模式",
        )
        # 模拟向量（不调用 embedding API）
        memory.embedding = [0.1, 0.2, 0.3]
        # 设置 semantic 元数据（search 方法会检查 semantic_index_version 非空）
        memory.semantic = SemanticMetadata.create_current()
        store1._namespaces["test-ns"]["mem-001"] = memory
        store1._persist_memory("test-ns", memory)

        # 35a: 写入后验证
        loaded = persistence.load_memories("test-ns")
        if len(loaded) == 1 and loaded[0]["text"] == "用户喜欢深色模式":
            ok("UserMemory 写入 DuckDB 成功")
        else:
            fail("写入异常")

        # 35b: 向量持久化
        if loaded and loaded[0].get("embedding") == [0.1, 0.2, 0.3]:
            ok("向量持久化正确")
        else:
            fail("向量持久化异常")

        # 35c: 新 store 实例模拟"重启恢复"
        store2 = UserMemoryStore(persistence=persistence)
        store2._get_namespace("test-ns")  # 触发从 DuckDB 加载

        restored = store2.list_memories("test-ns")
        if len(restored) == 1 and restored[0].text == "用户喜欢深色模式":
            ok("重启后 UserMemory 恢复成功")
        else:
            fail("恢复异常", f"count={len(restored)}")

        # 35d: 恢复的向量正确
        if restored and restored[0].embedding == [0.1, 0.2, 0.3]:
            ok("恢复的向量正确")
        else:
            fail("恢复的向量异常", str(restored[0].embedding) if restored else "None")

        # 35e: 恢复的记忆可搜索
        import asyncio
        search_results = asyncio.new_event_loop().run_until_complete(
            store2.search("test-ns", [0.1, 0.2, 0.3], limit=5)
        )
        if len(search_results) == 1 and search_results[0].memory.text == "用户喜欢深色模式":
            ok("恢复的记忆可被向量搜索命中")
        else:
            fail("搜索异常", f"results={len(search_results)}")

        # 35f: 删除后恢复
        store2.delete("test-ns", "mem-001")
        store3 = UserMemoryStore(persistence=persistence)
        store3._get_namespace("test-ns")
        if len(store3.list_memories("test-ns")) == 0:
            ok("删除后重启恢复为空")
        else:
            fail("删除后不应有记忆")

    finally:
        persistence.close()
        try:
            _os.remove(db_path)
        except Exception:
            pass


def test_thread_store_persistence():
    """测试 36: ThreadStore 持久化恢复"""
    section("单元测试 36: ThreadStore 持久化恢复")
    import tempfile
    import os as _os
    from duckdb_store import DuckDBPersistence
    from thread_state import ThreadStore, ThreadState, ThreadMessage
    from stream import create_id

    db_path = _os.path.join(tempfile.gettempdir(), f"test_thread_{int(time.time() * 1000)}.duckdb")
    persistence = DuckDBPersistence(db_path)

    if not persistence.is_enabled:
        fail("DuckDBPersistence 应启用")
        return

    try:
        # 第一个 store 实例: 创建 ThreadState + 写入消息
        store1 = ThreadStore(persistence=persistence)
        state = store1.create()
        thread_id = state.thread_id

        state.append("user", "你好")
        state.append("assistant", "你好！有什么可以帮你的？")
        state.summary = "用户打招呼"
        state.pinned_decisions = ["使用深色模式"]
        store1.persist_thread(thread_id)

        # 36a: 验证持久化
        ts_data = persistence.load_thread_state(thread_id)
        msgs_data = persistence.load_thread_messages(thread_id)
        if (ts_data and ts_data["summary"] == "用户打招呼"
                and ts_data["pinned_decisions"] == ["使用深色模式"]
                and len(msgs_data) == 2):
            ok("ThreadState + Messages 持久化成功")
        else:
            fail("持久化异常", f"ts={ts_data}, msgs={len(msgs_data)}")

        # 36b: 新 store 实例模拟"重启恢复"
        store2 = ThreadStore(persistence=persistence)
        restored = store2.get(thread_id)

        if restored and len(restored.messages) == 2:
            ok("重启后 ThreadState 恢复成功", f"{len(restored.messages)} 条消息")
        else:
            fail("恢复异常", f"messages={len(restored.messages) if restored else 0}")

        # 36c: 恢复的消息内容正确
        if restored:
            if (restored.messages[0].text == "你好"
                    and restored.messages[1].text == "你好！有什么可以帮你的？"):
                ok("恢复的消息内容正确")
            else:
                fail("消息内容异常",
                     f"[0]={restored.messages[0].text[:30]}, [1]={restored.messages[1].text[:30]}")

            # 36d: 恢复的 summary + pinned 正确
            if (restored.summary == "用户打招呼"
                    and restored.pinned_decisions == ["使用深色模式"]):
                ok("恢复的 summary + pinned_decisions 正确")
            else:
                fail("summary/pinned 恢复异常")

        # 36e: 删除后恢复
        store2.delete(thread_id)
        store3 = ThreadStore(persistence=persistence)
        restored_after_del = store3.get(thread_id)
        if restored_after_del is None:
            ok("删除后重启恢复为空")
        else:
            fail("删除后不应有 ThreadState")

    finally:
        persistence.close()
        try:
            _os.remove(db_path)
        except Exception:
            pass


def test_session_store_persistence():
    """测试 37: SessionStore 持久化恢复"""
    section("单元测试 37: SessionStore 持久化恢复")
    import tempfile
    import os as _os
    from duckdb_store import DuckDBPersistence
    from thread_state import SessionStore

    db_path = _os.path.join(tempfile.gettempdir(), f"test_session_{int(time.time() * 1000)}.duckdb")
    persistence = DuckDBPersistence(db_path)

    if not persistence.is_enabled:
        fail("DuckDBPersistence 应启用")
        return

    try:
        # 第一个 store 实例: 创建会话注册表
        store1 = SessionStore(persistence=persistence)
        registry1 = store1.get_or_create("test-session-001")

        conv1 = registry1.create("第一个对话")
        conv2 = registry1.create("第二个对话")
        registry1.select(conv1.conversation_id)
        registry1.touch(conv1.conversation_id)
        registry1.rename(conv2.conversation_id, "重命名的对话")

        # 37a: 验证持久化
        convs_data = persistence.load_conversations("test-session-001")
        selected = persistence.load_session_registry("test-session-001")
        if len(convs_data) == 2 and selected == conv1.conversation_id:
            ok("Conversation + 选中状态持久化成功")
        else:
            fail("持久化异常", f"convs={len(convs_data)}, selected={selected}")

        # 37b: 新 store 实例模拟"重启恢复"
        store2 = SessionStore(persistence=persistence)
        registry2 = store2.get_or_create("test-session-001")

        restored_convs = registry2.list_conversations()
        if len(restored_convs) == 2:
            ok("重启后会话列表恢复成功", f"{len(restored_convs)} 个会话")
        else:
            fail("恢复异常", f"convs={len(restored_convs)}")

        # 37c: 恢复的标题正确
        titles = {c.title for c in restored_convs}
        if "重命名的对话" in titles:
            ok("恢复的会话标题正确 (含重命名)")
        else:
            fail("标题恢复异常", str(titles))

        # 37d: 恢复的选中状态正确
        if registry2.selected_conversation_id == conv1.conversation_id:
            ok("恢复的选中状态正确")
        else:
            fail("选中状态恢复异常",
                 f"expected={conv1.conversation_id[:8]}, actual={registry2.selected_conversation_id[:8]}")

        # 37e: 恢复的 has_messages 正确
        conv1_restored = registry2.get(conv1.conversation_id)
        if conv1_restored and conv1_restored.has_messages:
            ok("恢复的 has_messages 正确")
        else:
            fail("has_messages 恢复异常")

        # 37f: 删除会话后恢复
        registry2.delete(conv1.conversation_id)
        store3 = SessionStore(persistence=persistence)
        registry3 = store3.get_or_create("test-session-001")
        restored_convs_3 = registry3.list_conversations()
        if len(restored_convs_3) == 1:
            ok("删除会话后重启恢复正确", f"{len(restored_convs_3)} 个会话")
        else:
            fail("删除恢复异常", f"convs={len(restored_convs_3)}")

    finally:
        persistence.close()
        try:
            _os.remove(db_path)
        except Exception:
            pass


def test_duckdb_degradation():
    """测试 38: DuckDB 降级模式 — DUCKDB_PATH 为空时纯内存模式"""
    section("单元测试 38: DuckDB 降级模式")
    from duckdb_store import DuckDBPersistence
    from user_memory import UserMemoryStore, UserMemory, STATUS_ACTIVE
    from thread_state import ThreadStore

    # 38a: DUCKDB_PATH 为空 → 不启用持久化
    persistence_empty = DuckDBPersistence("")
    if not persistence_empty.is_enabled:
        ok("DUCKDB_PATH 为空时不启用持久化")
    else:
        fail("空路径不应启用持久化")

    # 38b: 纯内存 UserMemoryStore 仍正常工作
    store_mem = UserMemoryStore(persistence=None)
    store_mem._get_namespace("ns-mem-test")
    memory = UserMemory(
        stable_key="mem-mem",
        text="纯内存记忆",
        status=STATUS_ACTIVE,
    )
    store_mem._namespaces["ns-mem-test"]["mem-mem"] = memory
    if store_mem.list_memories("ns-mem-test")[0].text == "纯内存记忆":
        ok("纯内存 UserMemoryStore 正常工作")
    else:
        fail("纯内存模式异常")

    # 38c: 纯内存 ThreadStore 仍正常工作
    store_thread = ThreadStore(persistence=None)
    state = store_thread.create()
    state.append("user", "测试")
    if store_thread.get(state.thread_id) and len(state.messages) == 1:
        ok("纯内存 ThreadStore 正常工作")
    else:
        fail("纯内存 ThreadStore 异常")

    # 38d: persist_thread 在无持久化时静默跳过
    try:
        store_thread.persist_thread(state.thread_id)
        ok("persist_thread 无持久化时静默跳过")
    except Exception as e:
        fail("无持久化时 persist_thread 不应报错", str(e))

    # 38e: 无效路径 → 降级为纯内存
    persistence_bad = DuckDBPersistence("/nonexistent/path/that/does/not/exist/db.duckdb")
    # DuckDB 可能创建目录，如果不行则降级
    if not persistence_bad.is_enabled:
        ok("无效路径时降级为纯内存模式")
    else:
        # DuckDB 可能成功创建了目录，这也 OK
        ok("DuckDB 自动创建目录 (也 acceptable)")
        persistence_bad.close()


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
    # Agent Loop 钩子化循环测试 (pi.dev 风格)
    test_agent_loop_basic()
    test_agent_loop_tool_calls()
    test_transform_context_hook()
    test_prepare_next_turn_hook()
    test_should_stop_after_turn_hook()
    test_fail_tool_calls_truncated()
    test_followup_queue()
    test_followup_double_loop()
    test_steer_injection_in_loop()
    test_agent_loop_continue()
    test_followup_chunks()
    test_tool_execution_error_handling()
    test_truncated_tool_call_in_loop()
    # Chat Orchestrator 集成测试 (阶段一: agent_loop 迁移)
    test_orchestrator_message_conversion()
    test_orchestrator_stop_reason_mapping()
    test_orchestrator_build_tool_definitions()
    test_orchestrator_should_stop()
    test_orchestrator_emit_event()
    test_orchestrator_full_loop_mock()
    # DuckDB 持久化测试
    test_duckdb_persistence_basic()
    test_user_memory_persistence()
    test_thread_store_persistence()
    test_session_store_persistence()
    test_duckdb_degradation()


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
