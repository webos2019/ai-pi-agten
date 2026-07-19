import { useState, useRef, useCallback } from 'react'
import type { ChatMessage, ChatBlock, ChatStatus, StructuredRequest, UploadedFile } from '../types'
import { useStreamTextBuffer } from './useStreamTextBuffer'

const MAX_CONTEXT_ROUNDS = 8

export function useChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([])
    const [status, setStatus] = useState<ChatStatus>('idle')
    const [error, setError] = useState<string | null>(null)
    const [streamingBlocks, setStreamingBlocks] = useState<ChatBlock[]>([])
    const [streamingText, setStreamingText] = useState('')
    const [steerQueueId, setSteerQueueId] = useState<string | null>(null)
    const [steerError, setSteerError] = useState<string | null>(null)
    const abortRef = useRef<AbortController | null>(null)

    const isStreaming = status === 'loading' || status === 'streaming'

    // ─── 文本增量缓冲: 合并高频 text/reasoning delta ─────
    const handleFlush = useCallback((partType: 'text' | 'reasoning', accumulated: string) => {
        if (partType === 'text') {
            setStreamingText(prev => prev + accumulated)
            setStreamingBlocks(prev => {
                const next = [...prev]
                const last = next[next.length - 1]
                if (last && last.type === 'text') last.content = (last.content || '') + accumulated
                else next.push({ type: 'text', content: accumulated })
                return next
            })
        } else {
            setStreamingBlocks(prev => {
                const next = [...prev]
                const last = next[next.length - 1]
                if (last && last.type === 'reasoning') last.content = (last.content || '') + accumulated
                else next.push({ type: 'reasoning', content: accumulated })
                return next
            })
        }
    }, [])

    const { enqueue, flush, flushAndClear, reset } = useStreamTextBuffer(handleFlush)

    // ─── addChunk: text/reasoning 走缓冲，结构性 chunk 先 flush ──
    const addChunk = useCallback((chunk: any) => {
        // start chunk: 提取 steerQueueId，不加入 streamingBlocks
        if (chunk.type === 'start') {
            if (chunk.steerQueueId) setSteerQueueId(chunk.steerQueueId)
            return
        }
        if (chunk.type === 'text') { enqueue('text', chunk.content || ''); return }
        if (chunk.type === 'reasoning') { enqueue('reasoning', chunk.content || ''); return }

        flush()

        setStreamingBlocks(prev => {
            const next = [...prev]
            switch (chunk.type) {
                case 'steer_queued':
                    next.push({ type: 'steer_queued', steerId: chunk.steerId, steerText: chunk.steerText, queueSize: chunk.queueSize, content: chunk.steerText || '' })
                    break
                case 'steer_applied':
                    next.push({ type: 'steer_applied', steerId: chunk.steerId, steerText: chunk.steerText, appliedAtStep: chunk.appliedAtStep, actionType: chunk.actionType, content: chunk.steerText || '' })
                    break
                case 'steer_rejected':
                    next.push({ type: 'steer_rejected', steerId: chunk.steerId, steerText: chunk.steerText, reason: chunk.reason, content: chunk.steerText || '' })
                    break
                case 'tool_call':
                    next.push({ type: 'tool_call', toolName: chunk.toolName || '', toolArgs: chunk.toolArgs || {}, serverId: chunk.serverId, content: '' })
                    break
                case 'tool_result':
                    next.push({ type: 'tool_result', toolName: chunk.toolName || '', toolResult: chunk.toolResult || '', isValid: chunk.isValid, serverId: chunk.serverId, content: chunk.toolResult || '' })
                    break
                case 'resource_start':
                    next.push({ type: 'resource_start', resourceName: chunk.resourceName || '', resourceUri: chunk.resourceUri || '', serverId: chunk.serverId, content: '' })
                    break
                case 'resource_end':
                    next.push({ type: 'resource_end', content: chunk.contentPreview || '', resourceName: chunk.resourceName, resourceUri: chunk.resourceUri, serverId: chunk.serverId, isTruncated: chunk.isTruncated, previewChars: chunk.previewChars })
                    break
                case 'resource_error':
                    next.push({ type: 'resource_error', content: chunk.error || '', resourceName: chunk.resourceName, resourceUri: chunk.resourceUri, serverId: chunk.serverId })
                    break
                case 'error':
                    next.push({ type: 'text', content: '⚠️ 错误：' + (chunk.error || '服务端错误') })
                    break
                case 'recovering':
                    next.push({ type: 'text', content: '🔄 ' + chunk.message })
                    break
                case 'recovery_fallback':
                    next.push({ type: 'text', content: '📌 ' + chunk.message + ' (' + chunk.fallbackMethod + ')' })
                    break
                case 'agent_step_start': {
                    const lastStep = next[next.length - 1]
                    if (lastStep && lastStep.type === 'agent_step' && lastStep.runId === chunk.runId && lastStep.status === 'pending') {
                        lastStep.actionType = chunk.actionType
                        lastStep.title = chunk.title
                        lastStep.stepIndex = chunk.stepIndex
                    } else {
                        next.push({ type: 'agent_step', runId: chunk.runId, stepIndex: chunk.stepIndex, actionType: chunk.actionType, title: chunk.title, status: 'pending', content: '' })
                    }
                    break
                }
                case 'agent_step_end': {
                    const matchingStep = [...next].reverse().find(b => b.type === 'agent_step' && b.runId === chunk.runId && b.stepIndex === chunk.stepIndex)
                    if (matchingStep) {
                        matchingStep.status = chunk.status
                        matchingStep.summary = chunk.summary
                        matchingStep.durationMs = chunk.durationMs
                    } else {
                        next.push({ type: 'agent_step', runId: chunk.runId, stepIndex: chunk.stepIndex, status: chunk.status, summary: chunk.summary, durationMs: chunk.durationMs, content: '' })
                    }
                    break
                }
            }
            return next
        })

        if (chunk.type === 'error') setStatus('retrying')
    }, [enqueue, flush])

    const trimMsgs = useCallback((msgs: ChatMessage[]): ChatMessage[] => {
        const indices = msgs.map((m, i) => m.role === 'assistant' ? i : -1).filter(i => i !== -1)
        if (indices.length <= MAX_CONTEXT_ROUNDS) return msgs
        return msgs.slice(indices[indices.length - MAX_CONTEXT_ROUNDS])
    }, [])

    // ─── 恢复会话 (从 hydration 数据加载消息) ──────────
    const hydrate = useCallback((data: {
        messages?: Array<{ id: string; role: string; text: string }>
        summary?: string
        pinnedDecisions?: string[]
    }) => {
        setMessages((data.messages || []).map(m => ({
            role: m.role as 'user' | 'assistant',
            content: m.text,
        })))
        setError(null)
        setStreamingBlocks([])
        setStreamingText('')
        reset()
        setStatus('idle')
    }, [reset])

    // ─── 清空到空白草稿 ────────────────────────────────
    const clearToDraft = useCallback(() => {
        setMessages([])
        setStreamingBlocks([])
        setStreamingText('')
        setError(null)
        reset()
        setStatus('idle')
        if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    }, [reset])

    // ─── 发送消息 ────────────────────────────────────────────────────────────────────────────
    const sendMessage = useCallback(async (
        rawText: string,
        structured: StructuredRequest | null,
        mode: string,
        clientIP: string | null,
        uploadedFiles: UploadedFile[],
        sessionContext?: { sessionId: string; conversationId?: string; createConversation?: boolean },
    ) => {
        if (!rawText.trim() && uploadedFiles.length === 0) return
        if (status === 'loading' || status === 'streaming') return
        if (abortRef.current) abortRef.current.abort()
        abortRef.current = new AbortController()

        setError(null)
        setStatus('loading')
        setStreamingBlocks([])
        setStreamingText('')
        setSteerQueueId(null)
        setSteerError(null)
        reset()

        const userMessage: ChatMessage = {
            role: 'user',
            content: rawText,
            files: uploadedFiles.length > 0 ? [...uploadedFiles] : undefined,
        }
        const updatedMessages = [...messages, userMessage]
        setMessages(updatedMessages)

        const requestPayload: Record<string, unknown> = {
            messages: [...messages, {
                role: 'user',
                content: rawText,
                files: uploadedFiles.length > 0 ? [...uploadedFiles] : undefined,
                structured: structured || null,
            }],
            skill: mode,
            clientIP,
        }
        // 传递会话归属
        if (sessionContext?.sessionId) {
            requestPayload.sessionId = sessionContext.sessionId
            if (sessionContext.createConversation) {
                requestPayload.createConversation = true
            } else if (sessionContext.conversationId) {
                requestPayload.conversationId = sessionContext.conversationId
            }
        }

        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestPayload),
                signal: abortRef.current.signal,
            })

            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({ error: '请求失败' }))
                throw new Error(errData.error || `请求失败 (${resp.status})`)
            }

            setStatus('streaming')
            const reader = resp.body!.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            let isDone = false
            const collectedBlocks: ChatBlock[] = []
            let collectedText = ''

            while (!isDone) {
                const { done, value } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (!line.trim()) continue
                    try {
                        const chunk = JSON.parse(line)
                        addChunk(chunk)
                        if (chunk.type === 'text') collectedText += chunk.content || ''
                        collectedBlocks.push({
                            type: chunk.type, content: chunk.content || '',
                            toolCallId: chunk.toolCallId, toolName: chunk.toolName,
                            toolArgs: chunk.toolArgs, toolResult: chunk.toolResult,
                            isValid: chunk.isValid, resourceName: chunk.resourceName,
                            resourceUri: chunk.resourceUri, serverId: chunk.serverId,
                            isTruncated: chunk.isTruncated, previewChars: chunk.previewChars,
                            actionType: chunk.actionType, title: chunk.title,
                            stepIndex: chunk.stepIndex, status: chunk.status,
                            summary: chunk.summary, durationMs: chunk.durationMs,
                            runId: chunk.runId,
                            steerId: chunk.steerId, steerText: chunk.steerText,
                            appliedAtStep: chunk.appliedAtStep, queueSize: chunk.queueSize,
                            reason: chunk.reason,
                        })
                        if (chunk.type === 'done') isDone = true
                    } catch { /* skip */ }
                    if (isDone) break
                }
                if (isDone) break
            }

            flushAndClear()

            const assistantMessage: ChatMessage = { role: 'assistant', content: collectedText, blocks: collectedBlocks }
            setMessages(prev => trimMsgs([...prev, assistantMessage]))
            setStreamingBlocks([])
            setStreamingText('')
            setSteerQueueId(null)
            setStatus('idle')
        } catch (err: any) {
            flushAndClear()
            if (err.name === 'AbortError') { setStatus('idle'); return }
            setError(err.message || '未知错误')
            setStatus('error')
            setMessages(prev => prev.slice(0, -1))
        } finally {
            abortRef.current = null
        }
    }, [messages, status, addChunk, trimMsgs, enqueue, flush, flushAndClear, reset])

    const cancelStream = useCallback(() => {
        if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
        flushAndClear()
        setSteerQueueId(null)
        setStatus('idle')
    }, [flushAndClear])

    const regenerate = useCallback(() => {
        if (status === 'loading' || status === 'streaming') return
        let idx = -1
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'user') { idx = i; break }
        }
        if (idx === -1) { setError('没有找到可重新生成的消息'); return }
        const userMsg = messages[idx]
        const remaining = messages.slice(0, idx)
        setMessages(remaining)
        return { text: userMsg.content, files: userMsg.files || [] }
    }, [messages, status])

    const retry = useCallback(() => {
        const lastMsg = messages[messages.length - 1]
        if (lastMsg && lastMsg.role === 'user') {
            const text = lastMsg.content
            const files = lastMsg.files || []
            setMessages(prev => prev.slice(0, -1))
            setError(null)
            return { text, files }
        }
        return null
    }, [messages])

    // ─── 流式插话 (steer) ──────────────────────────────
    const sendSteer = useCallback(async (text: string): Promise<boolean> => {
        if (!steerQueueId || !isStreaming) return false
        if (!text.trim()) return false
        setSteerError(null)
        try {
            const resp = await fetch('/api/chat/steer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steerQueueId, steerText: text.trim() }),
            })
            const data = await resp.json()
            if (!resp.ok || !data.ok) {
                setSteerError(data.error || 'steer 失败')
                return false
            }
            return true
        } catch (e: any) {
            setSteerError(e.message || 'steer 网络错误')
            return false
        }
    }, [steerQueueId, isStreaming])

    return {
        messages, status, error, isStreaming,
        streamingBlocks, streamingText,
        sendMessage, cancelStream, regenerate, retry,
        hydrate, clearToDraft, setStatus,
        steerQueueId, steerError, sendSteer,
    }
}
