import { useState, useCallback, useEffect, useRef } from 'react'

const SESSION_ID_KEY = 'pi_session_id'
const SELECTED_KEY = 'pi_selected_conversation'

export interface ConversationItem {
    conversationId: string
    title: string
    lastActiveAt: number
    hasMessages: boolean
}

export interface HydrationResult {
    conversationId: string
    threadId: string
    title: string
    messages: Array<{ id: string; role: string; text: string; createdAt: number }>
    summary: string
    pinnedDecisions: string[]
    restored: boolean
}

/**
 * 多会话短期记忆容器 Hook
 *
 * 配合后端 ThreadState (v0.3.0):
 * - 会话注册表: 管理当前浏览器会话下的会话索引 (≤10)
 * - 空白草稿: "新聊天"只进入草稿态, 首条消息后才创建正式会话
 * - 服务端校验选中会话: 恢复/上下文/写入都以服务端确认为准
 * - 流式输出中禁止切换: 防串线
 */
export function useConversations() {
    const [sessionId, setSessionId] = useState<string>('')
    const [conversations, setConversations] = useState<ConversationItem[]>([])
    const [selectedId, setSelectedId] = useState<string>('')
    const [isDraft, setIsDraft] = useState(true) // 空白草稿态
    const [isLoading, setIsLoading] = useState(false)
    const hydratedRef = useRef(false)

    // ─── 初始化 sessionId ─────────────────────────────
    useEffect(() => {
        let sid = localStorage.getItem(SESSION_ID_KEY)
        if (!sid) {
            sid = 'sess_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
            localStorage.setItem(SESSION_ID_KEY, sid)
        }
        setSessionId(sid)
    }, [])

    // ─── 加载会话列表 ─────────────────────────────────
    const refreshList = useCallback(async () => {
        if (!sessionId) return
        try {
            const resp = await fetch(`/api/conversations?session_id=${sessionId}`)
            const data = await resp.json()
            if (data.conversations) {
                setConversations(data.conversations)
                if (data.selectedConversationId) {
                    setSelectedId(data.selectedConversationId)
                    setIsDraft(false)
                }
            }
        } catch { /* ignore */ }
    }, [sessionId])

    // sessionId 就绪后加载会话列表
    useEffect(() => {
        if (sessionId && !hydratedRef.current) {
            hydratedRef.current = true
            const savedSelected = localStorage.getItem(SELECTED_KEY)
            if (savedSelected) setSelectedId(savedSelected)
            refreshList()
        }
    }, [sessionId, refreshList])

    // ─── 新建空白草稿 (不立即持久化) ─────────────────
    const startNewDraft = useCallback(() => {
        setSelectedId('')
        setIsDraft(true)
        localStorage.removeItem(SELECTED_KEY)
    }, [])

    // ─── 创建正式会话 (首条消息后调用) ───────────────
    const createConversation = useCallback(async (title?: string): Promise<{
        conversationId: string
        threadId: string
    } | null> => {
        if (!sessionId) return null
        try {
            const resp = await fetch('/api/conversations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, title: title || '新对话' }),
            })
            const data = await resp.json()
            if (data.conversationId) {
                setSelectedId(data.conversationId)
                setIsDraft(false)
                localStorage.setItem(SELECTED_KEY, data.conversationId)
                await refreshList()
                return { conversationId: data.conversationId, threadId: data.threadId }
            }
        } catch { /* ignore */ }
        return null
    }, [sessionId, refreshList])

    // ─── 切换会话 (服务端校验 + hydration) ─────────────────────────────────────────────
    const selectConversation = useCallback(async (conversationId: string): Promise<HydrationResult | null> => {
        if (!sessionId || !conversationId) return null
        try {
            // 获取 hydration 数据
            const resp = await fetch(`/api/conversations/${conversationId}?session_id=${sessionId}`)
            if (!resp.ok) return null
            const data: HydrationResult = await resp.json()

            // 服务端校验选中 + touch
            await fetch(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, select: true }),
            })

            setSelectedId(conversationId)
            setIsDraft(false)
            localStorage.setItem(SELECTED_KEY, conversationId)
            await refreshList()
            return data
        } catch { /* ignore */ }
        return null
    }, [sessionId, refreshList])

    // ─── 删除会话 ─────────────────────────────────────
    const deleteConversation = useCallback(async (conversationId: string): Promise<string> => {
        if (!sessionId) return ''
        try {
            const resp = await fetch(`/api/conversations/${conversationId}?session_id=${sessionId}`, {
                method: 'DELETE',
            })
            const data = await resp.json()
            await refreshList()
            // 返回新的选中会话
            return data.selectedConversationId || ''
        } catch { /* ignore */ }
        return ''
    }, [sessionId, refreshList])

    // ─── 重命名会话 ───────────────────────────────────
    const renameConversation = useCallback(async (conversationId: string, title: string) => {
        if (!sessionId) return
        try {
            await fetch(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, title }),
            })
            await refreshList()
        } catch { /* ignore */ }
    }, [sessionId, refreshList])

    // ─── 更新会话活跃时间 (发送消息时调用) ──────────────────────────────────────────────────
    const touchConversation = useCallback(async (conversationId: string) => {
        if (!sessionId || !conversationId) return
        try {
            await fetch(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, touch: true }),
            })
            await refreshList()
        } catch { /* ignore */ }
    }, [sessionId, refreshList])

    return {
        sessionId,
        conversations,
        selectedId,
        isDraft,
        isLoading,
        startNewDraft,
        createConversation,
        selectConversation,
        deleteConversation,
        renameConversation,
        touchConversation,
        refreshList,
    }
}
