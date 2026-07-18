import { useRef, useEffect, useState, useCallback } from 'react'

interface UseChatAutoScrollOptions {
    /** 是否正在流式输出 */
    isActive: boolean
    /** 消息变化信号（messages.length 或 streamingText.length 等） */
    dependency: unknown
}

interface UseChatAutoScrollReturn {
    /** 绑定到滚动容器的 ref */
    scrollContainerRef: React.RefObject<HTMLDivElement>
    /** 是否显示"回到底部"按钮 */
    showScrollToBottom: boolean
    /** 底部留白（px） */
    bottomSpacing: number
    /** 新一轮对话时重置自动跟随 */
    resetAutoScrollForNewTurn: () => void
    /** 点击"回到底部"：恢复自动跟随并滚动到底部 */
    scrollToBottom: () => void
}

const BOTTOM_THRESHOLD = 80 // px — 距底部多少以内算"在底部"
const IDLE_BOTTOM_SPACING = 20
const ACTIVE_BOTTOM_SPACING = 100

/**
 * AI 对话自动滚动 Hook
 *
 * 功能:
 * - 流式输出时自动跟随到底部
 * - 用户向上滚动时锁定，不强制拉走
 * - 不在底部时显示"回到底部"按钮
 * - requestAnimationFrame 节流，避免滚动事件触发过于频繁
 * - 底部留白，让最后一条消息不完全贴底
 */
export function useChatAutoScroll(options: UseChatAutoScrollOptions): UseChatAutoScrollReturn {
    const { isActive, dependency } = options
    const scrollContainerRef = useRef<HTMLDivElement>(null)
    const [showScrollToBottom, setShowScrollToBottom] = useState(false)
    const [bottomSpacing, setBottomSpacing] = useState(IDLE_BOTTOM_SPACING)

    // 用户是否主动向上滚动了（锁定自动跟随）
    const isLockedRef = useRef(false)
    // rAF 节流标记
    const rafRef = useRef<number | null>(null)
    // 上一轮的 scrollHeight，用于检测内容增长
    const prevScrollHeightRef = useRef(0)

    // ─── 滚动到底部 ─────────────────────────────────
    const scrollToBottomInternal = useCallback(() => {
        const el = scrollContainerRef.current
        if (!el) return
        el.scrollTop = el.scrollHeight
    }, [])

    // ─── 滚动事件检测 ─────────────────────────────────
    useEffect(() => {
        const el = scrollContainerRef.current
        if (!el) return

        const handleScroll = () => {
            if (rafRef.current !== null) return // 已有待执行帧
            rafRef.current = requestAnimationFrame(() => {
                rafRef.current = null
                const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
                const isAtBottom = distFromBottom < BOTTOM_THRESHOLD

                if (isAtBottom) {
                    isLockedRef.current = false
                    setShowScrollToBottom(false)
                } else {
                    // 用户向上滚动 → 锁定
                    if (!isActive) {
                        // 非流式时，只要不在底部就显示按钮
                        setShowScrollToBottom(true)
                    } else if (el.scrollTop < prevScrollHeightRef.current - el.clientHeight * 0.3) {
                        // 流式时，只有明显向上滚动才锁定（避免内容增长导致的微小上移）
                        isLockedRef.current = true
                        setShowScrollToBottom(true)
                    }
                }
                prevScrollHeightRef.current = el.scrollHeight
            })
        }

        el.addEventListener('scroll', handleScroll, { passive: true })
        return () => {
            el.removeEventListener('scroll', handleScroll)
            if (rafRef.current !== null) {
                cancelAnimationFrame(rafRef.current)
                rafRef.current = null
            }
        }
    }, [isActive])

    // ─── 内容变化时自动跟随 ─────────────────────────
    useEffect(() => {
        if (isLockedRef.current) return // 用户锁定了，不强制滚动
        if (rafRef.current !== null) return
        rafRef.current = requestAnimationFrame(() => {
            rafRef.current = null
            scrollToBottomInternal()
        })
    }, [dependency, scrollToBottomInternal])

    // ─── 流式状态切换时调整留白 ─────────────────────
    useEffect(() => {
        setBottomSpacing(isActive ? ACTIVE_BOTTOM_SPACING : IDLE_BOTTOM_SPACING)
        if (!isActive) {
            // 流式结束后，如果在底部就隐藏按钮
            const el = scrollContainerRef.current
            if (el) {
                const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
                if (distFromBottom < BOTTOM_THRESHOLD) {
                    setShowScrollToBottom(false)
                }
            }
        }
    }, [isActive])

    // ─── 暴露的动作 ─────────────────────────────────
    const resetAutoScrollForNewTurn = useCallback(() => {
        isLockedRef.current = false
        setShowScrollToBottom(false)
        // 下一帧滚动到底部
        requestAnimationFrame(() => scrollToBottomInternal())
    }, [scrollToBottomInternal])

    const scrollToBottom = useCallback(() => {
        isLockedRef.current = false
        setShowScrollToBottom(false)
        scrollToBottomInternal()
    }, [scrollToBottomInternal])

    return {
        scrollContainerRef,
        showScrollToBottom,
        bottomSpacing,
        resetAutoScrollForNewTurn,
        scrollToBottom,
    }
}
