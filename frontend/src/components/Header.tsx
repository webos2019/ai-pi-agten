import React, { useState, useRef, useEffect } from 'react'
import type { Workspace } from '../types'

interface Props {
    mode: string
    onSetMode: (mode: string) => void
    hasMessages: boolean
    onRegenerate: () => void
    tokenUsage: { prompt: number; completion: number; total: number }
    todayTokens?: number
    dailyTokenLimit?: number
    view?: 'chat' | 'stock' | 'chanlun' | 'image' | 'opspilot' | 'voice' | 'kb'
    onSetView?: (view: 'chat' | 'stock' | 'chanlun' | 'image' | 'opspilot' | 'voice' | 'kb') => void
    onOpenSettings?: () => void
    llmModel?: string
    llmHasKey?: boolean
    // 工作区
    workspaces?: Workspace[]
    activeWorkspace?: Workspace | null
    onSwitchWorkspace?: (id: string) => void
    onCreateWorkspace?: (name: string) => void
    onDeleteWorkspace?: (id: string) => void
    onRenameWorkspace?: (id: string, name: string) => void
}

// 格式化 token 数为简洁形式: 1234 -> "1.2k", 123456 -> "123.5k"
function formatTokenK(n: number): string {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
    return String(n)
}

const Header: React.FC<Props> = ({
    mode, onSetMode, hasMessages, onRegenerate, tokenUsage,
    todayTokens = 0, dailyTokenLimit = 500000,
    view = 'chat', onSetView, onOpenSettings, llmModel, llmHasKey,
    workspaces, activeWorkspace, onSwitchWorkspace, onCreateWorkspace, onDeleteWorkspace, onRenameWorkspace,
}) => {
    const [wsDropdownOpen, setWsDropdownOpen] = useState(false)
    const [editingWsId, setEditingWsId] = useState<string | null>(null)
    const [editingName, setEditingName] = useState('')
    const [creatingWs, setCreatingWs] = useState(false)
    const [newWsName, setNewWsName] = useState('')
    const wsDropdownRef = useRef<HTMLDivElement>(null)

    // 点击外部关闭下拉
    useEffect(() => {
        if (!wsDropdownOpen) return
        const handler = (e: MouseEvent) => {
            if (wsDropdownRef.current && !wsDropdownRef.current.contains(e.target as Node)) {
                setWsDropdownOpen(false)
                setCreatingWs(false)
                setEditingWsId(null)
            }
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [wsDropdownOpen])

    const subtitle = view === 'stock'
        ? '股票分析 · 7因子技术面 + ESG有效性'
        : view === 'chanlun'
        ? '缠论分析 · 分型/笔/线段/中枢/背驰/买卖点'
        : view === 'image'
        ? 'AI 生图 · 商汤 SenseMirage 文字生图'
        : view === 'opspilot'
        ? 'OpsPilot · 多Agent AIOps 协作流水线'
        : view === 'voice'
        ? '语音对话 · 微软 TTS 语音合成 + 实时语音识别'
        : mode === 'utility-skill' ? '实用工具模式 · 工具调用 + 流式输出' : '文件与天气模式 · 本地读取 + 实时查询'

    const handleCreateWs = () => {
        const name = newWsName.trim() || '新公司'
        onCreateWorkspace?.(name)
        setNewWsName('')
        setCreatingWs(false)
        setWsDropdownOpen(false)
    }

    const handleRenameWs = (id: string) => {
        const name = editingName.trim()
        if (name) {
            onRenameWorkspace?.(id, name)
        }
        setEditingWsId(null)
    }

    return (
        <header className="header flex shrink-0 items-center justify-between px-3 py-2 relative z-10 sm:px-4 sm:py-3">
            <div className="flex items-center gap-2 sm:gap-3">
                <div className="header-icon" title="Pi Agent">π</div>
                <div>
                    <h1 className="header-title">Pi Agent</h1>
                    <p className="header-subtitle hidden sm:block">{subtitle}</p>
                </div>

                {/* 工作区切换器 */}
                {activeWorkspace && (
                    <div className="ws-switcher-wrapper" ref={wsDropdownRef}>
                        <button
                            className="ws-switcher-btn"
                            onClick={() => setWsDropdownOpen(!wsDropdownOpen)}
                            title="切换公司/工作区"
                        >
                            <svg className="ws-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M3 21h18M3 7l1-4h16l1 4M3 7v14M21 7v14M9 21v-7h6v7"/>
                            </svg>
                            <span className="ws-name">{activeWorkspace.name}</span>
                            <svg className="ws-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M6 9l6 6 6-6"/>
                            </svg>
                        </button>

                        {wsDropdownOpen && (
                            <div className="ws-dropdown">
                                <div className="ws-dropdown-header">公司列表</div>
                                {workspaces?.map(ws => (
                                    <div
                                        key={ws.id}
                                        className={`ws-dropdown-item ${ws.id === activeWorkspace?.id ? 'active' : ''}`}
                                        onClick={() => {
                                            if (ws.id !== activeWorkspace?.id) {
                                                onSwitchWorkspace?.(ws.id)
                                                setWsDropdownOpen(false)
                                            }
                                        }}
                                    >
                                        {editingWsId === ws.id ? (
                                            <input
                                                className="ws-rename-input"
                                                type="text"
                                                value={editingName}
                                                onChange={e => setEditingName(e.target.value)}
                                                onClick={e => e.stopPropagation()}
                                                onKeyDown={e => {
                                                    if (e.key === 'Enter') handleRenameWs(ws.id)
                                                    if (e.key === 'Escape') setEditingWsId(null)
                                                }}
                                                onBlur={() => handleRenameWs(ws.id)}
                                                autoFocus
                                            />
                                        ) : (
                                            <>
                                                <span className="ws-item-name">{ws.name}</span>
                                                <div className="ws-item-actions">
                                                    <button
                                                        className="ws-item-btn"
                                                        title="重命名"
                                                        onClick={e => {
                                                            e.stopPropagation()
                                                            setEditingWsId(ws.id)
                                                            setEditingName(ws.name)
                                                        }}
                                                    >
                                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                                        </svg>
                                                    </button>
                                                    {(workspaces?.length || 0) > 1 && (
                                                        <button
                                                            className="ws-item-btn ws-item-delete"
                                                            title="删除"
                                                            onClick={e => {
                                                                e.stopPropagation()
                                                                onDeleteWorkspace?.(ws.id)
                                                                setWsDropdownOpen(false)
                                                            }}
                                                        >
                                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                                <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                                            </svg>
                                                        </button>
                                                    )}
                                                </div>
                                            </>
                                        )}
                                    </div>
                                ))}

                                {/* 新建公司 */}
                                <div className="ws-dropdown-divider" />
                                {creatingWs ? (
                                    <div className="ws-create-form" onClick={e => e.stopPropagation()}>
                                        <input
                                            className="ws-create-input"
                                            type="text"
                                            placeholder="公司名称..."
                                            value={newWsName}
                                            onChange={e => setNewWsName(e.target.value)}
                                            onKeyDown={e => {
                                                if (e.key === 'Enter') handleCreateWs()
                                                if (e.key === 'Escape') { setCreatingWs(false); setNewWsName('') }
                                            }}
                                            autoFocus
                                        />
                                        <button className="ws-create-confirm" onClick={handleCreateWs}>确认</button>
                                    </div>
                                ) : (
                                    <button
                                        className="ws-dropdown-item ws-create-btn"
                                        onClick={() => setCreatingWs(true)}
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <path d="M12 5v14M5 12h14"/>
                                        </svg>
                                        <span>新建公司</span>
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
            <div className="flex items-center gap-1.5 sm:gap-2 relative z-10">
                {/* LLM 设置按钮 */}
                {onOpenSettings && (
                    <button
                        className="action-btn flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs"
                        onClick={onOpenSettings}
                        title={llmHasKey ? `LLM: ${llmModel || '未知'}` : 'LLM 未配置，点击设置'}
                    >
                        <svg className="icon h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                        <span className={`llm-status-dot ${llmHasKey ? 'ok' : 'warn'}`} />
                    </button>
                )}
                {tokenUsage.total > 0 && (view === 'chat' || view === 'voice') && (
                    <div className="token-badge" title={`输入: ${tokenUsage.prompt} | 输出: ${tokenUsage.completion} | 总计: ${tokenUsage.total}`}>
                        <svg className="token-badge-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                            <path d="M2 17l10 5 10-5"/>
                            <path d="M2 12l10 5 10-5"/>
                        </svg>
                        <span className="token-badge-value">{tokenUsage.total.toLocaleString()}</span>
                    </div>
                )}
                {/* 今日 Token 花费进度条 */}
                {todayTokens > 0 && (
                    <div
                        className="token-bar-badge"
                        title={`今日 Token 消耗\n输入: ${tokenUsage.prompt.toLocaleString()}\n输出: ${tokenUsage.completion.toLocaleString()}\n累计: ${todayTokens.toLocaleString()} / ${dailyTokenLimit.toLocaleString()}`}
                    >
                        <span className="token-bar-label">今日</span>
                        <span className="token-bar-numbers">
                            {formatTokenK(todayTokens)}/{formatTokenK(dailyTokenLimit)}
                        </span>
                        <span className="token-bar-sep">·</span>
                        <span className="token-bar-blocks">
                            {(() => {
                                const pct = Math.min(todayTokens / dailyTokenLimit, 1)
                                const filled = Math.round(pct * 7)
                                return Array.from({ length: 7 }, (_, i) => (
                                    <span key={i} className={`token-block ${i < filled ? 'filled' : 'empty'}`}>▊</span>
                                ))
                            })()}
                        </span>
                        <span className="token-bar-pct">({Math.min(Math.round((todayTokens / dailyTokenLimit) * 100), 100)}%)</span>
                    </div>
                )}
                {/* 视图切换 */}
                {onSetView && (
                    <div className="mode-selector flex overflow-hidden rounded-md">
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'chat' ? 'active' : ''}`}
                            onClick={() => onSetView('chat')}
                        >对话</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'stock' ? 'active' : ''}`}
                            onClick={() => onSetView('stock')}
                        >股票</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'chanlun' ? 'active' : ''}`}
                            onClick={() => onSetView('chanlun')}
                        >缠论</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'image' ? 'active' : ''}`}
                            onClick={() => onSetView('image')}
                        >生图</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'opspilot' ? 'active' : ''}`}
                            onClick={() => onSetView('opspilot')}
                        >运维</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'kb' ? 'active' : ''}`}
                            onClick={() => onSetView('kb')}
                        >知识库</button>
                        <button
                            className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${view === 'voice' ? 'active' : ''}`}
                            onClick={() => onSetView('voice')}
                        >语音</button>
                    </div>
                )}
                {view === 'chat' && (
                    <>
                        <div className="mode-selector flex overflow-hidden rounded-md">
                            <button
                                className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${mode === 'utility-skill' ? 'active' : ''}`}
                                onClick={() => onSetMode('utility-skill')}
                            >工具</button>
                            <button
                                className={`mode-btn px-3 py-1.5 text-xs ${mode === 'reader-skill' ? 'active' : ''}`}
                                onClick={() => onSetMode('reader-skill')}
                            >文件</button>
                        </div>
                        {hasMessages && (
                            <button className="action-btn flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs" onClick={onRegenerate} title="重新生成上一个回答">
                                <svg className="icon h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                                重新生成
                            </button>
                        )}
                    </>
                )}
            </div>
        </header>
    )
}

export default Header
