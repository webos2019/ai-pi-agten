/**
 * AgentManager — 真实多 Sub-Agent 管理面板
 *
 * 连接 newtask-pi 的真实子代理系统:
 * - 展示 7 种真实子代理类型 (research/analysis/writer/log_analyst/db_doctor/infra_inspector/remediation)
 * - 展示所有已注册工具 (25+ 个)
 * - 展示所有已注册技能 (12+ 个)
 * - 可直接执行真实子代理，查看运行结果
 * - 活动日志 + 执行记录
 *
 * 后端: sub_agent.py + agent_loop.py + tool_registry.py
 */

import { useState, useCallback, useEffect, useRef } from 'react'

// ── Markdown 渲染 (复用 ChatBody 的 marked 库) ──
function renderMarkdown(content: string): string {
    if (!content) return ''
    if (typeof (window as any).marked !== 'undefined') {
        ;(window as any).marked.setOptions({ breaks: true, gfm: true })
        let html = (window as any).marked.parse(content)
        // 清理 &nbsp;
        html = html.replace(/&nbsp;/g, ' ')
        return html
    }
    // 降级: 转义 HTML
    const div = document.createElement('div')
    div.textContent = String(content)
    return div.innerHTML
}

// ─── 类型定义 ─────────────────────────────────────────

interface SubAgentType {
    name: string
    description: string
    system_prompt: string
    tool_names: string[]
    model: string
    max_turns: number
}

interface ToolInfo {
    name: string
    description: string
    keywords: string[]
    planning_category: string
    decision_weight: number
    result_is_authoritative: boolean
}

interface SkillInfo {
    id: string
    name: string
    description: string
    tool_names: string[]
    routing_hints: string[]
    tags: string[]
}

interface ActivityEvent {
    id: string
    type: string
    message: string
    timestamp: string
    data?: unknown
}

interface ExecutionRecord {
    id: string
    agent_type: string
    task: string
    result_preview: string
    duration_ms: number
    status: string
    timestamp: string
}

interface ExecutionResult {
    agent_type: string
    task: string
    result: string
    duration_ms: number
    status: string
}

interface WorkerResult {
    worker_index: number
    agent_type: string
    task: string
    result: string
    duration_ms: number
    status: string
}

interface TeamWorkflowResult {
    workflow_id: string
    leader_task: string
    status: string
    worker_count: number
    workers: WorkerResult[]
    summary: string
    total_duration_ms: number
}

interface WorkerSpec {
    agent_type: string
    task: string
}

// ─── 串行 Pipeline 类型 ───────────────────────────────

interface PipelineStep {
    label: string
    agent_type: string
    task: string
}

interface PipelineStepResult {
    step_index: number
    label: string
    agent_type: string
    task: string
    result: string
    duration_ms: number
    status: string
}

interface PipelineResult {
    workflow_id: string
    workflow_type: string
    incident: Record<string, unknown>
    status: string
    step_count: number
    steps: PipelineStepResult[]
    summary: string
    total_duration_ms: number
    error?: string
}

interface MatrixWorkerResult {
    worker_index: number
    agent_type: string
    task: string
    result: string
    duration_ms: number
    status: string
}

interface MatrixResult {
    workflow_id: string
    workflow_type: string
    incident: Record<string, unknown>
    status: string
    worker_count: number
    workers: MatrixWorkerResult[]
    summary: string
    total_duration_ms: number
    error?: string
}

// ─── OA Team 类型 ────────────────────────────────────

interface OATeamInfo {
    team_id: string
    team_name: string
    description: string
    leader: string
    leader_agent_type: string
    leader_tools: string[]
    leader_max_turns: number
    workers: string[]
    worker_specs: Record<string, {
        role: string
        mission: string
        tools: string[]
        max_turns: number
        skills: string[]
        output_contract: Record<string, unknown>
        skill?: string  // 兼容旧字段
    }>
    rules: string[]
    room: {
        room_id: string
        status: string
        created_at: string
        message_count: number
    }
}

interface OATeamWorkerResult {
    worker_index: number
    agent_type: string
    role: string
    task: string
    result: string
    duration_ms: number
    status: string
}

interface OATeamResult {
    workflow_id: string
    workflow_type: string
    team_id: string
    team_leader: string
    room_id: string
    status: string
    worker_count: number
    workers: OATeamWorkerResult[]
    leader_summary: string
    summary: string
    total_duration_ms: number
    error?: string
}

interface PresetScenario {
    id: string
    incident_id: string
    scenario: string
    title: string
    severity: string
    customer: string
    environment: string
    description: string
    alerts: string[]
    hypotheses: string[]
    workers: { agent_type: string; task: string }[]
    steps: PipelineStep[]
}

// ─── 聊天室类型 ──────────────────────────────────────

interface ChatMessage {
    id: string
    role: string         // user / worker / leader / system
    sender: string       // 发送者名称
    content: string
    mention: string      // 被 @ 的 worker
    duration_ms: number
    status: string       // done / error / thinking
    timestamp: string
}

interface ChatRoomInfo {
    room_id: string
    members: string[]
    status: string
    created_at: string
    message_count: number
    worker_status: Record<string, string>
    current_task_id?: string
}

interface ChatTask {
    id: string
    title: string
    message_count: number
    created_at: string
    last_active_at: number
    is_current: boolean
    worker_status?: Record<string, string>
}

// ─── API ──────────────────────────────────────────────

const API_BASE = '/api/agents-mgr'

async function apiGet(path: string) {
    const resp = await fetch(`${API_BASE}${path}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
}

async function apiPost(path: string, body: unknown) {
    const resp = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }))
        throw new Error(err.error || `HTTP ${resp.status}`)
    }
    return resp.json()
}

// ─── 子代理图标和颜色 ─────────────────────────────────

const agentIcons: Record<string, string> = {
    research: '🔍',
    analysis: '📊',
    writer: '✍️',
    log_analyst: '📋',
    db_doctor: '🗄️',
    infra_inspector: '🖥️',
    remediation: '🔧',
    task_analyzer: '📐',
    change_executor: '⚡',
    result_verifier: '✅',
}

const agentColors: Record<string, string> = {
    research: 'amt-agent-research',
    analysis: 'amt-agent-analysis',
    writer: 'amt-agent-writer',
    log_analyst: 'amt-agent-log',
    db_doctor: 'amt-agent-db',
    infra_inspector: 'amt-agent-infra',
    remediation: 'amt-agent-remediation',
    task_analyzer: 'amt-agent-task',
    change_executor: 'amt-agent-change',
    result_verifier: 'amt-agent-verifier',
}

// ─── 主组件 ───────────────────────────────────────────

type Tab = 'sub-agents' | 'tools' | 'skills' | 'execute' | 'team' | 'incident' | 'oa-team' | 'activity'

const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: 'sub-agents', label: 'Sub-Agents', icon: '🤖' },
    { id: 'tools', label: '工具注册表', icon: '🛠️' },
    { id: 'skills', label: '技能', icon: '⚡' },
    { id: 'execute', label: '执行子代理', icon: '▶️' },
    { id: 'team', label: '团队工作流', icon: '👥' },
    { id: 'incident', label: '故障场景', icon: '🚨' },
    { id: 'oa-team', label: 'OA Team', icon: '🏢' },
    { id: 'activity', label: '活动日志', icon: '📊' },
]

export default function AgentManager() {
    const [activeTab, setActiveTab] = useState<Tab>('sub-agents')
    const [subAgents, setSubAgents] = useState<SubAgentType[]>([])
    const [tools, setTools] = useState<ToolInfo[]>([])
    const [skills, setSkills] = useState<SkillInfo[]>([])
    const [activities, setActivities] = useState<ActivityEvent[]>([])
    const [executions, setExecutions] = useState<ExecutionRecord[]>([])
    const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

    // 执行表单状态
    const [execForm, setExecForm] = useState({ agent_type: 'research', task: '' })
    const [execResult, setExecResult] = useState<ExecutionResult | null>(null)
    const [executing, setExecuting] = useState(false)

    // 团队工作流状态 — 固定流程，不需要选择 agent
    const [wfLeaderTask, setWfLeaderTask] = useState('')
    const [wfResult, setWfResult] = useState<TeamWorkflowResult | null>(null)
    const [wfExecuting, setWfExecuting] = useState(false)

    // 串行 Pipeline / Matrix Team 状态
    const [scenarios, setScenarios] = useState<PresetScenario[]>([])
    const [selectedScenarioId, setSelectedScenarioId] = useState('')
    const [pipeResult, setPipeResult] = useState<PipelineResult | null>(null)
    const [pipeExecuting, setPipeExecuting] = useState(false)
    const [matrixResult, setMatrixResult] = useState<MatrixResult | null>(null)
    const [matrixExecuting, setMatrixExecuting] = useState(false)

    // OA Team 状态
    const [oaTeamInfo, setOaTeamInfo] = useState<OATeamInfo | null>(null)
    const [oaMessage, setOaMessage] = useState('@oa-team-leader ')
    const [oaResult, setOaResult] = useState<OATeamResult | null>(null)
    const [oaExecuting, setOaExecuting] = useState(false)

    // 聊天室状态 — 团队工作流聊天室
    const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
    const [chatInput, setChatInput] = useState('')
    const [chatSending, setChatSending] = useState(false)
    const [chatRoomInfo, setChatRoomInfo] = useState<ChatRoomInfo | null>(null)
    const chatEndRef = useRef<HTMLDivElement | null>(null)
    const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
    const chatScrollRef = useRef<HTMLDivElement | null>(null)
    const [showScrollBtn, setShowScrollBtn] = useState(false)
    // 记录用户是否手动向上滚动（用于阻止轮询时自动滚到底部）
    const userScrolledUp = useRef(false)

    // 多任务列表状态
    const [chatTasks, setChatTasks] = useState<ChatTask[]>([])
    const [currentTaskId, setCurrentTaskId] = useState('')
    const [taskCreating, setTaskCreating] = useState(false)
    const [taskRenamingId, setTaskRenamingId] = useState('')
    const [taskRenameValue, setTaskRenameValue] = useState('')

    // @ 自动补全下拉菜单状态
    const [mentionMenu, setMentionMenu] = useState<{
        show: boolean
        query: string
        items: { id: string; label: string; icon: string }[]
        selectedIndex: number
    }>({ show: false, query: '', items: [], selectedIndex: 0 })

    // 可选的 @ 成员列表
    const MENTIONABLE_MEMBERS = [
        { id: 'oa_team_leader', label: 'oa_team_leader', icon: '👑' },
        { id: 'task_analyzer', label: 'task_analyzer', icon: '📐' },
        { id: 'change_executor', label: 'change_executor', icon: '⚡' },
        { id: 'result_verifier', label: 'result_verifier', icon: '✅' },
    ]

    const showMessage = useCallback((text: string, type: 'success' | 'error' = 'success') => {
        setMessage({ type, text })
        setTimeout(() => setMessage(null), 5000)
    }, [])

    const refresh = useCallback(async () => {
        try {
            const snapshot = await apiGet('/snapshot')
            setSubAgents(snapshot.sub_agent_types || [])
            setTools(snapshot.tools || [])
            setSkills(snapshot.skills || [])
            setActivities(snapshot.activities || [])
            setExecutions(snapshot.executions || [])
        } catch {
            // ignore
        }
    }, [])

    // 加载预设场景
    useEffect(() => {
        apiGet('/preset-scenarios').then(data => {
            const list: PresetScenario[] = data.scenarios || []
            setScenarios(list)
            if (list.length > 0 && !selectedScenarioId) {
                setSelectedScenarioId(list[0].id)
            }
        }).catch(() => {})
        // 加载 OA Team 信息
        loadOaTeamInfo()
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    useEffect(() => {
        refresh()
        const interval = setInterval(refresh, 10000)
        return () => clearInterval(interval)
    }, [refresh])

    // ── 执行子代理 ──
    const handleExecute = async () => {
        if (!execForm.task.trim()) {
            showMessage('请输入任务描述', 'error')
            return
        }
        setExecuting(true)
        setExecResult(null)
        try {
            const result = await apiPost('/execute', execForm)
            setExecResult(result)
            if (result.status === 'success') {
                showMessage(`子代理 ${result.agent_type} 执行完成 (${result.duration_ms}ms)`)
            } else {
                showMessage(`子代理执行失败: ${result.result.slice(100)}`, 'error')
            }
            refresh()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '执行失败', 'error')
        } finally {
            setExecuting(false)
        }
    }

    // ── 执行团队工作流 (固定流程) ──
    const handleTeamWorkflow = async () => {
        if (!wfLeaderTask.trim()) {
            showMessage('请输入故障描述', 'error')
            return
        }
        setWfExecuting(true)
        setWfResult(null)
        try {
            // 固定 3 个 Worker + 任务，不需要用户选择
            const fixedWorkers = [
                { agent_type: 'task_analyzer', task: `你是团队工作流的 task-analyzer worker。\n故障描述: ${wfLeaderTask}\n\n请执行：\n1. 使用 system_monitor 检查系统状态\n2. 使用 service_check 检查服务可达性\n3. 使用 log_search 搜索相关日志\n\n输出：故障分类、影响范围、严重等级、根因排查方向` },
                { agent_type: 'change_executor', task: `你是团队工作流的 change-executor worker。\n故障描述: ${wfLeaderTask}\n\n请执行：\n1. 使用 service_check 确认执行前基线\n2. 使用 system_monitor 确认资源状态\n3. 使用 log_search 搜索变更日志\n\n输出：紧急止血方案、数据恢复方案、回滚方案` },
                { agent_type: 'result_verifier', task: `你是团队工作流的 result-verifier worker。\n故障描述: ${wfLeaderTask}\n\n请执行：\n1. 使用 service_check 验证服务恢复\n2. 使用 system_monitor 检查资源恢复\n3. 使用 log_search 确认无新错误\n\n输出：恢复验证清单、Incident 关闭建议` },
            ]
            const result = await apiPost('/team-workflow', {
                leader_task: wfLeaderTask,
                workers: fixedWorkers,
            })
            setWfResult(result)
            if (result.status === 'completed') {
                showMessage(`团队工作流完成 — ${result.worker_count} 个 worker，总耗时 ${result.total_duration_ms}ms`)
            } else {
                showMessage('团队工作流执行失败', 'error')
            }
            refresh()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '执行失败', 'error')
        } finally {
            setWfExecuting(false)
        }
    }

    // ── 执行串行 Pipeline ──
    const handlePipelineWorkflow = async () => {
        if (!selectedScenarioId) {
            showMessage('请选择一个预设场景', 'error')
            return
        }
        setPipeExecuting(true)
        setPipeResult(null)
        try {
            const result = await apiPost('/pipeline-workflow', {
                scenario_id: selectedScenarioId,
            })
            setPipeResult(result)
            if (result.status === 'completed') {
                showMessage(`串行 Pipeline 完成 — ${result.step_count} 步，总耗时 ${result.total_duration_ms}ms`)
            } else {
                showMessage('串行 Pipeline 执行失败', 'error')
            }
            refresh()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '执行失败', 'error')
        } finally {
            setPipeExecuting(false)
        }
    }

    // ── 执行 Matrix Team 并发工作流 ──
    const handleMatrixWorkflow = async () => {
        if (!selectedScenarioId) {
            showMessage('请选择一个预设场景', 'error')
            return
        }
        setMatrixExecuting(true)
        setMatrixResult(null)
        try {
            const result = await apiPost('/matrix-workflow', {
                scenario_id: selectedScenarioId,
            })
            setMatrixResult(result)
            if (result.status === 'completed') {
                showMessage(`Matrix Team 完成 — ${result.worker_count} 个 worker 并发，总耗时 ${result.total_duration_ms}ms`)
            } else {
                showMessage('Matrix Team 执行失败', 'error')
            }
            refresh()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '执行失败', 'error')
        } finally {
            setMatrixExecuting(false)
        }
    }

    // ── 加载 OA Team 信息 ──
    const loadOaTeamInfo = useCallback(async () => {
        try {
            const data = await apiGet('/teams/oa-team')
            setOaTeamInfo(data)
        } catch {
            // ignore
        }
    }, [])

    // ── 执行 OA Team 工作流 ──
    const handleOaTeamExecute = async () => {
        if (!oaMessage.trim()) {
            showMessage('请输入消息 (以 @oa-team-leader 开头)', 'error')
            return
        }
        setOaExecuting(true)
        setOaResult(null)
        try {
            const result = await apiPost('/oa-team/execute', {
                message: oaMessage,
            })
            setOaResult(result)
            if (result.status === 'completed') {
                showMessage(`OA Team 完成 — ${result.worker_count} 个 worker 并发，总耗时 ${result.total_duration_ms}ms`)
            } else {
                showMessage('OA Team 执行失败', 'error')
            }
            refresh()
            loadOaTeamInfo()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '执行失败', 'error')
        } finally {
            setOaExecuting(false)
        }
    }

    // ── 聊天室: 加载任务列表 ──
    const loadChatTasks = useCallback(async () => {
        try {
            const data = await apiGet('/chat-room/tasks')
            const tasks = data.tasks || []
            setChatTasks(tasks)
            // 同步当前选中任务
            const cur = tasks.find((t: ChatTask) => t.is_current)
            if (cur) {
                setCurrentTaskId(cur.id)
            } else if (tasks.length > 0 && !currentTaskId) {
                setCurrentTaskId(tasks[0].id)
            }
        } catch {
            // ignore
        }
    }, [])

    // ── 聊天室: 加载消息历史 ──
    const loadChatHistory = useCallback(async () => {
        try {
            const data = await apiGet('/chat-room/history?limit=200')
            setChatMessages(data.messages || [])
            // 仅在非手动向上滚动时自动滚到底部
            if (!userScrolledUp.current) {
                setTimeout(() => {
                    chatEndRef.current?.scrollIntoView({ behavior: 'auto' })
                }, 50)
            }
        } catch {
            // ignore
        }
    }, [])

    // ── 聊天室: 加载房间信息 ──
    const loadChatRoomInfo = useCallback(async () => {
        try {
            const data = await apiGet('/chat-room')
            setChatRoomInfo(data)
            if (data.current_task_id && data.current_task_id !== currentTaskId) {
                setCurrentTaskId(data.current_task_id)
            }
        } catch {
            // ignore
        }
    }, [currentTaskId])

    // ── 聊天室: 创建新任务 ──
    const handleCreateTask = async () => {
        setTaskCreating(true)
        try {
            const title = window.prompt('请输入任务名称', `任务-${chatTasks.length + 1}`)
            if (!title) return
            await apiPost('/chat-room/tasks/create', { title })
            await loadChatTasks()
            await loadChatHistory()
            await loadChatRoomInfo()
            showMessage('新任务已创建')
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '创建失败', 'error')
        } finally {
            setTaskCreating(false)
        }
    }

    // ── 聊天室: 切换任务 ──
    const handleSelectTask = async (taskId: string) => {
        if (taskId === currentTaskId) return
        try {
            await apiPost('/chat-room/tasks/select', { task_id: taskId })
            setCurrentTaskId(taskId)
            userScrolledUp.current = false
            await loadChatHistory()
            await loadChatRoomInfo()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '切换失败', 'error')
        }
    }

    // ── 聊天室: 删除任务 ──
    const handleDeleteTask = async (taskId: string, e: React.MouseEvent) => {
        e.stopPropagation()
        if (!window.confirm('确定删除该任务及其消息历史?')) return
        try {
            await apiPost('/chat-room/tasks/delete', { task_id: taskId })
            await loadChatTasks()
            await loadChatHistory()
            await loadChatRoomInfo()
            showMessage('任务已删除')
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '删除失败', 'error')
        }
    }

    // ── 聊天室: 重命名任务 ──
    const handleRenameTask = async (taskId: string) => {
        const newName = window.prompt('请输入新的任务名称', taskRenameValue)
        if (!newName) return
        try {
            await apiPost('/chat-room/tasks/rename', { task_id: taskId, title: newName })
            await loadChatTasks()
            showMessage('任务已重命名')
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '重命名失败', 'error')
        } finally {
            setTaskRenamingId('')
        }
    }

    // ── 聊天室: 发送消息（SSE 驱动模式 + 轮询降级） ──
    const handleChatSend = async () => {
        const msg = chatInput.trim()
        if (!msg) {
            showMessage('请输入消息', 'error')
            return
        }
        if (chatSending) return  // 防止重复发送
        setChatSending(true)
        setChatInput('')  // 清空输入框

        try {
            // 使用 SSE 流式接口
            const resp = await fetch(`${API_BASE}/chat-room/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: msg }),
            })
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

            const reader = resp.body!.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            let usePollingFallback = false

            while (true) {
                const { done, value } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })

                // 解析 SSE 数据行 (data: {...}\n\n)
                const lines = buffer.split('\n\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    const dataLine = line.trim()
                    if (!dataLine.startsWith('data: ')) continue
                    const jsonStr = dataLine.slice(6)
                    try {
                        const event = JSON.parse(jsonStr)
                        if (event.type === 'message' && event.message) {
                            // 实时追加消息
                            setChatMessages(prev => {
                                // 避免重复 (按 id 去重)
                                if (prev.some(m => m.id === event.message.id)) return prev
                                const next = [...prev, event.message as ChatMessage]
                                if (!userScrolledUp.current) {
                                    setTimeout(() => {
                                        chatEndRef.current?.scrollIntoView({ behavior: 'auto' })
                                    }, 50)
                                }
                                return next
                            })
                        } else if (event.type === 'worker_status' && event.worker) {
                            // 更新 worker 状态
                            setChatRoomInfo(prev => prev ? {
                                ...prev,
                                worker_status: {
                                    ...(prev.worker_status || {}),
                                    [event.worker]: event.status,
                                },
                            } : prev)
                        } else if (event.type === 'done') {
                            // 流结束，刷新 tasks 列表
                            loadChatTasks()
                        } else if (event.type === 'error') {
                            showMessage(event.error || '处理出错', 'error')
                            usePollingFallback = true
                        }
                    } catch { /* skip invalid JSON */ }
                }
            }

            // 流结束后最终刷新一次完整数据
            await loadChatHistory()
            await loadChatRoomInfo()
            await loadChatTasks()

            // 如果流式出错，降级到轮询
            if (usePollingFallback) {
                showMessage('流式连接异常，已降级为轮询模式')
            }
        } catch (err) {
            // 流式失败 → 降级到原有轮询模式
            showMessage('SSE 连接失败，降级为轮询模式', 'error')
            try {
                await apiPost('/chat-room/send', { message: msg })
                await loadChatTasks()
                await loadChatHistory()
                await loadChatRoomInfo()
            } catch (err2) {
                showMessage(err2 instanceof Error ? err2.message : '发送失败', 'error')
                setChatInput(msg)
            }
        } finally {
            setChatSending(false)
        }
    }

    // ── 聊天室: 停止任务 ──
    const handleChatStop = async () => {
        try {
            await apiPost('/chat-room/stop', {})
            showMessage('任务停止信号已发送，正在等待 worker 中断...')
            await loadChatHistory()
            await loadChatRoomInfo()
            await loadChatTasks()
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '停止失败', 'error')
        }
    }

    // ── 聊天室: 清空历史 ──
    const handleChatClear = async () => {
        try {
            await apiPost('/chat-room/clear', {})
            await loadChatHistory()
            await loadChatRoomInfo()
            showMessage('聊天室已清空')
        } catch (err) {
            showMessage(err instanceof Error ? err.message : '清空失败', 'error')
        }
    }

    // ── 聊天室: 滚动到底部 ──
    const scrollToBottom = useCallback((smooth = true) => {
        userScrolledUp.current = false
        setShowScrollBtn(false)
        chatEndRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto' })
    }, [])

    // ── 聊天室: 检测消息区滚动位置 ──
    const handleChatScroll = useCallback(() => {
        const el = chatScrollRef.current
        if (!el) return
        // 距离底部超过 80px 时显示按钮
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
        setShowScrollBtn(distFromBottom > 80)
        // 记录用户是否手动向上滚动
        userScrolledUp.current = distFromBottom > 80
    }, [])

    // ── 聊天室: 快捷 @worker 按钮 ──
    const insertMention = (worker: string) => {
        setChatInput(prev => {
            const mention = `@${worker} `
            if (prev.includes(mention)) return prev  // 已存在不重复
            return mention + (prev || '')
        })
        // 关闭下拉菜单
        setMentionMenu(prev => ({ ...prev, show: false }))
        // 聚焦回输入框
        setTimeout(() => chatInputRef.current?.focus(), 0)
    }

    // ── 聊天室: 处理输入框变更，检测 @ 触发 ──
    const handleChatInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const value = e.target.value
        const cursorPos = e.target.selectionStart
        setChatInput(value)

        // 检测光标位置前面是否有未完成的 @ 提及
        // 找到光标前面最后一个 @ 符号
        const beforeCursor = value.slice(0, cursorPos)
        const atMatch = beforeCursor.match(/@([\w]*)$/)

        if (atMatch) {
            const query = atMatch[1].toLowerCase()
            // 检查 @ 是否在词的边界（开头、空格后）
            const charBeforeAt = beforeCursor.length - atMatch[0].length - 1
            const isBoundary = charBeforeAt < 0 || /\s/.test(beforeCursor[charBeforeAt])
            if (isBoundary) {
                // 过滤匹配的成员
                const filtered = MENTIONABLE_MEMBERS.filter(m =>
                    m.id.toLowerCase().includes(query)
                )
                if (filtered.length > 0) {
                    setMentionMenu({
                        show: true,
                        query,
                        items: filtered,
                        selectedIndex: 0,
                    })
                } else {
                    setMentionMenu(prev => ({ ...prev, show: false }))
                }
            } else {
                setMentionMenu(prev => ({ ...prev, show: false }))
            }
        } else {
            setMentionMenu(prev => ({ ...prev, show: false }))
        }
    }

    // ── 聊天室: 处理下拉菜单键盘导航 ──
    const handleChatInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // 下拉菜单打开时优先处理导航
        if (mentionMenu.show && mentionMenu.items.length > 0) {
            if (e.key === 'ArrowDown') {
                e.preventDefault()
                setMentionMenu(prev => ({
                    ...prev,
                    selectedIndex: (prev.selectedIndex + 1) % prev.items.length,
                }))
                return
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault()
                setMentionMenu(prev => ({
                    ...prev,
                    selectedIndex: (prev.selectedIndex - 1 + prev.items.length) % prev.items.length,
                }))
                return
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault()
                const selected = mentionMenu.items[mentionMenu.selectedIndex]
                if (selected) {
                    selectMention(selected.id)
                }
                return
            }
            if (e.key === 'Escape') {
                e.preventDefault()
                setMentionMenu(prev => ({ ...prev, show: false }))
                return
            }
        }

        // 原有逻辑：Enter 发送
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleChatSend()
        }
    }

    // ── 聊天室: 从下拉菜单选择成员 ──
    const selectMention = (workerId: string) => {
        const value = chatInput
        const cursorPos = chatInputRef.current?.selectionStart ?? value.length

        // 找到光标前的 @xxx 部分，替换为 @workerId
        const beforeCursor = value.slice(0, cursorPos)
        const afterCursor = value.slice(cursorPos)
        const atMatch = beforeCursor.match(/@[\w]*$/)
        if (atMatch) {
            const newBefore = beforeCursor.slice(0, -atMatch[0].length)
            const mention = `@${workerId} `
            const newValue = newBefore + mention + afterCursor
            setChatInput(newValue)
            // 设置光标位置到提及之后
            const newCursorPos = newBefore.length + mention.length
            setTimeout(() => {
                chatInputRef.current?.focus()
                chatInputRef.current?.setSelectionRange(newCursorPos, newCursorPos)
            }, 0)
        }
        setMentionMenu(prev => ({ ...prev, show: false }))
    }

    // ── 聊天室: 初始化 + 低频兜底轮询 ──
    // SSE 流式驱动是主要模式；轮询仅做兜底（5秒），防止多 tab/设备状态不同步
    useEffect(() => {
        if (activeTab === 'team') {
            loadChatTasks()
            loadChatHistory()
            loadChatRoomInfo()
            const interval = setInterval(() => {
                // 低频兜底轮询，仅在非发送状态下刷新
                if (!chatSending) {
                    loadChatTasks()
                    loadChatHistory()
                    loadChatRoomInfo()
                }
            }, 5000)  // 5秒兜底轮询
            return () => clearInterval(interval)
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab, chatSending])

    const selectedScenario = scenarios.find(s => s.id === selectedScenarioId)

    const renderContent = () => {
        switch (activeTab) {
            case 'sub-agents':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">Sub-Agents (子代理类型)</h1>
                            <p className="amt-page-subtitle">{subAgents.length} 种真实子代理类型，每种有独立的 system_prompt + 工具子集 + agent_loop</p>
                        </div>
                        <div className="amt-grid-3">
                            {subAgents.map(agent => (
                                <div key={agent.name} className={`amt-card amt-agent-card ${agentColors[agent.name] || ''}`}>
                                    <div className="amt-agent-header">
                                        <div className="amt-agent-icon-big">{agentIcons[agent.name] || '🤖'}</div>
                                        <div className="amt-agent-info-block">
                                            <h3 className="amt-agent-name-big">{agent.name}</h3>
                                            <p className="amt-agent-desc">{agent.description}</p>
                                        </div>
                                    </div>
                                    <div className="amt-agent-meta">
                                        <div className="amt-meta-row"><span className="amt-meta-label">Model</span><span className="amt-tag">{agent.model}</span></div>
                                        <div className="amt-meta-row"><span className="amt-meta-label">Max Turns</span><span className="amt-tag">{agent.max_turns}</span></div>
                                        <div className="amt-meta-row">
                                            <span className="amt-meta-label">Tools ({agent.tool_names.length})</span>
                                        </div>
                                        <div className="amt-tag-list">
                                            {agent.tool_names.length > 0 ? agent.tool_names.map(t => <span key={t} className="amt-tag">{t}</span>) : <span className="amt-meta-empty">无工具 (纯 LLM)</span>}
                                        </div>
                                    </div>
                                    <details className="amt-prompt-details">
                                        <summary className="amt-prompt-summary">查看 System Prompt</summary>
                                        <pre className="amt-prompt-pre">{agent.system_prompt}</pre>
                                    </details>
                                </div>
                            ))}
                        </div>
                    </div>
                )

            case 'tools':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">工具注册表</h1>
                            <p className="amt-page-subtitle">{tools.length} 个已注册工具，通过 tool_registry 统一管理</p>
                        </div>
                        <div className="amt-card amt-tools-card">
                            <table className="amt-tools-table">
                                <thead>
                                    <tr>
                                        <th>工具名</th>
                                        <th>描述</th>
                                        <th>分类</th>
                                        <th>权重</th>
                                        <th>权威</th>
                                        <th>关键词</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {tools.map(tool => (
                                        <tr key={tool.name}>
                                            <td className="amt-tool-name-cell">{tool.name}</td>
                                            <td className="amt-tool-desc-cell">{tool.description}</td>
                                            <td><span className="amt-tag">{tool.planning_category}</span></td>
                                            <td className="amt-tool-weight">{(tool.decision_weight * 100).toFixed(0)}%</td>
                                            <td>{tool.result_is_authoritative ? '✅' : '—'}</td>
                                            <td>
                                                <div className="amt-tag-list-inline">
                                                    {tool.keywords.slice(0, 5).map(k => <span key={k} className="amt-tag-sm">{k}</span>)}
                                                    {tool.keywords.length > 5 && <span className="amt-tag-sm">+{tool.keywords.length - 5}</span>}
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )

            case 'skills':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">技能 (Skills)</h1>
                            <p className="amt-page-subtitle">{skills.length} 个已注册技能，每个技能定义独立的工具集和系统提示</p>
                        </div>
                        <div className="amt-grid-2">
                            {skills.map(skill => (
                                <div key={skill.id} className="amt-card amt-skill-card">
                                    <div className="amt-skill-header">
                                        <div className="amt-skill-icon">⚡</div>
                                        <div>
                                            <h3 className="amt-skill-name">{skill.name}</h3>
                                            <p className="amt-skill-id">{skill.id}</p>
                                        </div>
                                    </div>
                                    <p className="amt-skill-desc">{skill.description}</p>
                                    <div className="amt-skill-tools">
                                        <span className="amt-meta-label">工具 ({skill.tool_names.length}):</span>
                                        <div className="amt-tag-list">
                                            {skill.tool_names.map(t => <span key={t} className="amt-tag">{t}</span>)}
                                        </div>
                                    </div>
                                    {skill.routing_hints.length > 0 && (
                                        <div className="amt-skill-hints">
                                            <span className="amt-meta-label">路由关键词:</span>
                                            <div className="amt-tag-list-inline">
                                                {skill.routing_hints.slice(0, 8).map(h => <span key={h} className="amt-tag-sm">{h}</span>)}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                )

            case 'execute':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">执行子代理</h1>
                            <p className="amt-page-subtitle">选择子代理类型并输入任务，直接运行真实的 agent_loop</p>
                        </div>
                        <div className="amt-card amt-exec-card">
                            <div className="amt-exec-form">
                                <div className="amt-form-grid">
                                    <div>
                                        <label className="amt-label">子代理类型</label>
                                        <select
                                            value={execForm.agent_type}
                                            onChange={e => setExecForm({ ...execForm, agent_type: e.target.value })}
                                            className="amt-input"
                                        >
                                            {subAgents.map(a => <option key={a.name} value={a.name}>{agentIcons[a.name] || '🤖'} {a.name} — {a.description.slice(0, 40)}</option>)}
                                        </select>
                                    </div>
                                    <div className="amt-form-col-span-2">
                                        <label className="amt-label">任务描述</label>
                                        <textarea
                                            value={execForm.task}
                                            onChange={e => setExecForm({ ...execForm, task: e.target.value })}
                                            placeholder="例如：搜索最新的 AI Agent 框架并总结关键特性"
                                            className="amt-input amt-textarea"
                                            rows={3}
                                        />
                                    </div>
                                </div>
                                <button
                                    onClick={handleExecute}
                                    disabled={executing}
                                    className="amt-btn amt-btn-indigo"
                                >
                                    {executing ? '⏳ 执行中... (子代理独立运行 agent_loop)' : '▶️ 执行子代理'}
                                </button>
                            </div>
                        </div>

                        {/* 执行结果 */}
                        {execResult && (
                            <div className={`amt-card amt-result-card ${execResult.status === 'success' ? 'amt-result-success' : 'amt-result-error'}`}>
                                <div className="amt-result-header">
                                    <span className="amt-result-agent">{agentIcons[execResult.agent_type] || '🤖'} {execResult.agent_type}</span>
                                    <span className={`amt-status-badge ${execResult.status === 'success' ? 'amt-badge-green' : 'amt-badge-red'}`}>
                                        {execResult.status}
                                    </span>
                                    <span className="amt-result-duration">{execResult.duration_ms}ms</span>
                                </div>
                                <div className="amt-result-task">任务: {execResult.task}</div>
                                <div className="amt-result-text">{execResult.result}</div>
                            </div>
                        )}

                        {/* 历史执行记录 */}
                        {executions.length > 0 && (
                            <div className="amt-card amt-exec-history-card">
                                <h3 className="amt-form-title">📋 执行历史 ({executions.length})</h3>
                                <div className="amt-exec-list">
                                    {executions.map(rec => (
                                        <div key={rec.id} className="amt-exec-item">
                                            <div className="amt-exec-item-header">
                                                <span className="amt-exec-agent">{agentIcons[rec.agent_type] || '🤖'} {rec.agent_type}</span>
                                                <span className={`amt-status-badge ${rec.status === 'success' ? 'amt-badge-green' : 'amt-badge-red'}`}>{rec.status}</span>
                                                <span className="amt-exec-duration">{rec.duration_ms}ms</span>
                                                <span className="amt-exec-time">{rec.timestamp.slice(11)}</span>
                                            </div>
                                            <div className="amt-exec-task">{rec.task}</div>
                                            <div className="amt-exec-preview">{rec.result_preview}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )

            case 'activity':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">活动日志</h1>
                            <p className="amt-page-subtitle">Agent 系统操作事件流</p>
                        </div>
                        {activities.length === 0 ? (
                            <div className="amt-empty">暂无活动记录。执行子代理后这里会显示事件。</div>
                        ) : (
                            <div className="amt-card amt-activity-card">
                                <div className="amt-activity-list">
                                    {activities.map(activity => (
                                        <div key={activity.id} className="amt-activity-item">
                                            <span className="amt-activity-icon">{activityIcons[activity.type] || 'ℹ️'}</span>
                                            <span className="amt-activity-message">{activity.message}</span>
                                            <span className="amt-activity-time">{activity.timestamp.slice(11)}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )

            case 'incident':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">故障场景 (Matrix Team)</h1>
                            <p className="amt-page-subtitle">预设真实运维故障，3 个 worker (task_analyzer / change_executor / result_verifier) 一次分配并发执行</p>
                        </div>

                        {/* 场景选择器 */}
                        <div className="amt-card amt-exec-card">
                            <div className="amt-form-grid">
                                <div className="amt-form-col-span-2">
                                    <label className="amt-label">选择故障场景</label>
                                    <select
                                        value={selectedScenarioId}
                                        onChange={e => setSelectedScenarioId(e.target.value)}
                                        className="amt-input"
                                    >
                                        {scenarios.map(s => (
                                            <option key={s.id} value={s.id}>
                                                [{s.severity}] {s.incident_id} — {s.title}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                            </div>

                            {/* 场景详情 */}
                            {selectedScenario && (
                                <div className="amt-incident-detail">
                                    <div className="amt-incident-meta-grid">
                                        <div className="amt-incident-meta-item">
                                            <span className="amt-meta-label">Incident ID</span>
                                            <span className="amt-incident-meta-val">{selectedScenario.incident_id}</span>
                                        </div>
                                        <div className="amt-incident-meta-item">
                                            <span className="amt-meta-label">严重等级</span>
                                            <span className={`amt-status-badge ${selectedScenario.severity === 'P0' ? 'amt-badge-red' : 'amt-badge-orange'}`}>{selectedScenario.severity}</span>
                                        </div>
                                        <div className="amt-incident-meta-item">
                                            <span className="amt-meta-label">客户</span>
                                            <span className="amt-incident-meta-val">{selectedScenario.customer}</span>
                                        </div>
                                        <div className="amt-incident-meta-item">
                                            <span className="amt-meta-label">环境</span>
                                            <span className="amt-incident-meta-val">{selectedScenario.environment}</span>
                                        </div>
                                    </div>

                                    <div className="amt-incident-section">
                                        <h4 className="amt-incident-section-title">📋 故障描述</h4>
                                        <p className="amt-incident-description">{selectedScenario.description}</p>
                                    </div>

                                    <div className="amt-incident-section">
                                        <h4 className="amt-incident-section-title">🚨 初始告警</h4>
                                        <ul className="amt-incident-alerts">
                                            {selectedScenario.alerts.map((a, i) => (
                                                <li key={i} className="amt-incident-alert-item">{a}</li>
                                            ))}
                                        </ul>
                                    </div>

                                    <div className="amt-incident-section">
                                        <h4 className="amt-incident-section-title">🔎 排查假设</h4>
                                        <ul className="amt-incident-alerts">
                                            {selectedScenario.hypotheses.map((h, i) => (
                                                <li key={i} className="amt-incident-alert-item">{h}</li>
                                            ))}
                                        </ul>
                                    </div>

                                    {/* Matrix Team worker 列表 */}
                                    <div className="amt-incident-section">
                                        <h4 className="amt-incident-section-title">👥 Matrix Team Workers ({selectedScenario.workers?.length || 0}) — 一次分配并发执行</h4>
                                        <div className="amt-matrix-workers">
                                            {(selectedScenario.workers || []).map((w, i) => (
                                                <div key={i} className="amt-matrix-worker-card">
                                                    <div className="amt-matrix-worker-header">
                                                        <span className="amt-matrix-worker-icon">{agentIcons[w.agent_type] || '🤖'}</span>
                                                        <div>
                                                            <span className="amt-matrix-worker-name">{w.agent_type}</span>
                                                            <span className="amt-matrix-worker-role">Worker {i + 1}</span>
                                                        </div>
                                                    </div>
                                                    <div className="amt-matrix-worker-task">{w.task.slice(0, 150)}{w.task.length > 150 ? '...' : ''}</div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}

                            <div className="amt-matrix-actions">
                                <button
                                    onClick={handleMatrixWorkflow}
                                    disabled={matrixExecuting || !selectedScenario}
                                    className="amt-btn amt-btn-indigo"
                                >
                                    {matrixExecuting ? '⏳ Matrix Team 执行中... (3 worker 并发)' : '🚨 启动 Matrix Team (一次分配并发)'}
                                </button>
                                <button
                                    onClick={handlePipelineWorkflow}
                                    disabled={pipeExecuting || !selectedScenario}
                                    className="amt-btn amt-btn-violet"
                                >
                                    {pipeExecuting ? '⏳ 串行排查中...' : '🔗 串行 Pipeline (依次执行)'}
                                </button>
                            </div>
                        </div>

                        {/* Matrix Team 结果 */}
                        {matrixResult && (
                            <div className={`amt-card amt-result-card ${matrixResult.status === 'completed' ? 'amt-result-success' : 'amt-result-error'}`}>
                                <div className="amt-result-header">
                                    <span className="amt-result-agent">🚨 Matrix Team 并发执行</span>
                                    <span className={`amt-status-badge ${matrixResult.status === 'completed' ? 'amt-badge-green' : 'amt-badge-red'}`}>{matrixResult.status}</span>
                                    <span className="amt-result-duration">{matrixResult.total_duration_ms}ms</span>
                                </div>
                                <div className="amt-result-meta">Incident: {String(matrixResult.incident?.incident_id || '?')} | Worker 数: {matrixResult.worker_count}</div>

                                {/* 各 Worker 结果 */}
                                <div className="amt-wf-results">
                                    {matrixResult.workers.map(wr => (
                                        <div key={wr.worker_index} className="amt-wf-worker-result amt-matrix-result-card">
                                            <div className="amt-exec-item-header">
                                                <span className="amt-matrix-result-badge">Worker {wr.worker_index}</span>
                                                <span className="amt-exec-agent">{agentIcons[wr.agent_type] || '🤖'} {wr.agent_type}</span>
                                                <span className={`amt-status-badge ${wr.status === 'success' ? 'amt-badge-green' : 'amt-badge-red'}`}>{wr.status}</span>
                                                <span className="amt-exec-duration">{wr.duration_ms}ms</span>
                                            </div>
                                            <div className="amt-exec-task">任务: {wr.task.slice(0, 200)}{wr.task.length > 200 ? '...' : ''}</div>
                                            <div className="amt-result-text amt-wf-worker-text">{wr.result}</div>
                                        </div>
                                    ))}
                                </div>

                                {/* 组长汇总 */}
                                <div className="amt-wf-summary-section">
                                    <h3 className="amt-wf-summary-title">📋 组长故障处理报告</h3>
                                    <div className="amt-result-text amt-wf-summary-text">{matrixResult.summary}</div>
                                </div>
                            </div>
                        )}

                        {/* 串行 Pipeline 结果 (保留兼容) */}
                        {pipeResult && (
                            <div className={`amt-card amt-result-card ${pipeResult.status === 'completed' ? 'amt-result-success' : 'amt-result-error'}`}>
                                <div className="amt-result-header">
                                    <span className="amt-result-agent">🔗 串行 Pipeline</span>
                                    <span className={`amt-status-badge ${pipeResult.status === 'completed' ? 'amt-badge-green' : 'amt-badge-red'}`}>{pipeResult.status}</span>
                                    <span className="amt-result-duration">{pipeResult.total_duration_ms}ms</span>
                                </div>
                                <div className="amt-result-meta">Incident: {String(pipeResult.incident?.incident_id || '?')} | 步骤: {pipeResult.step_count}</div>
                                <div className="amt-pipeline-results">
                                    {pipeResult.steps.map(sr => (
                                        <div key={sr.step_index} className="amt-pipeline-result-step">
                                            <div className="amt-pipeline-result-header">
                                                <span className="amt-pipeline-result-badge">Step {sr.step_index}</span>
                                                <span className="amt-exec-agent">{agentIcons[sr.agent_type] || '🤖'} {sr.label}</span>
                                                <span className={`amt-status-badge ${sr.status === 'success' ? 'amt-badge-green' : 'amt-badge-red'}`}>{sr.status}</span>
                                                <span className="amt-exec-duration">{sr.duration_ms}ms</span>
                                            </div>
                                            <div className="amt-exec-task">任务: {sr.task.slice(0, 200)}{sr.task.length > 200 ? '...' : ''}</div>
                                            <div className="amt-result-text amt-pipeline-result-text">{sr.result}</div>
                                        </div>
                                    ))}
                                </div>
                                <div className="amt-wf-summary-section">
                                    <h3 className="amt-wf-summary-title">📋 组长故障处理报告</h3>
                                    <div className="amt-result-text amt-wf-summary-text">{pipeResult.summary}</div>
                                </div>
                            </div>
                        )}
                    </div>
                )

            case 'oa-team':
                return (
                    <div>
                        <div className="amt-section-header">
                            <h1 className="amt-page-title">🏢 OA Team</h1>
                            <p className="amt-page-subtitle">1 个 TeamLeader (oa-team-leader) + 3 个业务 Worker，Matrix Team 房间内一次分配并发执行</p>
                        </div>

                        {/* Team 信息卡 */}
                        {oaTeamInfo ? (
                            <div className="amt-card amt-oa-team-info">
                                <div className="amt-oa-team-header">
                                    <div className="amt-oa-team-title">
                                        <span className="amt-oa-team-icon">🏢</span>
                                        <span className="amt-oa-team-name">{oaTeamInfo.team_name}</span>
                                        <span className="amt-oa-team-id">({oaTeamInfo.team_id})</span>
                                    </div>
                                    <span className={`amt-status-badge ${oaTeamInfo.room?.status === 'ready' ? 'amt-badge-blue' : oaTeamInfo.room?.status === 'executing' ? 'amt-badge-orange' : 'amt-badge-green'}`}>{oaTeamInfo.room?.status || 'unknown'}</span>
                                </div>
                                <p className="amt-oa-team-desc">{oaTeamInfo.description}</p>

                                {/* Leader 信息 */}
                                <div className="amt-oa-leader-section">
                                    <h4 className="amt-oa-section-title">👑 TeamLeader (独立 Worker)</h4>
                                    <div className="amt-oa-leader-card">
                                        <div className="amt-oa-member-header">
                                            <span className="amt-oa-member-icon">👑</span>
                                            <span className="amt-oa-member-name">{oaTeamInfo.leader}</span>
                                            <span className="amt-oa-member-role">TeamLeader</span>
                                        </div>
                                        <div className="amt-oa-member-tools">
                                            <span className="amt-meta-label">Tools:</span>
                                            {(oaTeamInfo.leader_tools || []).map((t: string) => (
                                                <span key={t} className="amt-tag">{t}</span>
                                            ))}
                                        </div>
                                        <div className="amt-oa-member-meta">
                                            <span className="amt-tag">Max Turns: {oaTeamInfo.leader_max_turns}</span>
                                            <span className="amt-tag">Agent Type: {oaTeamInfo.leader_agent_type}</span>
                                        </div>
                                    </div>
                                </div>

                                {/* Workers 信息 — 3 个固定 Worker，不需要选择 */}
                                <div className="amt-oa-workers-section">
                                    <h4 className="amt-oa-section-title">👷 固定业务 Workers (3) — 不需要选择</h4>
                                    <div className="amt-oa-workers-grid">
                                        {/* Worker 1: task_analyzer (固定) */}
                                        {(() => {
                                            const wName = 'task_analyzer'
                                            const spec = (oaTeamInfo.worker_specs as Record<string, { role: string; mission: string; tools: string[]; max_turns: number; skills: string[]; skill?: string; output_contract: Record<string, unknown> }>)?.[wName] || {}
                                            const skillsList = spec.skills || (spec.skill ? [spec.skill] : [])
                                            return (
                                                <div key={wName} className="amt-oa-worker-card amt-oa-worker-fixed">
                                                    <div className="amt-oa-worker-badge">W1 固定</div>
                                                    <div className="amt-oa-member-header">
                                                        <span className="amt-oa-member-icon">📐</span>
                                                        <span className="amt-oa-member-name">task_analyzer</span>
                                                        <span className="amt-oa-member-role">{spec.role || 'Alert Intake Agent'}</span>
                                                    </div>
                                                    <div className="amt-oa-member-mission">Mission: {spec.mission || '归并客诉、告警和指标，输出事故候选和影响面'}</div>
                                                    <div className="amt-oa-member-skills-list">
                                                        <span className="amt-meta-label">Skills:</span>
                                                        {skillsList.map((s: string, i: number) => (
                                                            <span key={i} className="amt-oa-skill-tag">{s}</span>
                                                        ))}
                                                    </div>
                                                    <div className="amt-oa-member-tools">
                                                        <span className="amt-meta-label">Tools:</span>
                                                        {(spec.tools || ['system_monitor', 'service_check', 'log_search']).map(t => <span key={t} className="amt-tag">{t}</span>)}
                                                    </div>
                                                    <div className="amt-oa-member-meta">
                                                        <span className="amt-tag">Max Turns: {spec.max_turns || 8}</span>
                                                    </div>
                                                </div>
                                            )
                                        })()}
                                        {/* Worker 2: change_executor (固定) */}
                                        {(() => {
                                            const wName = 'change_executor'
                                            const spec = (oaTeamInfo.worker_specs as Record<string, { role: string; mission: string; tools: string[]; max_turns: number; skills: string[]; skill?: string; output_contract: Record<string, unknown> }>)?.[wName] || {}
                                            const skillsList = spec.skills || (spec.skill ? [spec.skill] : [])
                                            return (
                                                <div key={wName} className="amt-oa-worker-card amt-oa-worker-fixed">
                                                    <div className="amt-oa-worker-badge">W2 固定</div>
                                                    <div className="amt-oa-member-header">
                                                        <span className="amt-oa-member-icon">⚡</span>
                                                        <span className="amt-oa-member-name">change_executor</span>
                                                        <span className="amt-oa-member-role">{spec.role || 'Remediation Planner Agent'}</span>
                                                    </div>
                                                    <div className="amt-oa-member-mission">Mission: {spec.mission || '将 RCA 结论转换成修复计划、验证计划、回滚点'}</div>
                                                    <div className="amt-oa-member-skills-list">
                                                        <span className="amt-meta-label">Skills:</span>
                                                        {skillsList.map((s: string, i: number) => (
                                                            <span key={i} className="amt-oa-skill-tag">{s}</span>
                                                        ))}
                                                    </div>
                                                    <div className="amt-oa-member-tools">
                                                        <span className="amt-meta-label">Tools:</span>
                                                        {(spec.tools || ['service_check', 'system_monitor', 'log_search']).map(t => <span key={t} className="amt-tag">{t}</span>)}
                                                    </div>
                                                    <div className="amt-oa-member-meta">
                                                        <span className="amt-tag">Max Turns: {spec.max_turns || 8}</span>
                                                    </div>
                                                </div>
                                            )
                                        })()}
                                        {/* Worker 3: result_verifier (固定) */}
                                        {(() => {
                                            const wName = 'result_verifier'
                                            const spec = (oaTeamInfo.worker_specs as Record<string, { role: string; mission: string; tools: string[]; max_turns: number; skills: string[]; skill?: string; output_contract: Record<string, unknown> }>)?.[wName] || {}
                                            const skillsList = spec.skills || (spec.skill ? [spec.skill] : [])
                                            return (
                                                <div key={wName} className="amt-oa-worker-card amt-oa-worker-fixed">
                                                    <div className="amt-oa-worker-badge">W3 固定</div>
                                                    <div className="amt-oa-member-header">
                                                        <span className="amt-oa-member-icon">✅</span>
                                                        <span className="amt-oa-member-name">result_verifier</span>
                                                        <span className="amt-oa-member-role">{spec.role || 'Recovery Verifier Agent'}</span>
                                                    </div>
                                                    <div className="amt-oa-member-mission">Mission: {spec.mission || '两阶段核验（方案审核 → 执行核验）'}</div>
                                                    <div className="amt-oa-member-skills-list">
                                                        <span className="amt-meta-label">Skills:</span>
                                                        {skillsList.map((s: string, i: number) => (
                                                            <span key={i} className="amt-oa-skill-tag">{s}</span>
                                                        ))}
                                                    </div>
                                                    <div className="amt-oa-member-tools">
                                                        <span className="amt-meta-label">Tools:</span>
                                                        {(spec.tools || ['service_check', 'system_monitor', 'log_search']).map(t => <span key={t} className="amt-tag">{t}</span>)}
                                                    </div>
                                                    <div className="amt-oa-member-meta">
                                                        <span className="amt-tag">Max Turns: {spec.max_turns || 8}</span>
                                                    </div>
                                                </div>
                                            )
                                        })()}
                                    </div>
                                </div>

                                {/* 运行规则 */}
                                <div className="amt-oa-rules-section">
                                    <h4 className="amt-oa-section-title">📋 运行规则</h4>
                                    <ul className="amt-oa-rules-list">
                                        {oaTeamInfo.rules?.map((r: string, i: number) => (
                                            <li key={i} className="amt-oa-rule-item">{r}</li>
                                        ))}
                                    </ul>
                                </div>

                                {/* 房间信息 */}
                                {oaTeamInfo.room && (
                                    <div className="amt-oa-room-info">
                                        <span className="amt-meta-label">Matrix Team 房间:</span>
                                        <span className="amt-tag">{oaTeamInfo.room.room_id}</span>
                                        <span className="amt-tag">消息数: {oaTeamInfo.room.message_count}</span>
                                        <span className="amt-tag">创建: {oaTeamInfo.room.created_at}</span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="amt-empty">加载 OA Team 信息...</div>
                        )}

                        {/* 消息输入 */}
                        <div className="amt-card amt-exec-card">
                            <div className="amt-oa-flow">
                                <span className="amt-wf-node amt-wf-leader">@oa-team-leader</span>
                                <span className="amt-wf-arrow">→</span>
                                <span className="amt-wf-node">一次分配 3 Worker 并发</span>
                                <span className="amt-wf-arrow">→</span>
                                <span className="amt-wf-node amt-wf-leader">组长汇总报告</span>
                            </div>

                            <div className="amt-form-col-span-2">
                                <label className="amt-label">事故消息 (以 @oa-team-leader 开头触发组长)</label>
                                <textarea
                                    value={oaMessage}
                                    onChange={e => setOaMessage(e.target.value)}
                                    placeholder={"@oa-team-leader 存储驱动从 overlay2 改为 vfs 后 MySQL 容器启动失败，InnoDB 数据页损坏，P0 级故障，请排查"}
                                    className="amt-input amt-textarea"
                                    rows={3}
                                />
                            </div>

                            <button
                                onClick={handleOaTeamExecute}
                                disabled={oaExecuting}
                                className="amt-btn amt-btn-indigo"
                                style={{ marginTop: '16px' }}
                            >
                                {oaExecuting ? '⏳ OA Team 执行中... (组长分配 3 Worker 并发)' : '🚀 发送消息给 oa-team'}
                            </button>
                        </div>

                        {/* OA Team 执行结果 */}
                        {oaResult && (
                            <div className={`amt-card amt-result-card ${oaResult.status === 'completed' ? 'amt-result-success' : 'amt-result-error'}`}>
                                <div className="amt-result-header">
                                    <span className="amt-result-agent">🏢 OA Team 工作流</span>
                                    <span className={`amt-status-badge ${oaResult.status === 'completed' ? 'amt-badge-green' : 'amt-badge-red'}`}>{oaResult.status}</span>
                                    <span className="amt-result-duration">{oaResult.total_duration_ms}ms</span>
                                </div>
                                <div className="amt-result-meta">
                                    Team: {oaResult.team_id} | Leader: {oaResult.team_leader} | Worker 数: {oaResult.worker_count} | 房间: {oaResult.room_id}
                                </div>

                                {/* 各 Worker 结果 */}
                                <div className="amt-wf-results">
                                    {oaResult.workers.map(wr => (
                                        <div key={wr.worker_index} className="amt-wf-worker-result amt-matrix-result-card">
                                            <div className="amt-exec-item-header">
                                                <span className="amt-matrix-result-badge">Worker {wr.worker_index}</span>
                                                <span className="amt-exec-agent">{agentIcons[wr.agent_type] || '🤖'} {wr.agent_type}</span>
                                                <span className="amt-oa-worker-role-tag">{wr.role}</span>
                                                <span className={`amt-status-badge ${wr.status === 'success' ? 'amt-badge-green' : 'amt-badge-red'}`}>{wr.status}</span>
                                                <span className="amt-exec-duration">{wr.duration_ms}ms</span>
                                            </div>
                                            <div className="amt-exec-task">任务: {wr.task.slice(0, 200)}{wr.task.length > 200 ? '...' : ''}</div>
                                            <div className="amt-result-text amt-wf-worker-text">{wr.result}</div>
                                        </div>
                                    ))}
                                </div>

                                {/* 组长汇总 */}
                                <div className="amt-wf-summary-section">
                                    <h3 className="amt-wf-summary-title">📋 oa-team-leader 汇总报告</h3>
                                    <div className="amt-result-text amt-wf-summary-text">{oaResult.leader_summary}</div>
                                </div>
                            </div>
                        )}
                    </div>
                )

            case 'team':
                return (
                    <div className="amt-team-layout">
                        {/* 左侧: 任务列表 */}
                        <div className="amt-task-sidebar">
                            <div className="amt-task-sidebar-header">
                                <h2 className="amt-task-sidebar-title">📋 任务列表</h2>
                                <button
                                    onClick={handleCreateTask}
                                    disabled={taskCreating}
                                    className="amt-btn amt-btn-sm amt-btn-indigo"
                                    title="创建新任务"
                                >
                                    {taskCreating ? '⏳' : '➕ 新建'}
                                </button>
                            </div>
                            <div className="amt-task-list">
                                {chatTasks.length === 0 ? (
                                    <div className="amt-task-empty">暂无任务，点击「新建」创建</div>
                                ) : (
                                    chatTasks.map(task => (
                                        <div
                                            key={task.id}
                                            className={`amt-task-item ${task.id === currentTaskId ? 'amt-task-item-active' : ''}`}
                                            onClick={() => handleSelectTask(task.id)}
                                        >
                                            <div className="amt-task-item-header">
                                                <span className="amt-task-item-title">{task.title}</span>
                                                {task.id === currentTaskId && <span className="amt-task-item-badge">当前</span>}
                                                {/* 任务状态标记 */}
                                                {task.worker_status && Object.keys(task.worker_status).length > 0 && (() => {
                                                    const statuses = Object.values(task.worker_status)
                                                    const hasError = statuses.some(s => s.includes('error') || s.includes('fail'))
                                                    const allDone = statuses.every(s => s.includes('done') || s.includes('complete') || s.includes('success'))
                                                    const isRunning = !hasError && !allDone && statuses.some(s => s.includes('running') || s.includes('thinking') || s.includes('pending'))
                                                    if (hasError) return <span className="amt-task-status amt-task-status-error" title="有执行出错">❌ 出错</span>
                                                    if (allDone) return <span className="amt-task-status amt-task-status-done" title="全部完成">✅ 已完成</span>
                                                    if (isRunning) return <span className="amt-task-status amt-task-status-running" title="执行中">⏳ 处理中</span>
                                                    return null
                                                })()}
                                            </div>
                                            <div className="amt-task-item-meta">
                                                <span className="amt-task-item-count">💬 {task.message_count}</span>
                                                <span className="amt-task-item-time">{task.created_at?.slice(5, 16) || ''}</span>
                                            </div>
                                            <div className="amt-task-item-actions">
                                                <button
                                                    className="amt-task-item-btn"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        setTaskRenamingId(task.id)
                                                        setTaskRenameValue(task.title)
                                                    }}
                                                    title="重命名"
                                                >
                                                    ✏️
                                                </button>
                                                <button
                                                    className="amt-task-item-btn amt-task-item-btn-del"
                                                    onClick={(e) => handleDeleteTask(task.id, e)}
                                                    title="删除"
                                                >
                                                    🗑️
                                                </button>
                                            </div>
                                            {/* 重命名输入框 */}
                                            {taskRenamingId === task.id && (
                                                <div className="amt-task-rename-wrap" onClick={e => e.stopPropagation()}>
                                                    <input
                                                        className="amt-task-rename-input"
                                                        value={taskRenameValue}
                                                        onChange={e => setTaskRenameValue(e.target.value)}
                                                        onKeyDown={e => {
                                                            if (e.key === 'Enter') handleRenameTask(task.id)
                                                            if (e.key === 'Escape') { setTaskRenamingId('') }
                                                        }}
                                                        autoFocus
                                                    />
                                                    <button className="amt-btn amt-btn-sm amt-btn-indigo" onClick={() => handleRenameTask(task.id)}>确定</button>
                                                    <button className="amt-btn amt-btn-sm" onClick={() => setTaskRenamingId('')}>取消</button>
                                                </div>
                                            )}
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>

                        {/* 右侧: 聊天面板 */}
                        <div className="amt-chat-panel">
                            <div className="amt-section-header">
                                <h1 className="amt-page-title">💬 团队聊天室</h1>
                                <p className="amt-page-subtitle">输入 @oa_team_leader 提交任务，组长会串行分派给 3 个 Worker 协作执行</p>
                            </div>

                            {/* Worker 状态条 */}
                            {chatRoomInfo && (
                                <div className="amt-chat-status-bar">
                                    <div className="amt-chat-room-meta">
                                        <span className="amt-tag">消息: {chatRoomInfo.message_count}</span>
                                        {chatTasks.find(t => t.id === currentTaskId) && (
                                            <span className="amt-tag amt-tag-current">当前: {chatTasks.find(t => t.id === currentTaskId)?.title}</span>
                                        )}
                                    </div>
                                    <div className="amt-chat-worker-status">
                                        {Object.entries(chatRoomInfo.worker_status || {}).map(([worker, status]) => (
                                            <span key={worker} className={`amt-chat-worker-chip amt-chat-status-${status}`}>
                                                {agentIcons[worker] || '🤖'} {worker}
                                                <span className="amt-chat-status-dot">{status}</span>
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* 聊天消息区 (带滚动到底部按钮) */}
                            <div className="amt-chat-msg-wrap">
                                <div
                                    className="amt-chat-messages"
                                    ref={chatScrollRef}
                                    onScroll={handleChatScroll}
                                >
                                    {chatMessages.length === 0 ? (
                                        <div className="amt-chat-empty">
                                            💬 暂无消息。在下方输入框发送消息，或点击 @ 按钮快速选择 worker。
                                            <br />
                                            例如：<code>@oa_team_leader MySQL 容器启动失败，请排查</code>
                                        </div>
                                    ) : (
                                        chatMessages.map((msg, msgIdx) => {
                                            const isUser = msg.role === 'user'
                                            const avatar = isUser ? '🧑' :
                                                msg.role === 'leader' ? '👑' :
                                                msg.role === 'worker' ? (agentIcons[msg.sender] || '🤖') :
                                                'ℹ️'
                                            // 只在最后一条 thinking 消息上闪烁，已读的不闪
                                            const isLastThinking = msg.status === 'thinking' &&
                                                msgIdx === chatMessages.length - 1
                                            return (
                                                <div key={msg.id} className={`amt-chat-row ${isUser ? 'amt-chat-row-user' : 'amt-chat-row-ai'}`}>
                                                    {/* AI 消息: 头像在左 */}
                                                    {!isUser && (
                                                        <div className="amt-chat-avatar amt-chat-avatar-ai">{avatar}</div>
                                                    )}
                                                    <div className={`amt-chat-card ${isUser ? 'amt-chat-card-user' : `amt-chat-card-${msg.role}`} ${isLastThinking ? 'amt-chat-card-thinking' : ''}`}>
                                                        {/* 卡片头 */}
                                                        <div className="amt-chat-card-header">
                                                            {isUser ? (
                                                                <span className="amt-chat-card-time">{msg.timestamp.slice(11)}</span>
                                                            ) : (
                                                                <>
                                                                    <span className="amt-chat-card-name">{msg.sender}</span>
                                                                    {msg.mention && <span className="amt-chat-card-mention">@{msg.mention}</span>}
                                                                    {msg.duration_ms > 0 && <span className="amt-chat-card-duration">{msg.duration_ms}ms</span>}
                                                                    <span className={`amt-chat-card-status amt-chat-status-${msg.status}`}>
                                                                        {msg.status === 'thinking' ? '⏳ 处理中' : msg.status === 'done' ? '✅ 完成' : msg.status === 'error' ? '❌ 出错' : ''}
                                                                    </span>
                                                                    <span className="amt-chat-card-time">{msg.timestamp.slice(11)}</span>
                                                                </>
                                                            )}
                                                        </div>
                                                        {/* 卡片内容 */}
                                                        <div 
                                                            className="amt-chat-card-body"
                                                            dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                                                        />
                                                    </div>
                                                    {/* 用户消息: 头像在右 */}
                                                    {isUser && (
                                                        <div className="amt-chat-avatar amt-chat-avatar-user">{avatar}</div>
                                                    )}
                                                </div>
                                            )
                                        })
                                    )}
                                    <div ref={chatEndRef} />
                                </div>
                                {/* 悬浮滚动到底部按钮 */}
                                {showScrollBtn && (
                                    <button
                                        className="amt-scroll-bottom-btn"
                                        onClick={() => scrollToBottom(true)}
                                        title="滚动到底部"
                                    >
                                        ⬇️
                                    </button>
                                )}
                            </div>

                            {/* @ 按钮区 */}
                            <div className="amt-chat-mentions">
                                <span className="amt-meta-label">快捷 @:</span>
                                <button onClick={() => insertMention('oa_team_leader')} className="amt-chat-mention-btn amt-chat-mention-leader">👑 @oa_team_leader</button>
                                <button onClick={() => insertMention('task_analyzer')} className="amt-chat-mention-btn amt-chat-mention-worker" title="提交后由组长调度">📐 @task_analyzer</button>
                                <button onClick={() => insertMention('change_executor')} className="amt-chat-mention-btn amt-chat-mention-worker" title="提交后由组长调度">⚡ @change_executor</button>
                                <button onClick={() => insertMention('result_verifier')} className="amt-chat-mention-btn amt-chat-mention-worker" title="提交后由组长调度">✅ @result_verifier</button>
                                <button onClick={handleChatClear} className="amt-chat-clear-btn">🗑️ 清空当前任务</button>
                                {chatSending && (
                                    <button onClick={handleChatStop} className="amt-chat-stop-btn">⏹️ 停止任务</button>
                                )}
                            </div>

                            {/* 输入区 (带 @ 自动补全) */}
                            <div className="amt-chat-input-wrapper">
                                {mentionMenu.show && mentionMenu.items.length > 0 && (
                                    <div className="amt-mention-dropdown">
                                        {mentionMenu.items.map((item, idx) => (
                                            <div
                                                key={item.id}
                                                className={`amt-mention-item ${idx === mentionMenu.selectedIndex ? 'amt-mention-item-active' : ''}`}
                                                onMouseEnter={() => setMentionMenu(prev => ({ ...prev, selectedIndex: idx }))}
                                                onMouseDown={e => {
                                                    e.preventDefault()  // 阻止 textarea 失焦
                                                    selectMention(item.id)
                                                }}
                                            >
                                                <span className="amt-mention-icon">{item.icon}</span>
                                                <span className="amt-mention-label">@{item.label}</span>
                                                {item.id === 'oa_team_leader' && (
                                                    <span className="amt-mention-tag">组长</span>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                )}
                                <div className="amt-chat-input-area">
                                    <textarea
                                        ref={chatInputRef}
                                        value={chatInput}
                                        onChange={handleChatInputChange}
                                        onKeyDown={handleChatInputKeyDown}
                                        onBlur={() => {
                                            // 延迟关闭，允许点击下拉项
                                            setTimeout(() => setMentionMenu(prev => ({ ...prev, show: false })), 150)
                                        }}
                                        placeholder="输入消息... (输入 @ 选择成员，Enter 发送，Shift+Enter 换行)"
                                        className="amt-input amt-chat-input"
                                        rows={2}
                                    />
                                    <button
                                        onClick={handleChatSend}
                                        disabled={chatSending || !chatInput.trim()}
                                        className="amt-btn amt-btn-indigo amt-chat-send-btn"
                                    >
                                        {chatSending ? '⏳ 发送中...' : '📤 发送'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )

            default:
                return null
        }
    }

    const activityIcons: Record<string, string> = {
        worker: '🤖',
        team: '👥',
        project: '📁',
        orchestration: '🧭',
        system: 'ℹ️',
    }

    return (
        <div className="amt-container">
            {/* 架构说明 */}
            <div className="amt-arch-banner">
                <span className="amt-arch-label">真实多 Sub-Agent 架构</span>
                <span className="amt-arch-flow">
                    父 Agent (主聊天) → <span className="amt-arch-tool">delegate_sub_agent</span> 工具 →
                    子 Agent (独立 agent_loop) → 工具子集执行 → 返回结果
                </span>
            </div>

            {/* Tab 导航 */}
            <div className="amt-tab-nav">
                {tabs.map(tab => (
                    <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                        className={`amt-tab-btn ${activeTab === tab.id ? 'amt-tab-active' : ''}`}>
                        <span className="amt-tab-icon">{tab.icon}</span>
                        <span>{tab.label}</span>
                    </button>
                ))}
            </div>

            {/* 消息提示 */}
            {message && (
                <div className={`amt-message ${message.type === 'success' ? 'amt-msg-success' : 'amt-msg-error'}`}>
                    {message.text}
                </div>
            )}

            {/* 内容区 */}
            {renderContent()}
        </div>
    )
}
