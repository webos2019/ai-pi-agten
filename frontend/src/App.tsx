import { useState, useEffect, useCallback } from 'react'
import Header from './components/Header'
import ChatBody from './components/ChatBody'
import Footer from './components/Footer'
import ConversationSidebar from './components/ConversationSidebar'
import { useChat } from './hooks/useChat'
import { useConversations } from './hooks/useConversations'
import { useInputHistory } from './hooks/useInputHistory'
import { slashCommands, atReferences } from './types'
import type { UploadedFile, StructuredRequest, AtReference } from './types'

export default function App() {
    const [mode, setMode] = useState('utility-skill')
    const [clientIP, setClientIP] = useState<string | null>(null)
    const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([])
    const [atRefs, setAtRefs] = useState<AtReference[]>(atReferences)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

    const chat = useChat()
    const conversations = useConversations()
    const inputHistory = useInputHistory()

    // Fetch public IP on mount
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
    }, [])

    // Expose setMode and clearMessages for slash commands
    useEffect(() => {
        ;(window as any).__setMode = setMode
        ;(window as any).__clearMessages = chat.clearToDraft
    }, [chat.clearToDraft])

    // ─── 新建空白草稿 ─────────────────────────────────
    const handleNewChat = useCallback(() => {
        if (chat.isStreaming) return
        chat.clearToDraft()
        conversations.startNewDraft()
    }, [chat, conversations])

    // ─── 切换会话 (服务端校验 + hydration) ─────────────
    const handleSelectConversation = useCallback(async (conversationId: string) => {
        if (chat.isStreaming) return
        const data = await conversations.selectConversation(conversationId)
        if (data) {
            chat.hydrate(data)
        }
    }, [chat, conversations])

    // ─── 删除会话 ─────────────────────────────────────
    const handleDeleteConversation = useCallback(async (conversationId: string) => {
        if (chat.isStreaming) return
        const newSelectedId = await conversations.deleteConversation(conversationId)
        if (conversationId === conversations.selectedId) {
            if (newSelectedId) {
                const data = await conversations.selectConversation(newSelectedId)
                if (data) chat.hydrate(data)
            } else {
                chat.clearToDraft()
                conversations.startNewDraft()
            }
        }
    }, [chat, conversations])

    // ─── 发送消息 ──────────────────────────────────────
    const handleSend = useCallback(async (rawText: string, structured: StructuredRequest) => {
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
                <Header
                    mode={mode}
                    onSetMode={setMode}
                    hasMessages={chat.messages.length > 0}
                    onRegenerate={handleRegenerate}
                />
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
                />
            </div>
        </div>
    )
}
