import React, { useState, useCallback, useRef, useEffect } from 'react'

// ─── Types ────────────────────────────────────────────

interface ChatMsg {
    role: 'user' | 'assistant'
    content: string
    timestamp: string
    audioUrl?: string
    loading?: boolean
    imageUrl?: string
    imageLoading?: boolean
}

interface VoiceOption {
    name: string
    locale: string
    gender: string
    friendly_name: string
}

// ─── 常量 ─────────────────────────────────────────────

const POPULAR_VOICES = [
    { name: 'zh-CN-XiaoxiaoNeural', label: '晓晓 (女·温柔)', flag: '🇨🇳' },
    { name: 'zh-CN-YunxiNeural', label: '云希 (男·成熟)', flag: '🇨🇳' },
    { name: 'zh-CN-YunyangNeural', label: '云扬 (男·新闻)', flag: '🇨🇳' },
    { name: 'zh-CN-XiaoyiNeural', label: '晓伊 (女·活泼)', flag: '🇨🇳' },
    { name: 'zh-HK-HiuMaanNeural', label: '曉曼 (女·粵語)', flag: '🇭🇰' },
    { name: 'zh-TW-HsiaoChenNeural', label: '曉臻 (女·台灣)', flag: '🇹🇼' },
    { name: 'en-US-JennyNeural', label: 'Jenny (Female)', flag: '🇺🇸' },
    { name: 'en-US-GuyNeural', label: 'Guy (Male)', flag: '🇺🇸' },
]

// ─── Component ─────────────────────────────────────────

const VoiceChat: React.FC = () => {
    const [messages, setMessages] = useState<ChatMsg[]>([])
    const [listening, setListening] = useState(false)
    const [speaking, setSpeaking] = useState(false)
    const [thinking, setThinking] = useState(false)
    const [voice, setVoice] = useState('zh-CN-XiaoxiaoNeural')
    const [rate, setRate] = useState(0)  // 纯数字 -50 ~ 100
    const [interimText, setInterimText] = useState('')
    const [autoSpeak, setAutoSpeak] = useState(true)
    const [voices, setVoices] = useState<VoiceOption[]>([])
    const [tokenUsage, setTokenUsage] = useState({ prompt: 0, completion: 0, total: 0 })
    const [ttsError, setTtsError] = useState<string | null>(null)
    const lastSpokenTextRef = useRef<string>('')

    // ── 持久化会话管理 (修复: 每条消息创建新 session 导致无上下文) ──
    const [voiceSessionId] = useState<string>(() => {
        let sid = localStorage.getItem('pi_voice_session_id')
        if (!sid) {
            sid = 'voice_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
            localStorage.setItem('pi_voice_session_id', sid)
        }
        return sid
    })
    const [voiceConversationId, setVoiceConversationId] = useState<string | null>(
        () => localStorage.getItem('pi_voice_conversation_id') || null,
    )

    const recognitionRef = useRef<any>(null)
    const audioRef = useRef<HTMLAudioElement | null>(null)
    const messagesEndRef = useRef<HTMLDivElement | null>(null)
    const sendMsgRef = useRef<(text: string) => void>(() => {})
    const abortRef = useRef<AbortController | null>(null)  // 中止流式请求

    // ─── 挂载时加载已有对话历史 ───
    useEffect(() => {
        if (!voiceConversationId) return
        fetch(`/api/conversations/${voiceConversationId}?session_id=${voiceSessionId}`)
            .then(r => {
                if (!r.ok) throw new Error('not found')
                return r.json()
            })
            .then(data => {
                if (data.messages && data.messages.length > 0) {
                    const loaded: ChatMsg[] = data.messages.map((m: any) => ({
                        role: m.role === 'assistant' ? 'assistant' : 'user',
                        content: m.text || '',
                        timestamp: new Date((m.createdAt || Date.now() / 1000) * 1000)
                            .toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
                    }))
                    setMessages(loaded)
                }
            })
            .catch(() => {
                // conversation 不存在（可能 DuckDB 降级时创建的脏 ID）→ 清除
                setVoiceConversationId(null)
                localStorage.removeItem('pi_voice_conversation_id')
            })
    }, []) // 只在挂载时执行一次

    // ─── 初始化语音识别 ───
    useEffect(() => {
        const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
        if (SR) {
            const rec = new SR()
            rec.lang = 'zh-CN'
            rec.continuous = false
            rec.interimResults = true

            rec.onresult = (e: any) => {
                let interim = ''
                let final = ''
                for (let i = e.resultIndex; i < e.results.length; i++) {
                    const transcript = e.results[i][0].transcript
                    if (e.results[i].isFinal) {
                        final += transcript
                    } else {
                        interim += transcript
                    }
                }
                if (interim) setInterimText(interim)
                if (final) {
                    setInterimText('')
                    sendMsgRef.current(final.trim())
                }
            }

            rec.onerror = (e: any) => {
                console.error('Speech recognition error:', e.error)
                setListening(false)
            }

            rec.onend = () => {
                setListening(false)
            }

            recognitionRef.current = rec
        }

        return () => {
            try { recognitionRef.current?.abort() } catch {}
        }
    }, [])

    // ─── 加载语音列表 ───
    useEffect(() => {
        fetch('/api/tts/voices')
            .then(r => r.json())
            .then(data => {
                if (data.voices) setVoices(data.voices)
            })
            .catch(() => {})
    }, [])

    // ─── 清除可能过期的 conversationId ───
    // DuckDB 降级期间创建的 conversation 在恢复后可能找不到
    // 上面的 hydration 请求失败时会自动清除，这里无需额外处理

    // ─── 自动滚动 ───
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, interimText, thinking])

    // ─── 画图意图检测 ───
    const isImageIntent = useCallback((text: string): boolean => {
        const lower = text.toLowerCase()
        const patterns = [
            '画', '画画', '画一', '画个', '画张', '画只',
            '生成图片', '生成一张', '生成一幅',
            '帮我画', '帮我生成',
            'draw', 'paint', 'generate image', 'create image',
        ]
        return patterns.some(p => lower.includes(p))
    }, [])

    // ─── 提取画图提示词 ───
    const extractImagePrompt = useCallback((text: string): string => {
        // 去掉前缀指令词，保留描述部分
        let prompt = text
            .replace(/^(帮我|请|麻烦)?(画一|画个|画张|画只|画幅|画|生成一张|生成一幅|生成图片|生成|帮我画|帮我生成)/i, '')
            .replace(/^(draw|paint|generate image|create image|generate|create)/i, '')
            .trim()
        // 如果去掉后为空，用原文
        if (!prompt) prompt = text
        return prompt
    }, [])

    // ─── 生成图片 ───
    const generateImage = useCallback(async (prompt: string) => {
        try {
            const resp = await fetch('/api/text2image', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt,
                    size: '1024x1024',
                    n: 1,
                    provider: 'auto',
                }),
            })
            const data = await resp.json()
            if (!resp.ok) {
                throw new Error(data.error || '生图失败')
            }
            if (data.images && data.images.length > 0) {
                return data.images[0].url as string
            }
            throw new Error('未返回图片')
        } catch (e) {
            throw new Error((e as Error).message || '图片生成失败')
        }
    }, [])

    // ─── 发送消息到 LLM ───
    const sendMsg = useCallback(async (text: string) => {
        if (!text.trim()) return

        const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
        const userMsg: ChatMsg = { role: 'user', content: text, timestamp: now }
        setMessages(prev => [...prev, userMsg])
        setThinking(true)

        // ── 画图意图检测 ──
        if (isImageIntent(text)) {
            const imagePrompt = extractImagePrompt(text)
            const assistantTs = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: `正在为您生成图片：${imagePrompt}`,
                timestamp: assistantTs,
                imageLoading: true,
            }])

            try {
                const imageUrl = await generateImage(imagePrompt)
                setMessages(prev => prev.map((m, i) =>
                    i === prev.length - 1
                        ? { ...m, content: `图片已生成！提示词：${imagePrompt}`, imageUrl, imageLoading: false }
                        : m
                ))
                setThinking(false)
                // 语音播报
                if (autoSpeak) {
                    synthesize(`图片已生成！提示词：${imagePrompt}`)
                }
            } catch (e) {
                setMessages(prev => prev.map((m, i) =>
                    i === prev.length - 1
                        ? { ...m, content: `❌ 图片生成失败: ${(e as Error).message}`, imageLoading: false }
                        : m
                ))
                setThinking(false)
            }
            return
        }

        // 添加 assistant 占位
        const assistantTs = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
        setMessages(prev => [...prev, { role: 'assistant', content: '', timestamp: assistantTs, loading: true }])

        try {
            // ── 确保有 conversation (首次消息时创建) ──
            let convId = voiceConversationId
            if (!convId) {
                try {
                    const convResp = await fetch('/api/conversations', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            sessionId: voiceSessionId,
                            title: text.slice(0, 40) || '语音对话',
                        }),
                    })
                    const convData = await convResp.json()
                    if (convData.conversationId) {
                        convId = convData.conversationId as string
                        setVoiceConversationId(convId)
                        localStorage.setItem('pi_voice_conversation_id', convId)
                    }
                } catch { /* 降级: 无 conversation 也能聊天 */ }
            }

            // ⚠️ 不传 skill → 后端 resolve_skill 自动路由到合适的技能
            // (知识性问题 → search-skill, 运维 → oa-ops-skill, 默认 → utility-skill)
            const requestBody: Record<string, unknown> = {
                messages: [{ role: 'user', content: text }],
                sessionId: voiceSessionId,
            }
            if (convId) {
                requestBody.conversationId = convId
            }

            // 创建 AbortController 以支持中途停止
            const controller = new AbortController()
            abortRef.current = controller

            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
                signal: controller.signal,
            })

            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({ error: '请求失败' }))
                throw new Error(errData.error || `HTTP ${resp.status}`)
            }

            const reader = resp.body?.getReader()
            if (!reader) return

            const decoder = new TextDecoder()
            let buffer = ''
            let fullText = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    if (!line.trim()) continue
                    try {
                        const data = JSON.parse(line)
                        // ⚠️ 后端 chunk type 是 'text'，不是 'token'
                        if (data.type === 'text' && data.content) {
                            fullText += data.content
                            setMessages(prev => prev.map((m, i) =>
                                i === prev.length - 1
                                    ? { ...m, content: fullText, loading: false }
                                    : m
                            ))
                        } else if (data.type === 'done' || data.type === 'end') {
                            setMessages(prev => prev.map((m, i) =>
                                i === prev.length - 1
                                    ? { ...m, content: fullText || '(空回复)', loading: false }
                                    : m
                            ))
                        } else if (data.type === 'error') {
                            setMessages(prev => prev.map((m, i) =>
                                i === prev.length - 1
                                    ? { ...m, content: '❌ ' + (data.error || '未知错误'), loading: false }
                                    : m
                            ))
                        } else if (data.type === 'usage') {
                            // 累加 token 消耗
                            setTokenUsage(prev => ({
                                prompt: prev.prompt + (data.promptTokens || 0),
                                completion: prev.completion + (data.completionTokens || 0),
                                total: prev.total + (data.totalTokens || 0),
                            }))
                            // 通知全局 Header 更新
                            window.dispatchEvent(new CustomEvent('voice-token-usage', { detail: {
                                prompt: data.promptTokens || 0,
                                completion: data.completionTokens || 0,
                                total: data.totalTokens || 0,
                            }}))
                        }
                    } catch { /* skip */ }
                }
            }

            // 完成 — 清理 abortRef
            abortRef.current = null
            setThinking(false)
            if (autoSpeak && fullText) {
                synthesize(fullText)
            }
        } catch (e) {
            abortRef.current = null
            setThinking(false)
            // 用户主动中止 — 不显示错误
            if ((e as Error).name === 'AbortError') {
                setMessages(prev => prev.map((m, i) =>
                    i === prev.length - 1
                        ? { ...m, loading: false }
                        : m
                ))
            } else {
                setMessages(prev => prev.map((m, i) =>
                    i === prev.length - 1
                        ? { ...m, content: '❌ 请求失败: ' + (e as Error).message, loading: false }
                        : m
                ))
            }
        }
    }, [autoSpeak, voice, rate, voiceSessionId, voiceConversationId])

    // 保持 ref 始终指向最新的 sendMsg（避免闭包陷阱）
    // ⚠️ 必须放在 sendMsg 定义之后，否则 const TDZ 报错
    useEffect(() => { sendMsgRef.current = sendMsg }, [sendMsg])

    // ─── 语音合成 (TTS) ───
    const synthesize = useCallback(async (text: string, isRetry = false) => {
        // 去除 markdown 标记
        const clean = text.replace(/```[\s\S]*?```/g, '代码块').replace(/[#*`_~\[\]]/g, '').trim()
        if (!clean) return

        // 格式化 rate: 数字 → "+0%" / "-50%" / "+100%"
        const rateStr = `${rate >= 0 ? '+' : ''}${rate}%`

        setSpeaking(true)
        setTtsError(null)
        lastSpokenTextRef.current = clean

        try {
            const resp = await fetch('/api/tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: clean, voice, rate: rateStr }),
            })

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: '未知错误' }))
                throw new Error(err.error || `HTTP ${resp.status}`)
            }

            const blob = await resp.blob()

            // 检查 blob 是否有效
            if (blob.size === 0) {
                throw new Error('合成音频为空')
            }

            const url = URL.createObjectURL(blob)

            if (audioRef.current) {
                audioRef.current.src = url
                audioRef.current.onended = () => {
                    setSpeaking(false)
                    URL.revokeObjectURL(url)
                }
                audioRef.current.onerror = (e) => {
                    console.error('Audio playback error:', e)
                    setSpeaking(false)
                    setTtsError('音频播放失败，可能是浏览器不支持该音频格式')
                }
                audioRef.current.onloadstart = () => {
                    // 音频开始加载
                }
                audioRef.current.oncanplay = () => {
                    // 音频已就绪可以播放
                }
                audioRef.current.play().catch((playErr) => {
                    console.error('Audio play() rejected:', playErr)
                    setSpeaking(false)
                    setTtsError('音频播放被浏览器阻止，请点击页面后重试')
                })
            }
        } catch (e) {
            console.error('TTS error:', e)
            setSpeaking(false)
            setTtsError(isRetry ? `重试失败: ${(e as Error).message}` : `语音合成失败: ${(e as Error).message}`)
        }
    }, [voice, rate])

    // 重试 TTS
    const retryTTS = useCallback(() => {
        if (lastSpokenTextRef.current) {
            synthesize(lastSpokenTextRef.current, true)
        }
    }, [synthesize])

    // ─── 开始/停止语音识别 ───
    const toggleListen = useCallback(() => {
        if (listening) {
            recognitionRef.current?.stop()
            setListening(false)
        } else {
            // 停止当前播放
            if (audioRef.current) {
                audioRef.current.pause()
                setSpeaking(false)
            }
            setInterimText('')
            try {
                recognitionRef.current?.start()
                setListening(true)
            } catch (e) {
                console.error('Failed to start recognition:', e)
            }
        }
    }, [listening])

    // ─── 手动发送 ───
    const [manualText, setManualText] = useState('')
    const handleManualSend = useCallback(() => {
        if (!manualText.trim()) return
        sendMsg(manualText.trim())
        setManualText('')
    }, [manualText, sendMsg])

    // ─── 重播某条消息 ───
    const replayMsg = useCallback((msg: ChatMsg) => {
        synthesize(msg.content)
    }, [synthesize])

    // ─── 停止播放 ───
    const stopSpeaking = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause()
            setSpeaking(false)
        }
    }, [])

    // ─── 停止生成 (中止流式请求 + 停止 TTS) ───
    const stopGenerating = useCallback(() => {
        // 中止正在进行的 fetch 流式请求
        if (abortRef.current) {
            abortRef.current.abort()
            abortRef.current = null
        }
        setThinking(false)
        // 停止 TTS 播放
        if (audioRef.current) {
            audioRef.current.pause()
            setSpeaking(false)
        }
        // 清除最后一条消息的 loading 状态
        setMessages(prev => prev.map((m, i) =>
            i === prev.length - 1 ? { ...m, loading: false } : m
        ))
    }, [])

    // ─── 开始新对话 (清空当前对话状态) ───
    const startNewConversation = useCallback(() => {
        setVoiceConversationId(null)
        localStorage.removeItem('pi_voice_conversation_id')
        setMessages([])
        setTokenUsage({ prompt: 0, completion: 0, total: 0 })
        if (audioRef.current) {
            audioRef.current.pause()
            setSpeaking(false)
        }
    }, [])

    const srSupported = !!(window as any).SpeechRecognition || !!(window as any).webkitSpeechRecognition

    return (
        <div className="voicechat-page">
            {/* ─── 顶部控制栏 ─── */}
            <div className="voicechat-toolbar">
                <div className="voicechat-voice-select">
                    <label className="voicechat-label">语音角色</label>
                    <select
                        className="voicechat-select"
                        value={voice}
                        onChange={e => setVoice(e.target.value)}
                    >
                        {POPULAR_VOICES.map(v => (
                            <option key={v.name} value={v.name}>
                                {v.flag} {v.label}
                            </option>
                        ))}
                        {voices.length > 0 && <optgroup label="── 全部语音 ──">
                            {voices.filter(v => !POPULAR_VOICES.some(p => p.name === v.name)).map(v => (
                                <option key={v.name} value={v.name}>
                                    {v.locale} · {v.gender === 'Female' ? '女' : '男'} · {v.name}
                                </option>
                            ))}
                        </optgroup>}
                    </select>
                </div>

                <div className="voicechat-rate">
                    <label className="voicechat-label">语速 {rate >= 0 ? '+' : ''}{rate}%</label>
                    <input
                        type="range"
                        min={-50}
                        max={100}
                        step={10}
                        value={rate}
                        onChange={e => setRate(parseInt(e.target.value))}
                        className="voicechat-slider"
                    />
                </div>

                <label className="voicechat-autospeak">
                    <input
                        type="checkbox"
                        checked={autoSpeak}
                        onChange={e => setAutoSpeak(e.target.checked)}
                    />
                    <span>自动播报</span>
                </label>

                {messages.length > 0 && (
                    <button
                        className="voicechat-new-conv-btn"
                        onClick={startNewConversation}
                        title="开始新对话"
                    >
                        ✨ 新对话
                    </button>
                )}
            </div>

            {/* ─── 消息区域 ─── */}
            <div className="voicechat-messages">
                {messages.length === 0 && (
                    <div className="voicechat-welcome">
                        <div className="voicechat-welcome-icon">🎙️</div>
                        <h2 className="voicechat-welcome-title">语音对话</h2>
                        <p className="voicechat-welcome-sub">
                            点击下方麦克风按钮开始说话，AI 回复后自动语音播报
                        </p>
                        <div className="voicechat-welcome-hint">
                            支持语音画图：说"画一只猫"即可生成图片
                        </div>
                        <div className="voicechat-welcome-hint">
                            {!srSupported && '⚠️ 当前浏览器不支持语音识别，可手动输入文字对话'}
                        </div>
                    </div>
                )}

                {messages.map((msg, i) => (
                    <div key={i} className={`voicechat-msg ${msg.role}`}>
                        <div className="voicechat-msg-avatar">
                            {msg.role === 'user' ? '👤' : '🤖'}
                        </div>
                        <div className="voicechat-msg-bubble">
                            {msg.loading ? (
                                <span className="voicechat-typing">
                                    <span className="dot" /> <span className="dot" /> <span className="dot" />
                                </span>
                            ) : (
                                <>
                                    <div className="voicechat-msg-text">{msg.content}</div>
                                    {msg.imageLoading && (
                                        <div className="voicechat-image-loading">
                                            <span className="voicechat-image-spinner" />
                                            <span>正在生成图片，请稍候...</span>
                                        </div>
                                    )}
                                    {msg.imageUrl && !msg.imageLoading && (
                                        <div className="voicechat-image-result">
                                            <img
                                                src={msg.imageUrl}
                                                alt="生成图片"
                                                onClick={() => window.open(msg.imageUrl, '_blank')}
                                            />
                                            <div className="voicechat-image-actions">
                                                <button
                                                    className="voicechat-image-btn"
                                                    onClick={() => {
                                                        const a = document.createElement('a')
                                                        a.href = msg.imageUrl!
                                                        a.download = `voice-image-${Date.now()}.png`
                                                        a.target = '_blank'
                                                        a.click()
                                                    }}
                                                >⬇ 下载</button>
                                                <button
                                                    className="voicechat-image-btn"
                                                    onClick={() => window.open(msg.imageUrl, '_blank')}
                                                >🔍 放大</button>
                                            </div>
                                        </div>
                                    )}
                                    <div className="voicechat-msg-footer">
                                        <span className="voicechat-msg-time">{msg.timestamp}</span>
                                        {msg.role === 'assistant' && (
                                            <button
                                                className="voicechat-replay-btn"
                                                onClick={() => replayMsg(msg)}
                                                title="重新播放语音"
                                            >
                                                🔊 播放
                                            </button>
                                        )}
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                ))}

                {/* 语音识别中间结果 */}
                {interimText && (
                    <div className="voicechat-msg user">
                        <div className="voicechat-msg-avatar">👤</div>
                        <div className="voicechat-msg-bubble interim">
                            <div className="voicechat-msg-text">{interimText}...</div>
                        </div>
                    </div>
                )}

                {thinking && !interimText && messages.length > 0 && messages[messages.length - 1].loading && (
                    <div className="voicechat-thinking">AI 正在思考...</div>
                )}

                <div ref={messagesEndRef} />
            </div>

            {/* ─── 底部操作区 ─── */}
            <div className="voicechat-input-area">
                <input
                    type="text"
                    className="voicechat-text-input"
                    placeholder="或在此输入文字..."
                    value={manualText}
                    onChange={e => setManualText(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleManualSend() }}
                />

                {(speaking || thinking) ? (
                    <button
                        className="voicechat-stop-btn"
                        onClick={thinking ? stopGenerating : stopSpeaking}
                        title={thinking ? '停止生成' : '停止播放'}
                    >
                        <span className="voicechat-stop-icon">⏹</span>
                    </button>
                ) : null}

                <button
                    className={`voicechat-mic-btn ${listening ? 'listening' : ''} ${!srSupported ? 'disabled' : ''}`}
                    onClick={toggleListen}
                    disabled={!srSupported}
                    title={listening ? '点击停止' : '点击说话'}
                >
                    {listening ? (
                        <span className="voicechat-mic-pulse">
                            <span className="voicechat-mic-wave" />
                            <span className="voicechat-mic-wave delay1" />
                            <span className="voicechat-mic-wave delay2" />
                        </span>
                    ) : null}
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                        <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                    </svg>
                </button>
            </div>

            {/* ─── TTS 错误提示 ─── */}
            {ttsError && (
                <div className="voicechat-tts-error">
                    <span className="voicechat-tts-error-icon">⚠️</span>
                    <span className="voicechat-tts-error-text">{ttsError}</span>
                    <button className="voicechat-tts-retry-btn" onClick={retryTTS} title="重试语音合成">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <path d="M1 4v6h6" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                        </svg>
                        重试
                    </button>
                    <button className="voicechat-tts-dismiss-btn" onClick={() => setTtsError(null)} title="关闭">
                        ✕
                    </button>
                </div>
            )}

            <audio ref={audioRef} />
        </div>
    )
}

export default VoiceChat
