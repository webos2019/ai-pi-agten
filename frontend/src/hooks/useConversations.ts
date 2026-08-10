import { useState, useCallback, useEffect, useRef } from 'react'
import type { ChatMessage, Workspace } from '../types'
import {
    loadIndex,
    saveIndex,
    loadSnapshot,
    saveSnapshot,
    deleteSnapshot,
    reconcileSnapshots,
    type ConversationIndexData,
    type ConversationSnapshot,
} from '../lib/localChatStore'
import { projectRecoverableMessages } from '../lib/stableSnapshot'

const SESSION_ID_KEY = 'pi_session_id'
const SELECTED_KEY = 'pi_selected_conversation'
const WORKSPACES_KEY = 'pi_workspaces'
const ACTIVE_WS_KEY = 'pi_active_workspace'

// ─── 工作区 localStorage 工具 ──────────────────────

function loadWorkspaces(): Workspace[] {
    const raw = localStorage.getItem(WORKSPACES_KEY)
    if (!raw) return []
    try { return JSON.parse(raw) as Workspace[] } catch { return [] }
}

function saveWorkspaces(ws: Workspace[]): void {
    localStorage.setItem(WORKSPACES_KEY, JSON.stringify(ws))
}

function loadActiveWsId(): string {
    return localStorage.getItem(ACTIVE_WS_KEY) || ''
}

function saveActiveWsId(id: string): void {
    localStorage.setItem(ACTIVE_WS_KEY, id)
}

function genSessionId(): string {
    return 'sess_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

function genWsId(): string {
    return 'ws_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

// ─── 会话列表内联刷新 (绕过 sessionId 闭包) ──────────

async function fetchConversationList(sid: string): Promise<{
    conversations: ConversationItem[]
    selectedConversationId?: string
} | null> {
    try {
        const resp = await fetch(`/api/conversations?session_id=${sid}`)
        const data = await resp.json()
        if (data.conversations) {
            return {
                conversations: data.conversations,
                selectedConversationId: data.selectedConversationId,
            }
        }
    } catch { /* ignore */ }
    return null
}

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
 * 本地快照恢复结果
 * - messages: 从 IndexedDB 恢复的完整消息（含 blocks）
 * - isReadOnly: 服务端 hydration 不可用时进入只读模式
 */
export interface LocalRecoveryResult {
    messages: ChatMessage[]
    isReadOnly: boolean
    conversationId: string
    title: string
}

/**
 * 多会话短期记忆容器 Hook (v0.4.7 local-first)
 * + 多工作区支持 (一人多公司)
 *
 * 工作区是 session_id 之上的隔离层:
 * - 每个工作区有独立的 sessionId → 独立的对话、记忆、LLM 配置
 * - 切换工作区 = 切换 session_id = 全新隔离环境
 */
export function useConversations() {
    const [sessionId, setSessionId] = useState<string>('')
    const [workspaces, setWorkspaces] = useState<Workspace[]>([])
    const [activeWorkspaceId, setActiveWorkspaceId] = useState<string>('')
    const [conversations, setConversations] = useState<ConversationItem[]>([])
    const [selectedId, setSelectedId] = useState<string>('')
    const [isDraft, setIsDraft] = useState(true) // 空白草稿态
    const [isLoading, setIsLoading] = useState(false)
    const [isReadOnly, setIsReadOnly] = useState(false) // 只读降级标志
    const hydratedRef = useRef(false)

    // ─── 初始化: 工作区 + sessionId ─────────────────────
    useEffect(() => {
        let wsList = loadWorkspaces()

        // 如果没有工作区，用当前 session_id 创建默认工作区
        if (wsList.length === 0) {
            let sid = localStorage.getItem(SESSION_ID_KEY)
            if (!sid) {
                sid = genSessionId()
                localStorage.setItem(SESSION_ID_KEY, sid)
            }
            const defaultWs: Workspace = {
                id: genWsId(),
                name: '默认公司',
                sessionId: sid,
                createdAt: Date.now(),
            }
            wsList = [defaultWs]
            saveWorkspaces(wsList)
        }

        setWorkspaces(wsList)

        // 加载激活的工作区
        let activeId = loadActiveWsId()
        let activeWs = wsList.find(w => w.id === activeId)
        if (!activeWs) {
            activeWs = wsList[0]
            activeId = activeWs.id
            saveActiveWsId(activeId)
        }
        setActiveWorkspaceId(activeId)

        // 从工作区取 sessionId
        localStorage.setItem(SESSION_ID_KEY, activeWs.sessionId)
        setSessionId(activeWs.sessionId)
    }, [])

    // ─── local-first 恢复: 刷新后先从 IndexedDB 加载 ────
    const recoverFromLocal = useCallback(async (): Promise<LocalRecoveryResult | null> => {
        const indexData = await loadIndex()
        if (!indexData || !indexData.selectedConversationId) {
            return null
        }

        const convId = indexData.selectedConversationId
        const snapshot = await loadSnapshot(convId)
        if (!snapshot || !snapshot.messages || snapshot.messages.length === 0) {
            return null
        }

        // 找到会话标题
        const convEntry = indexData.conversations.find(
            c => c.conversationId === convId,
        )

        return {
            messages: snapshot.messages,
            isReadOnly: false, // local 恢复本身不设只读，等服务端校准
            conversationId: convId,
            title: convEntry?.title || '恢复的对话',
        }
    }, [])

    // ─── 权威校准: 后台请求服务端 hydration ──────────────
    // 返回 true 表示服务端可用，false 表示不可用（需要只读降级）
    const calibrateWithServer = useCallback(async (
        conversationId: string,
    ): Promise<{ available: boolean; data: HydrationResult | null }> => {
        if (!sessionId || !conversationId) {
            return { available: false, data: null }
        }
        try {
            const resp = await fetch(
                `/api/conversations/${conversationId}?session_id=${sessionId}`,
            )
            if (resp.status === 503) {
                // 服务端不可用 → 只读降级
                return { available: false, data: null }
            }
            if (!resp.ok) {
                return { available: false, data: null }
            }
            const data: HydrationResult = await resp.json()

            // 服务端校验选中 + touch
            await fetch(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, select: true }),
            }).catch(() => {})

            return { available: true, data }
        } catch {
            return { available: false, data: null }
        }
    }, [sessionId])

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
                // 权威校准: 清理本地快照中服务端已不存在的会话
                const serverIds = data.conversations.map((c: ConversationItem) => c.conversationId)
                reconcileSnapshots(serverIds).catch(() => {})
            }
        } catch { /* ignore */ }
    }, [sessionId])

    // sessionId 就绪后: local-first 恢复 + 后台服务端校准
    useEffect(() => {
        if (sessionId && !hydratedRef.current) {
            hydratedRef.current = true
            const savedSelected = localStorage.getItem(SELECTED_KEY)
            if (savedSelected) setSelectedId(savedSelected)
            refreshList()
        }
    }, [sessionId, refreshList])

    // ─── 工作区变化时刷新会话列表 ─────────────────────
    // 跳过首次（由上面的 init 处理），后续切换工作区时触发
    const prevSidRef = useRef('')
    useEffect(() => {
        if (!sessionId) return
        if (prevSidRef.current && prevSidRef.current !== sessionId) {
            // 工作区切换 → 重新加载会话列表
            refreshList()
        }
        prevSidRef.current = sessionId
    }, [sessionId, refreshList])

    // ─── 保存本地索引 + 快照 ────────────────────────────
    const saveLocalIndex = useCallback(async (
        convId: string,
        title: string,
        messages: ChatMessage[],
    ) => {
        const existingIndex = await loadIndex()
        const existingConvs = existingIndex?.conversations || []

        // 更新或添加会话条目
        const convEntry = existingConvs.find(c => c.conversationId === convId)
        const hasMessages = messages.length > 0
        const updatedConvs = convEntry
            ? existingConvs.map(c =>
                c.conversationId === convId
                    ? { ...c, title, lastActiveAt: Date.now(), hasMessages: hasMessages || c.hasMessages }
                    : c,
            )
            : [
                ...existingConvs,
                { conversationId: convId, title, lastActiveAt: Date.now(), hasMessages },
            ]

        const newIndexData: ConversationIndexData = {
            conversations: updatedConvs,
            selectedConversationId: convId,
            revision: (existingIndex?.revision || 0) + 1,
        }

        // 只有传入非空消息时才保存快照（避免覆盖 useChat.ts 保存的正确快照）
        if (hasMessages) {
            await Promise.all([
                saveIndex(newIndexData),
                saveSnapshot(convId, projectRecoverableMessages(messages), Date.now()),
            ])
        } else {
            await saveIndex(newIndexData)
        }
    }, [])

    // ─── 新建空白草稿 (不立即持久化) ─────────────────
    const startNewDraft = useCallback(() => {
        setSelectedId('')
        setIsDraft(true)
        setIsReadOnly(false)
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
                setIsReadOnly(false)
                localStorage.setItem(SELECTED_KEY, data.conversationId)
                await refreshList()
                return { conversationId: data.conversationId, threadId: data.threadId }
            }
        } catch { /* ignore */ }
        return null
    }, [sessionId, refreshList])

    // ─── 切换会话 (local-first + 服务端校验) ─────────────
    // 先从 IndexedDB 加载本地快照（含富 UI blocks），再后台请求服务端校准
    const selectConversationLocalFirst = useCallback(async (
        conversationId: string,
    ): Promise<{ localMessages: ChatMessage[] | null; serverData: HydrationResult | null }> => {
        if (!sessionId || !conversationId) return { localMessages: null, serverData: null }

        // 1. 先从 IndexedDB 加载本地快照
        let localMessages: ChatMessage[] | null = null
        try {
            const snapshot = await loadSnapshot(conversationId)
            if (snapshot && snapshot.messages && snapshot.messages.length > 0) {
                localMessages = snapshot.messages
            }
        } catch { /* ignore */ }

        // 2. 后台请求服务端 hydration（不阻塞）
        let serverData: HydrationResult | null = null
        try {
            const resp = await fetch(`/api/conversations/${conversationId}?session_id=${sessionId}`)
            if (resp.ok) {
                serverData = await resp.json()
            }
        } catch { /* ignore */ }

        // 3. 服务端校验选中 + touch
        try {
            await fetch(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, select: true }),
            })
        } catch { /* ignore */ }

        setSelectedId(conversationId)
        setIsDraft(false)
        setIsReadOnly(false)
        localStorage.setItem(SELECTED_KEY, conversationId)
        refreshList()

        return { localMessages, serverData }
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
            setIsReadOnly(false)
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
            // 同步删除本地快照
            await deleteSnapshot(conversationId)
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

    // ═══════════════════════════════════════════════════
    //  工作区管理 (一人多公司)
    // ═══════════════════════════════════════════════════

    // ─── 切换工作区 ─────────────────────────────────
    const switchWorkspace = useCallback(async (workspaceId: string) => {
        const ws = workspaces.find(w => w.id === workspaceId)
        if (!ws) return

        setActiveWorkspaceId(workspaceId)
        saveActiveWsId(workspaceId)
        localStorage.setItem(SESSION_ID_KEY, ws.sessionId)
        setSessionId(ws.sessionId)

        // 清空当前会话状态
        setConversations([])
        setSelectedId('')
        setIsDraft(true)
        localStorage.removeItem(SELECTED_KEY)

        // 内联加载新工作区的会话列表 (绕过 sessionId 闭包延迟)
        const result = await fetchConversationList(ws.sessionId)
        if (result) {
            setConversations(result.conversations)
            if (result.selectedConversationId) {
                setSelectedId(result.selectedConversationId)
                setIsDraft(false)
            }
        }
    }, [workspaces])

    // ─── 创建工作区 ─────────────────────────────────
    const createWorkspace = useCallback(async (name: string): Promise<string | null> => {
        const newSid = genSessionId()
        const newWs: Workspace = {
            id: genWsId(),
            name: name || '新公司',
            sessionId: newSid,
            createdAt: Date.now(),
        }
        const updated = [...workspaces, newWs]
        setWorkspaces(updated)
        saveWorkspaces(updated)

        // 切换到新工作区
        setActiveWorkspaceId(newWs.id)
        saveActiveWsId(newWs.id)
        localStorage.setItem(SESSION_ID_KEY, newSid)
        setSessionId(newSid)

        // 清空会话状态
        setConversations([])
        setSelectedId('')
        setIsDraft(true)
        localStorage.removeItem(SELECTED_KEY)

        return newWs.id
    }, [workspaces])

    // ─── 删除工作区 ─────────────────────────────────
    const deleteWorkspace = useCallback(async (workspaceId: string) => {
        if (workspaces.length <= 1) return // 不能删除最后一个工作区

        const updated = workspaces.filter(w => w.id !== workspaceId)
        setWorkspaces(updated)
        saveWorkspaces(updated)

        // 如果删的是激活的工作区，切到另一个
        if (activeWorkspaceId === workspaceId) {
            const newActive = updated[0]
            setActiveWorkspaceId(newActive.id)
            saveActiveWsId(newActive.id)
            localStorage.setItem(SESSION_ID_KEY, newActive.sessionId)
            setSessionId(newActive.sessionId)

            setConversations([])
            setSelectedId('')
            setIsDraft(true)
            localStorage.removeItem(SELECTED_KEY)

            const result = await fetchConversationList(newActive.sessionId)
            if (result) {
                setConversations(result.conversations)
                if (result.selectedConversationId) {
                    setSelectedId(result.selectedConversationId)
                    setIsDraft(false)
                }
            }
        }
    }, [workspaces, activeWorkspaceId])

    // ─── 重命名工作区 ─────────────────────────────────
    const renameWorkspace = useCallback((workspaceId: string, name: string) => {
        const updated = workspaces.map(w =>
            w.id === workspaceId ? { ...w, name } : w
        )
        setWorkspaces(updated)
        saveWorkspaces(updated)
    }, [workspaces])

    const activeWorkspace = workspaces.find(w => w.id === activeWorkspaceId) || null

    return {
        sessionId,
        conversations,
        selectedId,
        isDraft,
        isLoading,
        isReadOnly,
        setIsReadOnly,
        startNewDraft,
        createConversation,
        selectConversation,
        selectConversationLocalFirst,
        deleteConversation,
        renameConversation,
        touchConversation,
        refreshList,
        recoverFromLocal,
        calibrateWithServer,
        saveLocalIndex,
        // 工作区
        workspaces,
        activeWorkspace,
        activeWorkspaceId,
        switchWorkspace,
        createWorkspace,
        deleteWorkspace,
        renameWorkspace,
    }
}
