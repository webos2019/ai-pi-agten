import React, { useState, useCallback, useRef } from 'react'

// ─── Types ────────────────────────────────────────────

interface StageData {
    stage: string
    status: 'started' | 'running' | 'done'
    message: string
    alert?: string
    level?: string
    timestamp?: string
    result?: Record<string, any>
    evidence?: string[]
    root_cause?: string
    evidence_chain?: string[]
    baseline?: Record<string, any>
    fix_steps?: Array<{ action: string; type: string; risk: string }>
    before?: Record<string, any>
    after?: Record<string, any>
    verdict?: string
    summary?: Record<string, any>
}

interface CheckItem {
    name: string
    check: string
    pass: boolean
    detail: string
}

interface ErrorReportData {
    stage: string
    status: 'started' | 'running' | 'done'
    message: string
    error_type?: string
    title?: string
    icon?: string
    severity?: string
    module?: string
    symptom?: string
    timestamp?: string
    check_item?: CheckItem
    check_index?: number
    check_total?: number
    checks?: CheckItem[]
    passed?: number
    failed?: number
    root_cause?: string
    evidence?: string[]
    fix_steps?: Array<{ action: string; type: string; risk: string }>
    prevention?: string[]
    report?: {
        title: string
        icon: string
        severity: string
        module: string
        symptom: string
        root_cause: string
        evidence: string[]
        checks: CheckItem[]
        fix_steps: Array<{ action: string; type: string; risk: string }>
        prevention: string[]
        checks_passed: number
        checks_failed: number
        generated_at: string
    }
}

// ─── 故障场景定义 ─────────────────────────────────────

const SCENARIOS = [
    {
        id: '502',
        title: 'OA 502 宕机',
        icon: '🔴',
        desc: 'OA系统首页返回 502 Bad Gateway，用户无法访问',
        level: 'P0',
    },
    {
        id: 'slow',
        title: 'OA 响应缓慢',
        icon: '🟡',
        desc: '核心接口平均响应时间 >5s，疑似数据库慢查询',
        level: 'P1',
    },
    {
        id: 'disk',
        title: '磁盘告警',
        icon: '🟠',
        desc: '服务器磁盘使用率 96%，即将写满',
        level: 'P1',
    },
]

// ─── 错误场景定义 ─────────────────────────────────────

const ERROR_SCENARIOS = [
    {
        id: 'voice-tts',
        title: '语音 TTS 合成失败',
        icon: '🔊',
        desc: '语音界面 AI 回复文字正常但无语音播放',
        severity: 'P1',
        module: '语音对话 / TTS',
    },
    {
        id: 'voice-stt',
        title: '语音识别 (STT) 失败',
        icon: '🎤',
        desc: '点击麦克风按钮无法开始语音输入',
        severity: 'P2',
        module: '语音对话 / STT',
    },
    {
        id: 'chat-timeout',
        title: '聊天接口超时',
        icon: '💬',
        desc: '发送消息后长时间无响应，显示网络错误',
        severity: 'P0',
        module: '智能对话 / Chat',
    },
    {
        id: 'stock-api',
        title: '股票数据加载失败',
        icon: '📈',
        desc: '搜索框无自动补全，K线图显示加载失败',
        severity: 'P2',
        module: '股票分析 / Stock',
    },
    {
        id: 'image-api',
        title: '生图 API 调用失败',
        icon: '🎨',
        desc: '输入提示词后显示「生图失败，请检查 API Key」',
        severity: 'P2',
        module: '文字生图 / TextToImage',
    },
    {
        id: 'frontend-crash',
        title: '前端 JS 运行时错误',
        icon: '💥',
        desc: '切换页面后白屏，控制台报 ReferenceError',
        severity: 'P0',
        module: '前端 / Frontend',
    },
]

// ─── 流水线阶段定义 ───────────────────────────────────

const STAGES = [
    { id: 'manager-start', label: '指挥官', worker: 'manager', desc: '接收告警，启动流水线' },
    { id: 'alert-intake', label: '告警接单', worker: 'alert-intake', desc: '快速侦察 + 生成工单' },
    { id: 'rca-analyst', label: '根因分析', worker: 'rca-analyst', desc: '深度排查 + 定位根因' },
    { id: 'remediation-planner', label: '修复规划', worker: 'remediation-planner', desc: '制定修复方案' },
    { id: 'fix-execution', label: '修复执行', worker: 'system', desc: '执行修复操作' },
    { id: 'recovery-verifier', label: '恢复验证', worker: 'recovery-verifier', desc: '验证修复效果' },
    { id: 'manager-end', label: '汇总报告', worker: 'manager', desc: '输出总报告' },
]

const ERROR_STAGES = [
    { id: 'error-intake', label: '错误接单', desc: '接收错误报告' },
    { id: 'diagnostic', label: '诊断检查', desc: '逐项检查各组件' },
    { id: 'root-cause', label: '根因分析', desc: '定位根本原因' },
    { id: 'fix-plan', label: '修复方案', desc: '制定修复步骤' },
    { id: 'prevention', label: '预防建议', desc: '生成预防措施' },
    { id: 'summary', label: '汇总报告', desc: '输出完整报告' },
]

// ─── Component ─────────────────────────────────────────

const OpsPilotDemo: React.FC = () => {
    const [tab, setTab] = useState<'pipeline' | 'error-report'>('pipeline')
    const [running, setRunning] = useState(false)
    const [completed, setCompleted] = useState(false)
    const [stages, setStages] = useState<StageData[]>([])
    const [selectedScenario, setSelectedScenario] = useState('502')
    const abortRef = useRef<AbortController | null>(null)

    // ── 错误报告状态 ──
    const [errRunning, setErrRunning] = useState(false)
    const [errCompleted, setErrCompleted] = useState(false)
    const [errStages, setErrStages] = useState<ErrorReportData[]>([])
    const [selectedError, setSelectedError] = useState('voice-tts')
    const errAbortRef = useRef<AbortController | null>(null)

    // ── 流水线 Demo ──
    const runDemo = useCallback(async () => {
        if (running) return

        setRunning(true)
        setCompleted(false)
        setStages([])

        const controller = new AbortController()
        abortRef.current = controller

        try {
            const resp = await fetch('/api/opspilot-demo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scenario: selectedScenario }),
                signal: controller.signal,
            })

            const reader = resp.body?.getReader()
            if (!reader) return

            const decoder = new TextDecoder()
            let buffer = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (!line.trim()) continue
                    try {
                        const data: StageData = JSON.parse(line)
                        setStages(prev => [...prev, data])
                    } catch { /* skip */ }
                }
            }
            setCompleted(true)
        } catch (e) {
            if ((e as Error).name !== 'AbortError') {
                console.error('Demo error:', e)
            }
        } finally {
            setRunning(false)
        }
    }, [running, selectedScenario])

    const stopDemo = useCallback(() => {
        abortRef.current?.abort()
        setRunning(false)
    }, [])

    // ── 错误报告 ──
    const runErrorReport = useCallback(async () => {
        if (errRunning) return

        setErrRunning(true)
        setErrCompleted(false)
        setErrStages([])

        const controller = new AbortController()
        errAbortRef.current = controller

        try {
            const resp = await fetch('/api/opspilot-error-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ errorType: selectedError }),
                signal: controller.signal,
            })

            const reader = resp.body?.getReader()
            if (!reader) return

            const decoder = new TextDecoder()
            let buffer = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (!line.trim()) continue
                    try {
                        const data: ErrorReportData = JSON.parse(line)
                        setErrStages(prev => [...prev, data])
                    } catch { /* skip */ }
                }
            }
            setErrCompleted(true)
        } catch (e) {
            if ((e as Error).name !== 'AbortError') {
                console.error('Error report failed:', e)
            }
        } finally {
            setErrRunning(false)
        }
    }, [errRunning, selectedError])

    const stopErrorReport = useCallback(() => {
        errAbortRef.current?.abort()
        setErrRunning(false)
    }, [])

    // ── 流水线阶段状态 ──
    const getStageStatus = (stageId: string): 'pending' | 'active' | 'done' => {
        const stageMap: Record<string, string> = {
            'manager-start': 'manager',
            'alert-intake': 'alert-intake',
            'rca-analyst': 'rca-analyst',
            'remediation-planner': 'remediation-planner',
            'fix-execution': 'fix-execution',
            'recovery-verifier': 'recovery-verifier',
            'manager-end': 'manager',
        }
        const targetStage = stageMap[stageId]
        if (!targetStage) return 'pending'

        const matching = stages.filter(s => s.stage === targetStage)
        if (matching.length === 0) return 'pending'
        if (matching.some(s => s.status === 'done')) return 'done'
        return 'active'
    }

    const getData = (stageName: string): StageData | undefined => {
        return stages.find(s => s.stage === stageName && s.status === 'done')
    }

    const alertIntakeData = getData('alert-intake')
    const rcaData = getData('rca-analyst')
    const remediationData = getData('remediation-planner')
    const recoveryData = getData('recovery-verifier')
    const managerStartData = stages.find(s => s.stage === 'manager' && s.status === 'started')
    const managerEndData = getData('manager')

    // ── 错误报告阶段状态 ──
    const getErrStageStatus = (stageId: string): 'pending' | 'active' | 'done' => {
        const matching = errStages.filter(s => s.stage === stageId)
        if (matching.length === 0) return 'pending'
        if (matching.some(s => s.status === 'done')) return 'done'
        return 'active'
    }

    const errIntakeData = errStages.find(s => s.stage === 'error-intake' && s.status === 'started')
    const diagnosticDone = errStages.find(s => s.stage === 'diagnostic' && s.status === 'done')
    const rootCauseDone = errStages.find(s => s.stage === 'root-cause' && s.status === 'done')
    const fixPlanDone = errStages.find(s => s.stage === 'fix-plan' && s.status === 'done')
    const preventionDone = errStages.find(s => s.stage === 'prevention' && s.status === 'done')
    const summaryDone = errStages.find(s => s.stage === 'summary' && s.status === 'done')
    const checkItems = errStages.filter(s => s.stage === 'diagnostic' && s.status === 'running' && s.check_item)

    return (
        <div className="opspilot-page">
            {/* ─── Tab 切换 ─── */}
            <div className="opspilot-tabs">
                <button
                    className={`opspilot-tab ${tab === 'pipeline' ? 'active' : ''}`}
                    onClick={() => setTab('pipeline')}
                >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
                    </svg>
                    AIOps 流水线
                </button>
                <button
                    className={`opspilot-tab ${tab === 'error-report' ? 'active' : ''}`}
                    onClick={() => setTab('error-report')}
                >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><path d="M12 9v4" /><path d="M12 17h.01" />
                    </svg>
                    网站错误处理报告
                </button>
            </div>

            {/* ════════════════════════════════════════════════════ */}
            {/* ─── Tab 1: AIOps 流水线 ─── */}
            {/* ════════════════════════════════════════════════════ */}
            {tab === 'pipeline' && (
                <>
                    {/* 场景选择 */}
                    <div className="opspilot-scenario-section">
                        <h2 className="opspilot-section-title">选择故障场景</h2>
                        <div className="opspilot-scenario-grid">
                            {SCENARIOS.map(s => (
                                <div
                                    key={s.id}
                                    className={`opspilot-scenario-card ${selectedScenario === s.id ? 'selected' : ''} ${running ? 'disabled' : ''}`}
                                    onClick={() => !running && setSelectedScenario(s.id)}
                                >
                                    <div className="opspilot-scenario-icon">{s.icon}</div>
                                    <div className="opspilot-scenario-info">
                                        <div className="opspilot-scenario-title">{s.title}</div>
                                        <div className="opspilot-scenario-desc">{s.desc}</div>
                                    </div>
                                    <div className={`opspilot-scenario-level level-${s.level}`}>{s.level}</div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* 启动按钮 */}
                    <div className="opspilot-action-bar">
                        {!running ? (
                            <button className="opspilot-run-btn" onClick={runDemo} disabled={running}>
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M8 5v14l11-7z" />
                                </svg>
                                启动 OpsPilot 流水线
                            </button>
                        ) : (
                            <button className="opspilot-stop-btn" onClick={stopDemo}>
                                <span className="opspilot-pulse" />
                                流水线执行中...
                                <span className="opspilot-stop-text">点击停止</span>
                            </button>
                        )}
                        {completed && (
                            <span className="opspilot-completed-badge">✅ 流水线已完成</span>
                        )}
                    </div>

                    {/* 流水线进度条 */}
                    {stages.length > 0 && (
                        <div className="opspilot-pipeline">
                            {STAGES.map((stage, i) => {
                                const status = getStageStatus(stage.id)
                                return (
                                    <React.Fragment key={stage.id}>
                                        <div className={`opspilot-pipeline-node ${status}`}>
                                            <div className="opspilot-pipeline-circle">
                                                {status === 'done' ? '✓' : status === 'active' ? <span className="opspilot-mini-spinner" /> : i + 1}
                                            </div>
                                            <div className="opspilot-pipeline-label">{stage.label}</div>
                                            <div className="opspilot-pipeline-desc">{stage.desc}</div>
                                        </div>
                                        {i < STAGES.length - 1 && (
                                            <div className={`opspilot-pipeline-line ${status === 'done' ? 'done' : ''}`} />
                                        )}
                                    </React.Fragment>
                                )
                            })}
                        </div>
                    )}

                    {/* 告警信息 */}
                    {managerStartData && (
                        <div className="opspilot-alert-banner">
                            <div className="opspilot-alert-icon">🚨</div>
                            <div className="opspilot-alert-content">
                                <div className="opspilot-alert-title">{managerStartData.alert}</div>
                                <div className="opspilot-alert-meta">
                                    <span className={`level-badge level-${managerStartData.level}`}>{managerStartData.level}</span>
                                    <span>⏰ {managerStartData.timestamp}</span>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* 阶段详情 */}
                    <div className="opspilot-stages">
                        {alertIntakeData?.result && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">alert-intake</span>
                                    <span className="opspilot-stage-title">告警接单 · 快速侦察</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-metrics-grid">
                                        <MetricCard label="HTTP 状态" value={alertIntakeData.result.http_status} bad={alertIntakeData.result.http_status >= 400} />
                                        <MetricCard label="响应时间" value={`${alertIntakeData.result.response_time}ms`} bad={alertIntakeData.result.response_time > 2000} />
                                        <MetricCard label="CPU" value={`${alertIntakeData.result.cpu}%`} warn={alertIntakeData.result.cpu > 80} />
                                        <MetricCard label="内存" value={`${alertIntakeData.result.memory}%`} warn={alertIntakeData.result.memory > 85} />
                                        <MetricCard label="磁盘" value={`${alertIntakeData.result.disk}%`} warn={alertIntakeData.result.disk > 90} />
                                        <MetricCard label="ERROR 日志" value={`${alertIntakeData.result.error_logs}条`} bad={alertIntakeData.result.error_logs > 10} />
                                    </div>
                                    <div className="opspilot-route-hint">
                                        → 路由到: <strong>{alertIntakeData.result.route_to}</strong>
                                    </div>
                                </div>
                            </div>
                        )}

                        {rcaData && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">rca-analyst</span>
                                    <span className="opspilot-stage-title">根因分析 · 深度排查</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-evidence-chain">
                                        <div className="opspilot-evidence-label">证据链:</div>
                                        {rcaData.evidence_chain?.map((e, i) => (
                                            <div key={i} className="opspilot-evidence-item">
                                                <span className="opspilot-evidence-num">{i + 1}</span>
                                                <span className="opspilot-evidence-text">{e}</span>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="opspilot-root-cause">
                                        <span className="opspilot-cause-label">根因:</span>
                                        <span className="opspilot-cause-text">{rcaData.root_cause}</span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {remediationData?.fix_steps && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">remediation-planner</span>
                                    <span className="opspilot-stage-title">修复方案规划</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-baseline">
                                        <span className="opspilot-baseline-label">修复前基线:</span>
                                        <span>HTTP {remediationData.baseline?.http} · CPU {remediationData.baseline?.cpu}% · 内存 {remediationData.baseline?.memory}% · 磁盘 {remediationData.baseline?.disk}%</span>
                                    </div>
                                    <div className="opspilot-fix-steps">
                                        {remediationData.fix_steps.map((step, i) => (
                                            <div key={i} className="opspilot-fix-step">
                                                <div className="opspilot-fix-step-num">{i + 1}</div>
                                                <div className="opspilot-fix-step-body">
                                                    <div className="opspilot-fix-step-action">{step.action}</div>
                                                    <div className="opspilot-fix-step-meta">
                                                        <span className={`fix-type type-${step.type}`}>{step.type}</span>
                                                        <span className={`fix-risk risk-${step.risk}`}>风险: {step.risk}</span>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {recoveryData?.before && recoveryData?.after && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">recovery-verifier</span>
                                    <span className="opspilot-stage-title">恢复验证</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-comparison-table">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>指标</th>
                                                    <th>修复前</th>
                                                    <th>修复后</th>
                                                    <th>状态</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                <ComparisonRow label="HTTP 状态" before={recoveryData.before.http} after={recoveryData.after.http} ok={(v: any) => v === 200} />
                                                <ComparisonRow label="响应时间" before={`${recoveryData.before.response_time || '-'}ms`} after={`${recoveryData.after.response_time}ms`} ok={(v: string) => !v.includes('5200')} />
                                                <ComparisonRow label="CPU" before={`${recoveryData.before.cpu}%`} after={`${recoveryData.after.cpu}%`} ok={(v: string) => parseInt(v) < 80} />
                                                <ComparisonRow label="内存" before={`${recoveryData.before.memory}%`} after={`${recoveryData.after.memory}%`} ok={(v: string) => parseInt(v) < 85} />
                                                <ComparisonRow label="磁盘" before={`${recoveryData.before.disk}%`} after={`${recoveryData.after.disk}%`} ok={(v: string) => parseInt(v) < 90} />
                                                <ComparisonRow label="ERROR日志" before={`${recoveryData.before.errors}条/min`} after={`${recoveryData.after.errors}条/min`} ok={(v: string) => parseInt(v) < 10} />
                                            </tbody>
                                        </table>
                                    </div>
                                    <div className="opspilot-verdict">
                                        {recoveryData.verdict}
                                    </div>
                                </div>
                            </div>
                        )}

                        {managerEndData?.summary && (
                            <div className="opspilot-summary-card">
                                <div className="opspilot-summary-header">
                                    <span className="opspilot-summary-icon">📋</span>
                                    <span className="opspilot-summary-title">OpsPilot 故障处理总报告</span>
                                </div>
                                <div className="opspilot-summary-body">
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">告警:</span>
                                        <span>{managerEndData.summary.alert}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">级别:</span>
                                        <span className={`level-badge level-${managerEndData.summary.level}`}>{managerEndData.summary.level}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">根因:</span>
                                        <span>{managerEndData.summary.root_cause}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">结论:</span>
                                        <span className="opspilot-summary-verdict">{managerEndData.summary.verdict}</span>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* 空状态 */}
                    {stages.length === 0 && !running && (
                        <div className="opspilot-empty">
                            <div className="opspilot-empty-icon">
                                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M12 2L2 7l10 5 10-5-10-5z" />
                                    <path d="M2 17l10 5 10-5" />
                                    <path d="M2 12l10 5 10-5" />
                                </svg>
                            </div>
                            <p className="opspilot-empty-title">OpsPilot AIOps Demo</p>
                            <p className="opspilot-empty-sub">选择一个故障场景，点击启动按钮体验 4-Worker 协作流水线</p>
                            <div className="opspilot-empty-workers">
                                <span>🔔 alert-intake</span>
                                <span>🔍 rca-analyst</span>
                                <span>🔧 remediation-planner</span>
                                <span>✅ recovery-verifier</span>
                            </div>
                        </div>
                    )}
                </>
            )}

            {/* ════════════════════════════════════════════════════ */}
            {/* ─── Tab 2: 网站错误处理报告 ─── */}
            {/* ════════════════════════════════════════════════════ */}
            {tab === 'error-report' && (
                <>
                    {/* 错误场景选择 */}
                    <div className="opspilot-scenario-section">
                        <h2 className="opspilot-section-title">选择错误场景</h2>
                        <div className="opspilot-error-scenario-grid">
                            {ERROR_SCENARIOS.map(s => (
                                <div
                                    key={s.id}
                                    className={`opspilot-scenario-card opspilot-error-card ${selectedError === s.id ? 'selected' : ''} ${errRunning ? 'disabled' : ''}`}
                                    onClick={() => !errRunning && setSelectedError(s.id)}
                                >
                                    <div className="opspilot-scenario-icon">{s.icon}</div>
                                    <div className="opspilot-scenario-info">
                                        <div className="opspilot-scenario-title">{s.title}</div>
                                        <div className="opspilot-scenario-desc">{s.desc}</div>
                                        <div className="opspilot-error-module">{s.module}</div>
                                    </div>
                                    <div className={`opspilot-scenario-level level-${s.severity}`}>{s.severity}</div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* 启动按钮 */}
                    <div className="opspilot-action-bar">
                        {!errRunning ? (
                            <button className="opspilot-run-btn" onClick={runErrorReport} disabled={errRunning}>
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><path d="M12 9v4" /><path d="M12 17h.01" />
                                </svg>
                                生成错误处理报告
                            </button>
                        ) : (
                            <button className="opspilot-stop-btn" onClick={stopErrorReport}>
                                <span className="opspilot-pulse" />
                                诊断中...
                                <span className="opspilot-stop-text">点击停止</span>
                            </button>
                        )}
                        {errCompleted && (
                            <span className="opspilot-completed-badge">✅ 报告已生成</span>
                        )}
                    </div>

                    {/* 诊断进度条 */}
                    {errStages.length > 0 && (
                        <div className="opspilot-pipeline">
                            {ERROR_STAGES.map((stage, i) => {
                                const status = getErrStageStatus(stage.id)
                                return (
                                    <React.Fragment key={stage.id}>
                                        <div className={`opspilot-pipeline-node ${status}`}>
                                            <div className="opspilot-pipeline-circle">
                                                {status === 'done' ? '✓' : status === 'active' ? <span className="opspilot-mini-spinner" /> : i + 1}
                                            </div>
                                            <div className="opspilot-pipeline-label">{stage.label}</div>
                                            <div className="opspilot-pipeline-desc">{stage.desc}</div>
                                        </div>
                                        {i < ERROR_STAGES.length - 1 && (
                                            <div className={`opspilot-pipeline-line ${status === 'done' ? 'done' : ''}`} />
                                        )}
                                    </React.Fragment>
                                )
                            })}
                        </div>
                    )}

                    {/* 错误信息横幅 */}
                    {errIntakeData && (
                        <div className="opspilot-alert-banner opspilot-error-banner">
                            <div className="opspilot-alert-icon">{errIntakeData.icon}</div>
                            <div className="opspilot-alert-content">
                                <div className="opspilot-alert-title">{errIntakeData.title}</div>
                                <div className="opspilot-alert-meta">
                                    <span className={`level-badge level-${errIntakeData.severity}`}>{errIntakeData.severity}</span>
                                    <span>📦 {errIntakeData.module}</span>
                                    <span>⏰ {errIntakeData.timestamp}</span>
                                </div>
                                <div className="opspilot-error-symptom">{errIntakeData.symptom}</div>
                            </div>
                        </div>
                    )}

                    {/* 诊断检查详情 */}
                    <div className="opspilot-stages">
                        {/* 实时检查项 */}
                        {checkItems.length > 0 && !diagnosticDone && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">diagnostic</span>
                                    <span className="opspilot-stage-title">诊断检查进行中</span>
                                    <span className="opspilot-stage-status active">
                                        <span className="opspilot-mini-spinner" /> 检查中
                                    </span>
                                </div>
                                <div className="opspilot-stage-body">
                                    {checkItems.map((item, i) => {
                                        const c = item.check_item!
                                        return (
                                            <div key={i} className={`err-check-item ${c.pass ? 'pass' : 'fail'}`}>
                                                <span className="err-check-icon">{c.pass ? '✅' : '❌'}</span>
                                                <div className="err-check-info">
                                                    <span className="err-check-name">{c.name}</span>
                                                    <span className="err-check-detail">{c.detail}</span>
                                                </div>
                                                <span className="err-check-progress">
                                                    {item.check_index! + 1}/{item.check_total}
                                                </span>
                                            </div>
                                        )
                                    })}
                                </div>
                            </div>
                        )}

                        {/* 诊断完成总结 */}
                        {diagnosticDone && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">diagnostic</span>
                                    <span className="opspilot-stage-title">诊断检查完成</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="err-check-summary">
                                        <span className="err-check-pass">✅ 通过: {diagnosticDone.passed}</span>
                                        <span className="err-check-fail-num">❌ 失败: {diagnosticDone.failed}</span>
                                    </div>
                                    {diagnosticDone.checks?.map((c, i) => (
                                        <div key={i} className={`err-check-item ${c.pass ? 'pass' : 'fail'}`}>
                                            <span className="err-check-icon">{c.pass ? '✅' : '❌'}</span>
                                            <div className="err-check-info">
                                                <span className="err-check-name">{c.name}</span>
                                                <span className="err-check-detail">{c.detail}</span>
                                                <span className="err-check-method">检查方式: <code>{c.check}</code></span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* 根因分析 */}
                        {rootCauseDone && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">root-cause</span>
                                    <span className="opspilot-stage-title">根因分析</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-evidence-chain">
                                        <div className="opspilot-evidence-label">证据链:</div>
                                        {rootCauseDone.evidence?.map((e, i) => (
                                            <div key={i} className="opspilot-evidence-item">
                                                <span className="opspilot-evidence-num">{i + 1}</span>
                                                <span className="opspilot-evidence-text">{e}</span>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="opspilot-root-cause">
                                        <span className="opspilot-cause-label">根因:</span>
                                        <span className="opspilot-cause-text">{rootCauseDone.root_cause}</span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 修复方案 */}
                        {fixPlanDone?.fix_steps && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">fix-plan</span>
                                    <span className="opspilot-stage-title">修复方案</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="opspilot-fix-steps">
                                        {fixPlanDone.fix_steps.map((step, i) => (
                                            <div key={i} className="opspilot-fix-step">
                                                <div className="opspilot-fix-step-num">{i + 1}</div>
                                                <div className="opspilot-fix-step-body">
                                                    <div className="opspilot-fix-step-action">{step.action}</div>
                                                    <div className="opspilot-fix-step-meta">
                                                        <span className={`fix-type type-${step.type}`}>{step.type}</span>
                                                        <span className={`fix-risk risk-${step.risk}`}>风险: {step.risk}</span>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 预防建议 */}
                        {preventionDone?.prevention && (
                            <div className="opspilot-stage-card">
                                <div className="opspilot-stage-header">
                                    <span className="opspilot-stage-worker">prevention</span>
                                    <span className="opspilot-stage-title">预防建议</span>
                                    <span className="opspilot-stage-status done">✅ 完成</span>
                                </div>
                                <div className="opspilot-stage-body">
                                    <div className="err-prevention-list">
                                        {preventionDone.prevention.map((p, i) => (
                                            <div key={i} className="err-prevention-item">
                                                <span className="err-prevention-icon">🛡️</span>
                                                <span>{p}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 汇总报告 */}
                        {summaryDone?.report && (
                            <div className="opspilot-summary-card err-report-summary">
                                <div className="opspilot-summary-header">
                                    <span className="opspilot-summary-icon">{summaryDone.report.icon}</span>
                                    <span className="opspilot-summary-title">错误处理报告 — {summaryDone.report.title}</span>
                                </div>
                                <div className="opspilot-summary-body">
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">严重级别:</span>
                                        <span className={`level-badge level-${summaryDone.report.severity}`}>{summaryDone.report.severity}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">影响模块:</span>
                                        <span>{summaryDone.report.module}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">故障现象:</span>
                                        <span>{summaryDone.report.symptom}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">检查结果:</span>
                                        <span>✅ {summaryDone.report.checks_passed} 通过 · ❌ {summaryDone.report.checks_failed} 失败</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">根因:</span>
                                        <span>{summaryDone.report.root_cause}</span>
                                    </div>
                                    <div className="opspilot-summary-row">
                                        <span className="opspilot-summary-label">生成时间:</span>
                                        <span>{summaryDone.report.generated_at}</span>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* 空状态 */}
                    {errStages.length === 0 && !errRunning && (
                        <div className="opspilot-empty">
                            <div className="opspilot-empty-icon">
                                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                                    <path d="M12 9v4" /><path d="M12 17h.01" />
                                </svg>
                            </div>
                            <p className="opspilot-empty-title">网站错误处理报告</p>
                            <p className="opspilot-empty-sub">选择一个错误场景，自动执行诊断检查 → 根因分析 → 修复方案 → 预防建议</p>
                            <div className="opspilot-empty-workers">
                                <span>🔍 诊断检查</span>
                                <span>📍 根因分析</span>
                                <span>🔧 修复方案</span>
                                <span>🛡️ 预防建议</span>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    )
}

// ─── 子组件 ───────────────────────────────────────────

const MetricCard: React.FC<{ label: string; value: any; warn?: boolean; bad?: boolean }> = ({ label, value, warn, bad }) => (
    <div className={`opspilot-metric-card ${bad ? 'metric-bad' : warn ? 'metric-warn' : 'metric-ok'}`}>
        <div className="opspilot-metric-label">{label}</div>
        <div className="opspilot-metric-value">{value}</div>
    </div>
)

const ComparisonRow: React.FC<{ label: string; before: any; after: any; ok: (v: any) => boolean }> = ({ label, before, after, ok }) => {
    const isOk = ok(after)
    return (
        <tr>
            <td>{label}</td>
            <td className="cmp-before">{before}</td>
            <td className="cmp-after">{after}</td>
            <td className={isOk ? 'cmp-ok' : 'cmp-fail'}>{isOk ? '✅' : '❌'}</td>
        </tr>
    )
}

export default OpsPilotDemo