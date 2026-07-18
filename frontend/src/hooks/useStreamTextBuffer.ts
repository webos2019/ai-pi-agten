import { useRef, useCallback, useEffect } from 'react'

const FLUSH_INTERVAL_MS = 50 // 50ms 定时 flush

interface PendingDelta {
    messageId: string
    partType: 'text' | 'reasoning'
    delta: string
}

/**
 * 流式文本增量缓冲 Hook
 *
 * 问题: 服务端每来一个 text-delta 就 setState，会导致 Markdown 渲染和 DOM 更新过于频繁。
 *
 * 方案: 把高频 text/reasoning delta 先攒到 ref 里，定时 flush 到 React state。
 * - enqueue(): 接收 delta，攒到 buffer
 * - flush(): 把 buffer 里的内容一次性写出去
 * - 内部用 setTimeout 定时 flush，间隔 50ms
 *
 * 使用方式:
 *   const buffer = useStreamTextBuffer()
 *   // 收到 text chunk 时:
 *   buffer.enqueue(messageId, 'text', delta)
 *   // buffer 内部会定时调用 onFlush 把合并后的文本写出去
 */
export function useStreamTextBuffer(onFlush: (partType: 'text' | 'reasoning', accumulated: string) => void) {
    const pendingRef = useRef<Map<string, PendingDelta>>(new Map())
    const onFlushRef = useRef(onFlush)
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const textAccumulatedRef = useRef('')
    const reasoningAccumulatedRef = useRef('')

    // 保持最新的 onFlush 回调
    useEffect(() => {
        onFlushRef.current = onFlush
    }, [onFlush])

    // ─── flush: 把 buffer 里的内容写出去 ────────────
    const flush = useCallback(() => {
        const hasText = textAccumulatedRef.current
        const hasReasoning = reasoningAccumulatedRef.current

        if (hasText) {
            onFlushRef.current('text', textAccumulatedRef.current)
            textAccumulatedRef.current = ''
        }
        if (hasReasoning) {
            onFlushRef.current('reasoning', reasoningAccumulatedRef.current)
            reasoningAccumulatedRef.current = ''
        }
    }, [])

    // ─── enqueue: 接收 delta，攒到 buffer ───────────
    const enqueue = useCallback((partType: 'text' | 'reasoning', delta: string) => {
        if (!delta) return

        if (partType === 'text') {
            textAccumulatedRef.current += delta
        } else {
            reasoningAccumulatedRef.current += delta
        }

        // 如果定时器还没启动，启动一个
        if (timerRef.current === null) {
            timerRef.current = setTimeout(() => {
                timerRef.current = null
                flush()
            }, FLUSH_INTERVAL_MS)
        }
    }, [flush])

    // ─── 立即 flush 并清理（用于流结束或组件卸载时） ──
    const flushAndClear = useCallback(() => {
        if (timerRef.current !== null) {
            clearTimeout(timerRef.current)
            timerRef.current = null
        }
        flush()
    }, [flush])

    // ─── 重置（新一轮对话开始时） ──────────────────
    const reset = useCallback(() => {
        textAccumulatedRef.current = ''
        reasoningAccumulatedRef.current = ''
        pendingRef.current.clear()
        if (timerRef.current !== null) {
            clearTimeout(timerRef.current)
            timerRef.current = null
        }
    }, [])

    // ─── 组件卸载时清理 ────────────────────────────
    useEffect(() => {
        return () => {
            if (timerRef.current !== null) {
                clearTimeout(timerRef.current)
                timerRef.current = null
            }
        }
    }, [])

    return { enqueue, flush, flushAndClear, reset }
}
