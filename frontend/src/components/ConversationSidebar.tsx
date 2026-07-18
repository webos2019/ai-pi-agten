import React, { useState } from 'react'
import type { ConversationItem } from '../hooks/useConversations'

interface Props {
    conversations: ConversationItem[]
    selectedId: string
    isDraft: boolean
    disabled: boolean
    collapsed: boolean
    onToggleCollapse: () => void
    onNewChat: () => void
    onSelect: (conversationId: string) => void
    onDelete: (conversationId: string) => void
    onRename: (conversationId: string, title: string) => void
}

function formatTime(ts: number): string {
    const diff = Date.now() - ts * 1000
    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
    return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

const ConversationSidebar: React.FC<Props> = ({
    conversations, selectedId, isDraft, disabled, collapsed,
    onToggleCollapse, onNewChat, onSelect, onDelete, onRename,
}) => {
    const [editingId, setEditingId] = useState('')
    const [editText, setEditText] = useState('')

    const handleStartEdit = (e: React.MouseEvent, conv: ConversationItem) => {
        e.stopPropagation()
        setEditingId(conv.conversationId)
        setEditText(conv.title)
    }

    const handleFinishEdit = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            if (editingId && editText.trim()) onRename(editingId, editText.trim())
            setEditingId('')
        } else if (e.key === 'Escape') {
            setEditingId('')
        }
    }

    // 折叠态 — 只显示一个展开按钮
    if (collapsed) {
        return (
            <aside className="flex flex-col items-center gap-3 border-r border-[var(--cyan-border)] bg-[var(--bg-surface)] py-3 transition-all" style={{ width: '48px' }}>
                {/* 展开 */}
                <button
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--cyan-border)] text-[var(--text-secondary)] transition-all hover:border-[var(--cyan-border-bright)] hover:text-[var(--cyan)]"
                    onClick={onToggleCollapse}
                    title="展开侧边栏"
                >
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                    </svg>
                </button>
                {/* 新对话 (折叠态小按钮) */}
                <button
                    className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-all ${
                        isDraft
                            ? 'border-[var(--cyan-border-bright)] bg-[rgba(0,229,255,0.08)] text-[var(--cyan)]'
                            : 'border-[var(--cyan-border)] text-[var(--text-secondary)] hover:border-[var(--cyan-border-bright)] hover:text-[var(--cyan)]'
                    } ${disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
                    onClick={onNewChat}
                    disabled={disabled}
                    title="新建对话"
                >
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                </button>
            </aside>
        )
    }

    // 展开态 — 固定侧边栏
    return (
        <aside className="flex flex-col border-r border-[var(--cyan-border)] bg-gradient-to-b from-[var(--bg-surface)] to-[var(--bg-base)] transition-all" style={{ width: '260px' }}>
            {/* 顶部: 折叠 + 新建对话 */}
            <div className="flex items-center gap-2 border-b border-[var(--cyan-border)] p-3">
                <button
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--cyan-border)] text-[var(--text-secondary)] transition-all hover:border-[var(--cyan-border-bright)] hover:text-[var(--cyan)]"
                    onClick={onToggleCollapse}
                    title="收起侧边栏"
                >
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="14" height="14">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m4 14l7-7-7-7" />
                    </svg>
                </button>
                <button
                    className={`flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-all ${
                        isDraft
                            ? 'border-[var(--cyan-border-bright)] bg-[rgba(0,229,255,0.08)] text-[var(--cyan)] shadow-[0_0_12px_var(--cyan-glow)]'
                            : 'border-[var(--cyan-border)] bg-[rgba(0,229,255,0.03)] text-[var(--text-secondary)] hover:border-[var(--cyan-border-bright)] hover:bg-[rgba(0,229,255,0.06)] hover:text-[var(--cyan)]'
                    } ${disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
                    onClick={onNewChat}
                    disabled={disabled}
                >
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="14" height="14">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    <span>新建对话</span>
                </button>
            </div>

            {/* 会话列表 */}
            <div className="flex-1 overflow-y-auto p-2" style={{ scrollbarWidth: 'none' }}>
                {conversations.length === 0 ? (
                    <div className="px-3 py-8 text-center text-xs text-[var(--text-muted)]">
                        暂无历史会话
                    </div>
                ) : (
                    <div className="flex flex-col gap-1">
                        {conversations.map((conv) => {
                            const isSelected = conv.conversationId === selectedId && !isDraft
                            const isEditing = editingId === conv.conversationId
                            return (
                                <div
                                    key={conv.conversationId}
                                    className={`group relative cursor-pointer rounded-lg border px-3 py-2.5 transition-all ${
                                        isSelected
                                            ? 'border-[var(--cyan-border-bright)] bg-[rgba(0,229,255,0.06)] shadow-[0_0_8px_var(--cyan-glow)]'
                                            : 'border-transparent hover:border-[var(--cyan-border)] hover:bg-[rgba(0,229,255,0.03)]'
                                    } ${disabled && !isSelected ? 'pointer-events-none opacity-50' : ''}`}
                                    onClick={() => !disabled && !isEditing && onSelect(conv.conversationId)}
                                >
                                    {isEditing ? (
                                        <input
                                            className="w-full bg-transparent border-b border-[var(--cyan-border)] outline-none text-xs text-[var(--text-primary)]"
                                            value={editText}
                                            onChange={(e) => setEditText(e.target.value)}
                                            onKeyDown={handleFinishEdit}
                                            onBlur={() => setEditingId('')}
                                            autoFocus
                                            onClick={(e) => e.stopPropagation()}
                                        />
                                    ) : (
                                        <>
                                            <div className="flex items-center justify-between gap-2">
                                                <div className="truncate text-xs text-[var(--text-primary)]" style={{ fontFamily: '"SF Mono", monospace' }}>
                                                    {conv.title || '未命名对话'}
                                                </div>
                                                <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                                                    <button
                                                        className="flex h-5 w-5 items-center justify-center rounded text-[var(--text-muted)] hover:text-[var(--cyan)]"
                                                        onClick={(e) => handleStartEdit(e, conv)}
                                                        title="重命名"
                                                    >
                                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="11" height="11">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        className="flex h-5 w-5 items-center justify-center rounded text-[var(--text-muted)] hover:text-[var(--red-err)]"
                                                        onClick={(e) => { e.stopPropagation(); onDelete(conv.conversationId) }}
                                                        title="删除"
                                                    >
                                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="11" height="11">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            </div>
                                            <div className="mt-0.5 text-[10px] text-[var(--text-muted)]" style={{ fontFamily: '"SF Mono", monospace' }}>
                                                {formatTime(conv.lastActiveAt)}
                                            </div>
                                        </>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                )}
            </div>

            {/* 底部 */}
            <div className="border-t border-[var(--cyan-border)] px-3 py-2 text-[10px] text-[var(--text-muted)]">
                最多保留 10 个会话
            </div>
        </aside>
    )
}

export default ConversationSidebar
