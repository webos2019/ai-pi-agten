import { useState, useEffect, useCallback, useRef } from 'react'
import Header from './components/Header'
import ChatBody from './components/ChatBody'
import Footer from './components/Footer'
import ConversationSidebar from './components/ConversationSidebar'
import StockAnalysis from './components/StockAnalysis'
import ChanlunAnalysis from './components/ChanlunAnalysis'
import TextToImage from './components/TextToImage'
import OpsPilotDemo from './components/OpsPilotDemo'
import VoiceChat from './components/VoiceChat'
import SettingsPanel from './components/SettingsPanel'
import KnowledgeBase from './components/KnowledgeBase'
import { useChat } from './hooks/useChat'
import { useConversations } from './hooks/useConversations'
import { useInputHistory } from './hooks/useInputHistory'
import { slashCommands, atReferences } from './types'
import type { UploadedFile, StructuredRequest, AtReference } from './types'

export default function App() {
    const [view, setView] = useState<'chat' | 'stock' | 'chanlun' | 'image' | 'opspilot' | 'voice' | 'kb'>('chat')
    const [mode, setMode] = useState('utility-skill')
    const [clientIP, setClientIP] = useState<string | null>(null)
    const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([])
    const [atRefs, setAtRefs] = useState<AtReference[]>(atReferences)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
    const [settingsOpen, setSettingsOpen] = useState(false)
    const [llmModel, setLlmModel] = useState<string>('')
    const [llmHasKey, setLlmHasKey] = useState<boolean>(true)

    const chat = useChat()
    const conversations = useConversations()
    const inputHistory = useInputHistory()

    // ─── local-first 恢复: 刷新后先从 IndexedDB 加载快照 ────
    useEffect(() => {
        if (!conversations.sessionId) return
        let cancelled = false

        const recover = async () => {
            const localResult = await conversations.recoverFromLocal()
            if (cancelled || !localResult) return

            // 即时展示本地快照
            chat.hydrateFromLocal(localResult.messages)
            conversations.setIsReadOnly(false)

            // 后台静默校准: 请求服务端 hydration
            const calibration = await conversations.calibrateWithServer(localResult.conversationId)
            if (cancelled) return

            if (!calibration.available) {
                // 服务端不可用 → 只读降级
                conversations.setIsReadOnly(true)
            } else if (calibration.data) {
                // 服务端可用 → 用纯文本 hydration 数据替换
                // 注意: 服务端 hydration 只有纯文本，不包含 blocks
                // 所以只在服务端消息数 > 本地快照消息数时才替换
                // (说明本地快照可能过期)
                const serverMsgCount = calibration.data.messages?.length || 0
                const localMsgCount = localResult.messages.length
                if (serverMsgCount > localMsgCount) {
                    chat.hydrate(calibration.data)
                }
                conversations.setIsReadOnly(false)
            }
        }

        recover()
        return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [conversations.sessionId])

    // Fetch public IP on mount + load LLM config
    useEffect(() => {
        fetch('https://api.ipify.org?format=json')
            .then(r => r.json())
            .then(data => {
                if (data.ip) {
                    setClientIP(data.ip)
                    setAtRefs(prev => [...prev, {
                        label: 'IP: ' + data.ip, type: 'context',
                        desc: '客户端公网IP地址', keywords: ['ip', 'IP', '地址'],
                    }])
                }
            })
            .catch(() => {})
        // Load LLM config for header status indicator
        fetch('/api/settings/llm')
            .then(r => r.json())
            .then(data => {
                if (data.config) {
                    setLlmModel(data.config.model)
                    setLlmHasKey(data.config.hasKey)
                }
            })
            .catch(() => {})
    }, [])

    // Expose setMode and clearMessages for slash commands
    useEffect(() => {
        ;(window as any).__setMode = setMode
        ;(window as any).__clearMessages = chat.clearToDraft
    }, [chat.clearToDraft])

    // ─── 监听语音页面的 token 消耗 ─────────────────────
    const [voiceTokenUsage, setVoiceTokenUsage] = useState({ prompt: 0, completion: 0, total: 0 })
    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent).detail
            setVoiceTokenUsage(prev => ({
                prompt: prev.prompt + (detail.prompt || 0),
                completion: prev.completion + (detail.completion || 0),
                total: prev.total + (detail.total || 0),
            }))
        }
        window.addEventListener('voice-token-usage', handler)
        return () => window.removeEventListener('voice-token-usage', handler)
    }, [])

    // 合并 chat token + voice token 给 Header 显示
    const combinedTokenUsage = {
        prompt: chat.tokenUsage.prompt + voiceTokenUsage.prompt,
        completion: chat.tokenUsage.completion + voiceTokenUsage.completion,
        total: chat.tokenUsage.total + voiceTokenUsage.total,
    }

    // ─── 今日 Token 花费追踪 (localStorage 按日期持久化) ────
    const DAILY_TOKEN_LIMIT = 500000 // 每日 500k 上限
    const todayKey = new Date().toISOString().slice(0, 10) // YYYY-MM-DD
    const [todayTokens, setTodayTokens] = useState<number>(() => {
        try {
            const raw = localStorage.getItem('pi_token_today')
            if (raw) {
                const data = JSON.parse(raw)
                if (data.date === todayKey) return data.total || 0
            }
        } catch {}
        return 0
    })
    const prevTotalRef = useRef(0) // 用于计算增量
    useEffect(() => {
        const delta = combinedTokenUsage.total - prevTotalRef.current
        if (delta > 0) {
            setTodayTokens(prev => {
                const next = prev + delta
                try {
                    localStorage.setItem('pi_token_today', JSON.stringify({ date: todayKey, total: next }))
                } catch {}
                return next
            })
        }
        prevTotalRef.current = combinedTokenUsage.total
    }, [combinedTokenUsage.total, todayKey])

    // ─── 新建空白草稿 ─────────────────────────────────
    const handleNewChat = useCallback(() => {
        if (chat.isStreaming) return
        chat.clearToDraft()
        conversations.startNewDraft()
    }, [chat, conversations])

    // ─── 切换工作区 (一人多公司) ─────────────────────
    const handleSwitchWorkspace = useCallback(async (workspaceId: string) => {
        if (chat.isStreaming) return
        chat.clearToDraft()
        await conversations.switchWorkspace(workspaceId)
        // 刷新 LLM 配置状态
        const sid = conversations.workspaces.find(w => w.id === workspaceId)?.sessionId || ''
        if (sid) {
            fetch(`/api/settings/llm?session_id=${encodeURIComponent(sid)}`)
                .then(r => r.json())
                .then(data => {
                    if (data.config) {
                        setLlmModel(data.config.model)
                        setLlmHasKey(data.config.hasKey)
                    }
                })
                .catch(() => {})
        }
    }, [chat, conversations])

    // ─── 创建工作区 ─────────────────────────────────
    const handleCreateWorkspace = useCallback(async (name: string) => {
        if (chat.isStreaming) return
        chat.clearToDraft()
        await conversations.createWorkspace(name)
        // 刷新 LLM 配置状态 (新工作区默认用全局配置)
        setLlmModel('')
        setLlmHasKey(true)
    }, [chat, conversations])

    // ─── 删除工作区 ─────────────────────────────────
    const handleDeleteWorkspace = useCallback(async (workspaceId: string) => {
        if (chat.isStreaming) return
        chat.clearToDraft()
        await conversations.deleteWorkspace(workspaceId)
    }, [chat, conversations])

    // ─── 切换会话 (local-first + 服务端校验) ─────────────
    const handleSelectConversation = useCallback(async (conversationId: string) => {
        if (chat.isStreaming) return
        const { localMessages, serverData } = await conversations.selectConversationLocalFirst(conversationId)
        
        // 优先用本地快照（含富 UI blocks），没有再降级到服务端纯文本
        if (localMessages && localMessages.length > 0) {
            chat.hydrateFromLocal(localMessages)
            // 如果服务端消息更多，说明本地快照可能过期，用服务端数据替换
            if (serverData && (serverData.messages?.length || 0) > localMessages.length) {
                chat.hydrate(serverData)
            }
        } else if (serverData) {
            chat.hydrate(serverData)
        }
    }, [chat, conversations])

    // ─── 删除会话 ─────────────────────────────────────
    const handleDeleteConversation = useCallback(async (conversationId: string) => {
        if (chat.isStreaming) return
        const newSelectedId = await conversations.deleteConversation(conversationId)
        if (conversationId === conversations.selectedId) {
            if (newSelectedId) {
                const { localMessages, serverData } = await conversations.selectConversationLocalFirst(newSelectedId)
                if (localMessages && localMessages.length > 0) {
                    chat.hydrateFromLocal(localMessages)
                    if (serverData && (serverData.messages?.length || 0) > localMessages.length) {
                        chat.hydrate(serverData)
                    }
                } else if (serverData) {
                    chat.hydrate(serverData)
                }
            } else {
                chat.clearToDraft()
                conversations.startNewDraft()
            }
        }
    }, [chat, conversations])

    // ─── 发送消息 ──────────────────────────────────────
    const handleSend = useCallback(async (rawText: string, structured: StructuredRequest) => {
        // 只读模式下禁止发送
        if (conversations.isReadOnly) return

        inputHistory.addToHistory(rawText)

        const sessionContext: { sessionId: string; conversationId?: string; createConversation?: boolean } = {
            sessionId: conversations.sessionId,
        }
        if (conversations.isDraft) {
            const title = rawText.slice(0, 30) || '新对话'
            const result = await conversations.createConversation(title)
            if (result) {
                sessionContext.conversationId = result.conversationId
            } else {
                sessionContext.createConversation = true
            }
        } else if (conversations.selectedId) {
            sessionContext.conversationId = conversations.selectedId
            conversations.touchConversation(conversations.selectedId)
        }

        // 发送完成后异步保存本地索引（标题 + 活跃时间）
        // 注意：不在此处保存快照，快照由 useChat.ts 在流式完成后保存
        // 此处 chat.messages 是闭包旧值，用它保存会覆盖正确的快照
        const convId = sessionContext.conversationId
        const convTitle = conversations.isDraft ? (rawText.slice(0, 30) || '新对话') : ''
        if (convId) {
            setTimeout(() => {
                conversations.saveLocalIndex(convId, convTitle, [])
            }, 2000)
        }

        chat.sendMessage(rawText, structured, mode, clientIP, uploadedFiles, sessionContext)
        setUploadedFiles([])
    }, [chat, mode, clientIP, uploadedFiles, inputHistory, conversations])

    const handleFileDrop = useCallback((files: FileList) => {
        for (let i = 0; i < files.length; i++) {
            const file = files[i]
            const ext = '.' + file.name.split('.').pop()?.toLowerCase()
            const allowed = ['.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.md','.json','.yaml','.yml','.css','.scss','.sql','.sh','.bash','.toml','.xml','.html','.vue','.svelte','.c','.cpp','.h','.hpp','.rb','.php','.swift','.kt','.dart','.txt']
            if (!allowed.includes(ext) || file.size > 1024 * 1024) continue
            const reader = new FileReader()
            reader.onload = (e) => {
                const content = e.target?.result as string
                setUploadedFiles(prev => [...prev, { name: file.name, size: file.size, type: ext.slice(1), content }])
                setAtRefs(prev => [...prev, { label: file.name, type: 'file', desc: '已上传的文件', data: { file: file.name, content } }])
            }
            reader.readAsText(file)
        }
    }, [])

    const handleFileSelect = useCallback((files: FileList) => {
        handleFileDrop(files)
    }, [handleFileDrop])

    const handleFileRemove = useCallback((index: number) => {
        setUploadedFiles(prev => prev.filter((_, i) => i !== index))
    }, [])

    const handleRegenerate = useCallback(() => {
        const result = chat.regenerate()
        if (result) {
            setUploadedFiles(result.files)
            const sessionContext = conversations.selectedId
                ? { sessionId: conversations.sessionId, conversationId: conversations.selectedId }
                : { sessionId: conversations.sessionId }
            chat.sendMessage(result.text, null, mode, clientIP, result.files, sessionContext)
        }
    }, [chat, mode, clientIP, conversations])

    const handleRetry = useCallback(() => {
        const result = chat.retry()
        if (result) {
            setUploadedFiles(result.files)
            const sessionContext = conversations.selectedId
                ? { sessionId: conversations.sessionId, conversationId: conversations.selectedId }
                : { sessionId: conversations.sessionId }
            chat.sendMessage(result.text, null, mode, clientIP, result.files, sessionContext)
        }
    }, [chat, mode, clientIP, conversations])

    const isRetrying = chat.status === 'retrying'

    return (
        <div className="flex h-screen overflow-hidden">
            {/* 左侧会话边栏 */}
            <ConversationSidebar
                conversations={conversations.conversations}
                selectedId={conversations.selectedId}
                isDraft={conversations.isDraft}
                disabled={chat.isStreaming}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
                onNewChat={handleNewChat}
                onSelect={handleSelectConversation}
                onDelete={handleDeleteConversation}
                onRename={conversations.renameConversation}
            />

            {/* 右侧主区域 */}
            <div className="flex flex-1 flex-col overflow-hidden">
                {/* 只读降级提示条 */}
                {conversations.isReadOnly && (
                    <div className="readonly-banner">
                        <span className="readonly-banner-icon">⚠</span>
                        <span>离线模式 · 显示的是本地缓存快照，服务端不可用。发送消息已禁用。</span>
                        <button
                            className="readonly-banner-retry"
                            onClick={() => window.location.reload()}
                        >
                            重试连接
                        </button>
                    </div>
                )}
                <Header
                    mode={mode}
                    onSetMode={setMode}
                    hasMessages={chat.messages.length > 0}
                    onRegenerate={handleRegenerate}
                    tokenUsage={combinedTokenUsage}
                    todayTokens={todayTokens}
                    dailyTokenLimit={DAILY_TOKEN_LIMIT}
                    view={view}
                    onSetView={setView}
                    onOpenSettings={() => setSettingsOpen(true)}
                    llmModel={llmModel}
                    llmHasKey={llmHasKey}
                    workspaces={conversations.workspaces}
                    activeWorkspace={conversations.activeWorkspace}
                    onSwitchWorkspace={handleSwitchWorkspace}
                    onCreateWorkspace={handleCreateWorkspace}
                    onDeleteWorkspace={handleDeleteWorkspace}
                    onRenameWorkspace={conversations.renameWorkspace}
                />
                {view === 'stock' ? (
                    <div className="flex-1 overflow-auto stock-view-container">
                        <StockAnalysis />
                    </div>
                ) : view === 'chanlun' ? (
                    <div className="flex-1 overflow-auto chanlun-view-container">
                        <ChanlunAnalysis />
                    </div>
                ) : view === 'image' ? (
                    <div className="flex-1 overflow-auto image-view-container">
                        <TextToImage />
                    </div>
                ) : view === 'opspilot' ? (
                    <div className="flex-1 overflow-auto opspilot-view-container">
                        <OpsPilotDemo />
                    </div>
                ) : view === 'voice' ? (
                    <div className="flex-1 overflow-hidden voicechat-view-container">
                        <VoiceChat />
                    </div>
                ) : view === 'kb' ? (
                    <div className="flex-1 overflow-auto kb-view-container">
                        <KnowledgeBase sessionId={conversations.sessionId} />
                    </div>
                ) : (
                    <>
                        <ChatBody
                            messages={chat.messages}
                            isStreaming={chat.isStreaming}
                            streamingText={chat.streamingText}
                            streamingBlocks={chat.streamingBlocks}
                            error={chat.error}
                            mode={mode}
                        />
                        <Footer
                            isStreaming={chat.isStreaming}
                            isRetrying={isRetrying}
                            slashCommands={slashCommands}
                            atReferences={atRefs}
                            onSend={handleSend}
                            onFileDrop={handleFileDrop}
                            onFileSelect={handleFileSelect}
                            onFileRemove={handleFileRemove}
                            uploadedFiles={uploadedFiles}
                            onCancel={chat.cancelStream}
                            steerQueueId={chat.steerQueueId}
                            steerError={chat.steerError}
                            onSteer={chat.sendSteer}
                            disabled={conversations.isReadOnly}
                        />
                    </>
                )}
            </div>
            <SettingsPanel
                open={settingsOpen}
                sessionId={conversations.sessionId}
                workspaceName={conversations.activeWorkspace?.name}
                onClose={() => {
                    setSettingsOpen(false)
                    // 刷新当前工作区的 LLM 状态
                    const sid = conversations.sessionId
                    const query = sid ? `?session_id=${encodeURIComponent(sid)}` : ''
                    fetch(`/api/settings/llm${query}`)
                        .then(r => r.json())
                        .then(data => {
                            if (data.config) {
                                setLlmModel(data.config.model)
                                setLlmHasKey(data.config.hasKey)
                            }
                        })
                        .catch(() => {})
                }}
            />
        </div>
    )
}
