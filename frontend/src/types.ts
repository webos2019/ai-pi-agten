// ─── Types ─────────────────────────────────────────────

export type ChatStatus = 'idle' | 'loading' | 'streaming' | 'retrying' | 'error'

export interface UploadedFile {
    name: string
    size: number
    type: string
    content: string
}

export interface ChatBlock {
    type: string
    content?: string
    toolCallId?: string
    toolName?: string
    toolArgs?: Record<string, unknown>
    toolResult?: string
    isValid?: boolean
    resourceName?: string
    resourceUri?: string
    serverId?: string
    isTruncated?: boolean
    previewChars?: number
    // Agent step fields
    actionType?: string
    title?: string
    stepIndex?: number
    status?: string
    summary?: string
    durationMs?: number
    runId?: string
    // Steer fields (流式插话)
    steerId?: string
    steerText?: string
    appliedAtStep?: number
    queueSize?: number
    reason?: string
}

export interface ChatMessage {
    role: 'user' | 'assistant' | 'system'
    content: string
    files?: UploadedFile[]
    blocks?: ChatBlock[]
    structured?: StructuredRequest | null
}

export interface StructuredRequest {
    rawText: string
    segments: Array<{ type: string; content?: string; chipType?: string; label?: string; data?: unknown }>
    chips: Array<{ type: string; label: string; data?: unknown }>
}

export interface SlashCommand {
    label: string
    icon: string
    desc: string
    alias?: string[]
    action: (editor: AIInputEditorHandle) => void
}

export interface AtReference {
    label: string
    type: string
    desc: string
    keywords?: string[]
    data?: unknown
}

// Editor handle interface (for slash command callbacks)
export interface AIInputEditorHandle {
    clear(): void
    insertToolReference(toolName: string): void
    insertSkillReference(skillName: string): void
    insertDocReference(docUri: string): void
    setValue(text: string): void
}

// ─── Pi Agent 版本方案 URIs ────────────────────────────
// 与 c:/newtask-pi/docs/versions/ 实际文件保持一致
export const VERSION_PLAN_V01 = 'docs://versions/v0.1.0-controlled-tasklist-agent.md'
export const VERSION_PLAN_V02 = 'docs://versions/v0.2.0-agent-trace-panel.md'
export const VERSION_PLAN_V03 = 'docs://versions/v0.3.0-thread-state-short-term-memory.md'

// ─── Slash Commands & At References ─────────────────────

export const slashCommands: SlashCommand[] = [
    { label: '生成任务清单', icon: '📋', desc: 'Pi Agent: 基于版本方案生成 Tasklist 草稿', alias: ['tasklist', 'task', '任务', 'pi', 'agent'],
      action: (ed) => { ed.insertSkillReference('tasklist'); } },
    { label: '引用版本方案 v0.1.0', icon: '📑', desc: '受控 Tasklist Agent (v0.1.0)', alias: ['version', '版本', 'v0.1', 'tasklist'],
      action: (ed) => { ed.insertDocReference(VERSION_PLAN_V01); } },
    { label: '引用版本方案 v0.2.0', icon: '📑', desc: 'Agent Trace Panel (v0.2.0)', alias: ['version', '版本', 'v0.2', 'trace'],
      action: (ed) => { ed.insertDocReference(VERSION_PLAN_V02); } },
    { label: '引用版本方案 v0.3.0', icon: '📑', desc: 'ThreadState 短期记忆 (v0.3.0)', alias: ['version', '版本', 'v0.3', 'memory', 'thread'],
      action: (ed) => { ed.insertDocReference(VERSION_PLAN_V03); } },
    { label: '切换工具模式', icon: '🔧', desc: '切换到实用工具技能', alias: ['tool', '工具'],
      action: (ed) => { /* handled in App */ (window as any).__setMode?.('utility-skill'); ed.clear(); } },
    { label: '切换文件模式', icon: '📁', desc: '切换到文件与天气技能', alias: ['file', '文件', 'reader'],
      action: (ed) => { (window as any).__setMode?.('reader-skill'); ed.clear(); } },
    { label: '引用计算器', icon: '🔢', desc: '插入计算器工具引用', alias: ['calc', '计算'],
      action: (ed) => { ed.insertToolReference('calculator'); } },
    { label: '引用天气查询', icon: '🌤', desc: '插入天气工具引用', alias: ['weather', '天气'],
      action: (ed) => { ed.insertToolReference('get_weather'); } },
    { label: '引用文件读取', icon: '📄', desc: '插入文件读取引用', alias: ['read', '文件读取'],
      action: (ed) => { ed.insertToolReference('local-text-read'); } },
    { label: '清空对话', icon: '🗑', desc: '清空当前对话历史', alias: ['clear', '清空'],
      action: (ed) => { (window as any).__clearMessages?.(); ed.clear(); } },
]

export const atReferences: AtReference[] = [
    // ── 版本方案 (Pi Agent 入口) ──
    { label: VERSION_PLAN_V01, type: 'doc', desc: '受控 Tasklist Agent v0.1.0', keywords: ['version', '版本', 'v0.1', 'tasklist', 'agent', 'controlled'] },
    { label: VERSION_PLAN_V02, type: 'doc', desc: 'Agent Trace Panel v0.2.0', keywords: ['version', '版本', 'v0.2', 'trace', 'panel'] },
    { label: VERSION_PLAN_V03, type: 'doc', desc: 'ThreadState 短期记忆 v0.3.0', keywords: ['version', '版本', 'v0.3', 'memory', 'thread', 'state'] },
    // ── 工具 ──
    { label: 'calculator', type: 'tool', desc: '数学计算器', keywords: ['calc', '计算', 'math', '计算器'] },
    { label: 'datetime', type: 'tool', desc: '日期时间查询', keywords: ['time', '时间', 'date', '日期'] },
    { label: 'get_weather', type: 'tool', desc: '天气查询', keywords: ['weather', '天气', '温度'] },
    { label: 'get_location', type: 'tool', desc: '地理位置', keywords: ['location', '位置', 'ip', '地理'] },
    { label: 'unit_convert', type: 'tool', desc: '单位换算', keywords: ['unit', '换算', '转换'] },
    { label: 'text_transform', type: 'tool', desc: '文本转换', keywords: ['text', '文本', 'markdown', 'json'] },
    { label: 'web_browse', type: 'tool', desc: '网页浏览', keywords: ['web', '网页', 'url', 'browse'] },
    { label: 'local-text-read', type: 'tool', desc: '本地文件读取', keywords: ['file', '文件', 'read'] },
    { label: 'list_files', type: 'tool', desc: '目录列表', keywords: ['list', '目录', 'files'] },
    // ── 上下文 ──
    { label: '当前IP', type: 'context', desc: '引用客户端IP地址' },
    { label: '对话历史', type: 'context', desc: '引用最近的对话历史' },
    // ── 技能 ──
    { label: 'utility-skill', type: 'skill', desc: '工具技能模式' },
    { label: 'reader-skill', type: 'skill', desc: '文件与天气技能模式' },
    { label: 'tasklist', type: 'skill', desc: 'Pi Agent 任务清单生成' },
]
