import React, { useEffect, useRef } from 'react'
import type { ChatMessage, ChatBlock } from '../types'
import { useChatAutoScroll } from '../hooks/useChatAutoScroll'

interface Props {
    messages: ChatMessage[]
    isStreaming: boolean
    streamingText: string
    streamingBlocks: ChatBlock[]
    error: string | null
    mode: string
}

// ─── Markdown render ─────────────────────────────────
function renderMarkdown(content: string): string {
    if (!content) return ''
    if (typeof (window as any).marked !== 'undefined') {
        ;(window as any).marked.setOptions({ breaks: true, gfm: true })
        let html = (window as any).marked.parse(content)
        const div = document.createElement('div')
        div.innerHTML = html
        div.querySelectorAll('pre code').forEach((block: any) => {
            if (typeof (window as any).hljs !== 'undefined') try { (window as any).hljs.highlightElement(block) } catch { /* skip */ }
        })
        div.querySelectorAll('pre').forEach((pre: any) => {
            const code = pre.querySelector('code')
            if (!code) return
            const wrapper = document.createElement('div')
            wrapper.className = 'code-block-wrapper'
            const header = document.createElement('div')
            header.className = 'code-block-header'
            const lang = code.className.match(/language-(\w+)/)
            header.innerHTML = '<span>' + (lang ? lang[1] : 'text') + '</span>'
            const copyBtn = document.createElement('button')
            copyBtn.className = 'code-block-copy'
            copyBtn.textContent = '📋 复制'
            copyBtn.onclick = () => { navigator.clipboard.writeText(code.textContent).then(() => { copyBtn.textContent = '✓ 已复制'; setTimeout(() => { copyBtn.textContent = '📋 复制' }, 2000) }) }
            header.appendChild(copyBtn)
            pre.parentNode.insertBefore(wrapper, pre)
            wrapper.appendChild(header)
            wrapper.appendChild(pre)
        })
        return div.innerHTML
    }
    return escapeHtml(content)
}

function escapeHtml(text: string): string {
    if (!text) return ''
    const div = document.createElement('div')
    div.textContent = String(text)
    return div.innerHTML
}

// ─── Structured block render ─────────────────────────
function renderBlock(block: ChatBlock, key: number): React.ReactNode {
    switch (block.type) {
        case 'reasoning':
            return (
                <div key={key} className="my-2 overflow-hidden rounded-xl border border-amber-200 bg-amber-50/80">
                    <details className="group" open>
                        <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs font-medium text-amber-700">⚡ 推理过程 <span className="ml-auto text-amber-500">点击展开/收起</span></summary>
                        <div className="border-t border-amber-200/50 px-3 py-2 text-xs leading-relaxed text-amber-800" dangerouslySetInnerHTML={{ __html: renderMarkdown(block.content || '') }} />
                    </details>
                </div>
            )
        case 'tool_call': {
            const argsStr = block.toolArgs ? Object.entries(block.toolArgs).map(([k, v]) => k + '=' + v).join(', ') : ''
            return (
                <div key={key} className="tool-call-block">
                    <div className="tool-call-header flex items-center justify-between">
                        <span className="tool-call-name">🔧 {escapeHtml(block.toolName || '')}</span>
                        <span className="tool-result-badge success">执行中</span>
                    </div>
                    {argsStr && (
                        <div className="tool-call-args">
                            <div className="tool-call-args-label">输入</div>
                            <div className="tool-call-args-value">{escapeHtml(argsStr)}</div>
                        </div>
                    )}
                </div>
            )
        }
        case 'tool_result': {
            let displayResult = block.toolResult || ''
            let isError = false
            let weatherData: any = null
            try {
                const parsed = JSON.parse(displayResult)
                if (parsed.error) { displayResult = parsed.error; isError = true }
                else if (parsed.city && parsed.temperature !== undefined) weatherData = parsed
            } catch { /* not JSON */ }

            if (weatherData) {
                const rows: React.ReactNode[] = []
                if (weatherData.city) rows.push(<div key="city" className="weather-row"><span className="weather-label">城市</span><span className="weather-value">{weatherData.city}</span></div>)
                if (weatherData.weather) rows.push(<div key="w" className="weather-row"><span className="weather-label">天气</span><span className="weather-value">{weatherData.weather}</span></div>)
                if (weatherData.temperature != null) rows.push(<div key="t" className="weather-row"><span className="weather-label">温度</span><span className="weather-value">{weatherData.temperature}°C</span></div>)
                if (weatherData.humidity) rows.push(<div key="h" className="weather-row"><span className="weather-label">湿度</span><span className="weather-value">{weatherData.humidity}%</span></div>)
                return (
                    <div key={key} className="tool-result-block">
                        <div className="tool-result-header flex items-center justify-between"><span className="tool-result-label">天气结果</span></div>
                        <div className="weather-result">{rows}</div>
                    </div>
                )
            }

            const success = !isError && block.isValid !== false
            return (
                <div key={key} className="tool-result-block" style={isError ? { borderColor: '#fecaca', background: 'rgba(254,242,242,0.5)' } : success ? { borderColor: '#bbf7d0', background: 'rgba(240,253,244,0.5)' } : {}}>
                    <div className="tool-result-header flex items-center justify-between">
                        <span className="tool-result-label" style={isError ? { color: '#ef4444' } : success ? { color: '#22c55e' } : {}}>结果</span>
                        <span className={`tool-result-badge ${isError ? 'error' : 'success'}`}>{isError ? '✗ 失败' : '✓ 成功'}</span>
                    </div>
                    <div className="tool-result-content" style={isError ? { color: '#dc2626' } : {}}>
                        <pre>{escapeHtml(displayResult)}</pre>
                    </div>
                </div>
            )
        }
        case 'resource_start':
            return (
                <div key={key} className="resource-block">
                    <div className="resource-header flex items-center justify-between">
                        <span className="resource-name">📖 {escapeHtml(block.resourceName || '')}</span>
                        <span className="tool-result-badge success">读取中</span>
                    </div>
                </div>
            )
        case 'resource_end':
            return (
                <div key={key} className="resource-block" style={{ borderColor: '#c7d2fe', background: '#fff' }}>
                    <div className="resource-header" style={{ borderColor: '#e0e7ff' }}>
                        <span style={{ color: '#6366f1' }}>资源内容</span>
                        <span className="tool-result-badge success">✓ 已读取</span>
                    </div>
                    <div className="resource-content">
                        <pre>{escapeHtml(block.content || '')}</pre>
                        {block.isTruncated && <div className="mt-2 text-[10px] text-gray-500">内容已截断</div>}
                    </div>
                </div>
            )
        case 'resource_error':
            return (
                <div key={key} className="tool-result-block" style={{ borderColor: '#fecaca', background: 'rgba(254,242,242,0.5)' }}>
                    <div className="tool-result-header" style={{ borderColor: '#fecaca' }}>
                        <span className="tool-result-label" style={{ color: '#ef4444' }}>读取失败</span>
                        <span className="tool-result-badge error">✗ 错误</span>
                    </div>
                    <div className="tool-result-content" style={{ color: '#dc2626' }}>
                        <pre>{escapeHtml(block.content || '')}</pre>
                    </div>
                </div>
            )
        case 'agent_step': {
            const stepIcon = block.status === 'success' ? '✓' : block.status === 'error' ? '✗' : block.status === 'pending' ? '⟳' : '•'
            const stepColor = block.status === 'success' ? 'var(--green-ok)' : block.status === 'error' ? 'var(--red-err)' : 'var(--cyan)'
            return (
                <div key={key} className="agent-step-block">
                    <div className="agent-step-header flex items-center gap-2">
                        <span className="agent-step-icon" style={{ color: stepColor }}>{stepIcon}</span>
                        <span className="agent-step-title">{block.title || block.actionType || 'Agent 步骤'}</span>
                        {block.durationMs != null && (
                            <span className="agent-step-duration">{block.durationMs}ms</span>
                        )}
                        {block.status === 'pending' && (
                            <span className="agent-step-badge pending">执行中</span>
                        )}
                        {block.status === 'success' && (
                            <span className="agent-step-badge success">完成</span>
                        )}
                        {block.status === 'error' && (
                            <span className="agent-step-badge error">失败</span>
                        )}
                    </div>
                    {block.summary && (
                        <div className="agent-step-summary">{block.summary}</div>
                    )}
                </div>
            )
        }
        case 'text':
            return <div key={key} className="text-block" dangerouslySetInnerHTML={{ __html: renderMarkdown(block.content || '') }} />
        case 'steer_queued':
            return (
                <div key={key} className="steer-block steer-queued-block">
                    <div className="steer-header flex items-center gap-2">
                        <span className="steer-icon">🔄</span>
                        <span className="steer-label">转向指令已排队</span>
                        {block.queueSize != null && (
                            <span className="steer-badge">队列 {block.queueSize}</span>
                        )}
                    </div>
                    <div className="steer-text">{escapeHtml(block.steerText || '')}</div>
                </div>
            )
        case 'steer_applied':
            return (
                <div key={key} className="steer-block steer-applied-block">
                    <div className="steer-header flex items-center gap-2">
                        <span className="steer-icon">✅</span>
                        <span className="steer-label">转向指令已应用</span>
                        {block.appliedAtStep != null && (
                            <span className="steer-badge">步骤 {block.appliedAtStep}</span>
                        )}
                        {block.actionType && (
                            <span className="steer-action-type">{block.actionType}</span>
                        )}
                    </div>
                    <div className="steer-text">{escapeHtml(block.steerText || '')}</div>
                </div>
            )
        case 'steer_rejected':
            return (
                <div key={key} className="steer-block steer-rejected-block">
                    <div className="steer-header flex items-center gap-2">
                        <span className="steer-icon">⚠️</span>
                        <span className="steer-label">转向指令被拒绝</span>
                    </div>
                    <div className="steer-text">{escapeHtml(block.steerText || '')}</div>
                    {block.reason && (
                        <div className="steer-reason">{escapeHtml(block.reason)}</div>
                    )}
                </div>
            )
        default:
            return null
    }
}

// ─── Message render ───────────────────────────────────
function renderMessage(msg: ChatMessage, isStreaming: boolean, sText: string | undefined, sBlocks: ChatBlock[] | undefined): React.ReactNode {
    const isUser = msg.role === 'user'
    if (msg.role === 'system') return null

    const allBlocks = isStreaming ? (sBlocks || []) : (msg.blocks || [])
    const textContent = isStreaming && sText !== undefined ? sText : msg.content
    const avatarClass = isUser ? 'user' : 'ai'
    const avatarText = isUser ? 'U' : 'π'

    let content: React.ReactNode
    if (isUser) {
        let filesHtml: React.ReactNode = null
        if (msg.files && msg.files.length > 0) {
            filesHtml = (
                <div className="mb-2 flex flex-wrap gap-1.5">
                    {msg.files.map((f, i) => (
                        <span key={i} className="inline-flex items-center gap-1 rounded-md bg-white/20 px-2 py-0.5 text-xs">📄 {f.name}</span>
                    ))}
                </div>
            )
        }
        content = (
            <div className="user-bubble">
                {filesHtml}
                <div>{textContent || ''}</div>
            </div>
        )
    } else {
        let inner: React.ReactNode
        if (allBlocks.length > 0) {
            inner = allBlocks.map((b, i) => renderBlock(b, i))
        } else {
            inner = <div className="text-block" dangerouslySetInnerHTML={{ __html: renderMarkdown(textContent || '') }} />
        }
        if (isStreaming) {
            inner = <>{inner}<span className="streaming-cursor" /></>
        }
        content = <div className="ai-bubble">{inner}</div>
    }

    return (
        <div key={Math.random()} className={`msg-row flex mb-6 ${isUser ? 'user justify-end' : 'assistant justify-start'}`}>
            <div className="msg-wrapper flex w-full max-w-[90%] gap-3">
                <div className={`avatar ${avatarClass}`}>{avatarText}</div>
                <div className="msg-content min-w-0 flex-1">{content}</div>
            </div>
        </div>
    )
}

// ─── ChatBody ─────────────────────────────────────────
const ChatBody: React.FC<Props> = ({ messages, isStreaming, streamingText, streamingBlocks, error, mode }) => {
    const isEmpty = messages.length === 0 && !isStreaming

    const { scrollContainerRef, showScrollToBottom, bottomSpacing, scrollToBottom } = useChatAutoScroll({
        isActive: isStreaming,
        dependency: messages.length + streamingText.length + streamingBlocks.length,
    })

    // Feature cards
    const utilityFeatures = [
        { title: '数学计算', desc: '精确计算数学表达式' },
        { title: '日期时间', desc: '获取当前时间和日期' },
        { title: '单位换算', desc: '长度、重量、温度转换' },
        { title: '/ 斜杠命令', desc: '输入 / 调出命令菜单快速操作' },
    ]
    const readerFeatures = [
        { title: '目录列表', desc: '查看项目根目录文件' },
        { title: '文件读取', desc: '读取项目根目录下文本文件' },
        { title: '实时天气', desc: '查询指定城市实时天气' },
        { title: '@ 引用系统', desc: '输入 @ 引用工具、文件或上下文' },
    ]
    const features = mode === 'utility-skill' ? utilityFeatures : readerFeatures

    if (isEmpty) {
        return (
            <div className="flex-1 overflow-y-auto min-w-0" ref={scrollContainerRef}>
                <div className="mx-auto w-full max-w-2xl px-3 py-4 sm:px-4 sm:py-6">
                    <div className="empty-state flex min-h-[40vh] flex-col items-center justify-center text-center py-4 sm:py-6">
                        <div className="empty-icon-container mb-4 flex h-12 w-12 items-center justify-center rounded-xl sm:h-14 sm:w-14">
                            <span className="text-2xl font-bold sm:text-3xl" style={{ color: 'var(--cyan)', fontFamily: 'Georgia, serif' }}>π</span>
                        </div>
                        <h2 className="empty-title mb-2 text-xl font-bold sm:text-2xl">{mode === 'utility-skill' ? 'Pi Agent · 实用工具' : 'Pi Agent · 文件与天气'}</h2>
                        <p className="empty-desc mb-4 max-w-[18rem] text-xs leading-relaxed sm:mb-6 sm:max-w-md sm:text-sm">
                            {mode === 'utility-skill'
                                ? '输入 / 查看命令菜单，@ 引用工具。支持数学计算、日期查询、单位换算等工具调用。'
                                : '输入 / 引用工具，@ 引用上下文。支持读取本地文件、查询实时天气、获取地理位置。'}
                        </p>

                        {/* Pi Agent 入口提示卡片 */}
                        <div className="pi-agent-entry-card mb-4 w-full max-w-md">
                            <div className="pi-agent-entry-header flex items-center gap-2">
                                <span className="text-base">📋</span>
                                <span className="pi-agent-entry-title">Pi Agent 受控任务清单生成</span>
                            </div>
                            <div className="pi-agent-entry-desc">
                                输入 <code className="pi-code-inline">/tasklist</code> 并 <code className="pi-code-inline">@</code> 引用一个版本方案，触发受控 Agent 链路：
                                <span className="pi-agent-flow">read → extract → readiness → draft → validate → revise → eval → final</span>
                            </div>
                        </div>

                        <div className="mx-auto grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                            {features.map((f, i) => (
                                <div key={i} className="feature-card">
                                    <div className="flex items-center gap-2">
                                        <div className={`feature-icon-container flex h-6 w-6 shrink-0 items-center justify-center rounded ${mode === 'reader-skill' ? 'feature-icon-purple' : ''}`}>
                                            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                                        </div>
                                        <h3 className="feature-title">{f.title}</h3>
                                    </div>
                                    <p className="feature-desc mt-1">{f.desc}</p>
                                </div>
                            ))}
                        </div>
                        <div className="info-box mt-4 flex items-center gap-2 rounded-lg px-4 py-2 sm:mt-6 sm:px-5 sm:py-2.5">
                            <svg className="info-icon h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                            支持流式错误自动恢复 + 三层短期记忆，始终保障服务可用
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    let lastAssistantIndex = -1
    for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') { lastAssistantIndex = i; break }
    }

    return (
        <>
        <div className="flex-1 overflow-y-auto min-w-0" ref={scrollContainerRef}>
            <div className="mx-auto w-full max-w-2xl px-3 py-4 sm:px-4 sm:py-6" style={{ paddingBottom: `${bottomSpacing}px` }}>
                <div className="flex flex-col gap-4 sm:gap-6">
                    {messages.map((msg, i) => {
                        const isLastAssistant = i === lastAssistantIndex && msg.role === 'assistant'
                        const showStreaming = isStreaming && isLastAssistant
                        return renderMessage(msg, showStreaming, showStreaming ? streamingText : undefined, showStreaming ? streamingBlocks : undefined)
                    })}
                    {isStreaming && lastAssistantIndex < 0 && renderMessage({ role: 'assistant', content: '' }, true, streamingText, streamingBlocks)}
                    {error && (
                        <div className="error-container">
                            <div className="error-content flex items-start gap-3">
                                <svg className="error-icon mt-0.5 h-4 w-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                                <div className="error-text flex-1">
                                    <p className="error-title font-medium mb-1">出错了</p>
                                    <p className="error-msg">{error}</p>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
        {showScrollToBottom && (
            <button className="scroll-to-bottom-btn" onClick={scrollToBottom}>
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ width: '1rem', height: '1rem' }}><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" /></svg>
                <span>回到底部</span>
            </button>
        )}
        </>
    )
}

export default ChatBody
