import React, { useState, useCallback, useRef, useEffect } from 'react'

// ─── Types ────────────────────────────────────────────

interface GeneratedImage {
    url: string
    type: string
}

interface GenerateResult {
    prompt: string
    images: GeneratedImage[]
    model: string
    provider?: string
}

interface ImageProvider {
    id: string
    label: string
    model: string
    available: boolean
    requiresKey: boolean
    desc: string
}

// ─── 预设提示词 ───────────────────────────────────────

const PRESET_PROMPTS = [
    '一只可爱的橘猫坐在窗台上，阳光洒落，水彩画风格',
    '赛博朋克城市夜景，霓虹灯，雨后倒影，超高清',
    '中国山水画，云雾缭绕，远山近水，水墨风格',
    '一朵盛开的牡丹花，微距摄影，露珠，自然光',
    '宇航员在火星表面漫步，地球在远方，科幻风格',
]

const IMAGE_SIZES = [
    { label: '1024 x 1024', value: '1024x1024' },
    { label: '768 x 768', value: '768x768' },
    { label: '1024 x 1536', value: '1024x1536' },
]

// ─── Component ─────────────────────────────────────────

const TextToImage: React.FC = () => {
    const [prompt, setPrompt] = useState('')
    const [loading, setLoading] = useState(false)
    const [images, setImages] = useState<GeneratedImage[]>([])
    const [error, setError] = useState<string | null>(null)
    const [size, setSize] = useState('1024x1024')
    const [history, setHistory] = useState<Array<{ prompt: string; url: string }>>([])
    const [provider, setProvider] = useState('auto')
    const [providers, setProviders] = useState<ImageProvider[]>([])
    const [defaultProvider, setDefaultProvider] = useState('pollinations')
    const previewRef = useRef<HTMLDivElement>(null)

    // 加载提供商状态
    useEffect(() => {
        fetch('/api/text2image/status')
            .then(r => r.json())
            .then(data => {
                if (data.providers) setProviders(data.providers)
                if (data.default) {
                    setDefaultProvider(data.default)
                    setProvider('auto')
                }
            })
            .catch(() => {})
    }, [])

    const generate = useCallback(async () => {
        const trimmed = prompt.trim()
        if (!trimmed || loading) return

        setLoading(true)
        setError(null)
        setImages([])

        try {
            const resp = await fetch('/api/text2image', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: trimmed,
                    size: size,
                    n: 1,
                    provider: provider,
                }),
            })

            const data = await resp.json()

            if (!resp.ok) {
                // 如果商汤失败且有建议切换，自动提示
                if (data.suggestion === 'pollinations') {
                    setError(`${data.error}（已检测到 Pollinations 免费提供商可用，可在下方切换）`)
                } else {
                    setError(data.error || '生图失败')
                }
                return
            }

            setImages(data.images || [])

            // 添加到历史
            if (data.images && data.images.length > 0) {
                setHistory(prev => [
                    { prompt: trimmed, url: data.images[0].url },
                    ...prev.slice(0, 11),
                ])
            }
        } catch (e) {
            setError(`请求失败: ${e instanceof Error ? e.message : String(e)}`)
        } finally {
            setLoading(false)
        }
    }, [prompt, loading, size, provider])

    const handlePresetClick = useCallback((text: string) => {
        setPrompt(text)
    }, [])

    const handleDownload = useCallback((url: string, index: number) => {
        const a = document.createElement('a')
        a.href = url
        a.download = `pi-agent-image-${Date.now()}-${index}.png`
        a.target = '_blank'
        a.click()
    }, [])

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            generate()
        }
    }, [generate])

    // 当前选择的提供商显示名
    const currentProviderLabel = provider === 'auto'
        ? (defaultProvider === 'sensetime' ? '商汤 (自动)' : 'Pollinations (自动)')
        : providers.find(p => p.id === provider)?.label || provider

    return (
        <div className="text2image-container">
            {/* ── 左侧：输入区 ── */}
            <div className="text2image-left">
                {/* 提供商选择 */}
                <div className="text2image-input-section">
                    <label className="text2image-label">生图引擎</label>
                    <div className="text2image-provider-selector">
                        <button
                            className={`provider-btn ${provider === 'auto' ? 'active' : ''}`}
                            onClick={() => setProvider('auto')}
                            disabled={loading}
                        >
                            <span className="provider-btn-name">🔄 自动选择</span>
                            <span className="provider-btn-desc">
                                {defaultProvider === 'sensetime' ? '商汤 SenseMirage' : 'Pollinations (免费)'}
                            </span>
                        </button>
                        {providers.map(p => (
                            <button
                                key={p.id}
                                className={`provider-btn ${provider === p.id ? 'active' : ''} ${!p.available ? 'unavailable' : ''}`}
                                onClick={() => p.available && setProvider(p.id)}
                                disabled={loading || !p.available}
                                title={p.desc}
                            >
                                <span className="provider-btn-name">
                                    {p.id === 'pollinations' ? '🎁' : '🎨'} {p.label}
                                    {!p.available && <span className="provider-unavailable-tag">未配置</span>}
                                </span>
                                <span className="provider-btn-desc">{p.model}</span>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="text2image-input-section">
                    <label className="text2image-label">提示词</label>
                    <textarea
                        className="text2image-textarea"
                        placeholder="描述你想生成的图片，例如：一只可爱的橘猫坐在窗台上..."
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={6}
                        disabled={loading}
                    />
                    <div className="text2image-hint">Ctrl+Enter 快速生成</div>
                </div>

                <div className="text2image-input-section">
                    <label className="text2image-label">图片尺寸</label>
                    <div className="text2image-size-selector">
                        {IMAGE_SIZES.map(s => (
                            <button
                                key={s.value}
                                className={`size-btn ${size === s.value ? 'active' : ''}`}
                                onClick={() => setSize(s.value)}
                                disabled={loading}
                            >{s.label}</button>
                        ))}
                    </div>
                </div>

                <div className="text2image-input-section">
                    <label className="text2image-label">预设提示词</label>
                    <div className="text2image-presets">
                        {PRESET_PROMPTS.map((text, i) => (
                            <button
                                key={i}
                                className="preset-chip"
                                onClick={() => handlePresetClick(text)}
                                disabled={loading}
                            >{text.slice(0, 24)}...</button>
                        ))}
                    </div>
                </div>

                <button
                    className="text2image-generate-btn"
                    onClick={generate}
                    disabled={!prompt.trim() || loading}
                >
                    {loading ? (
                        <>
                            <span className="loading-spinner" />
                            生成中... ({currentProviderLabel})
                        </>
                    ) : (
                        <>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
                            </svg>
                            生成图片
                        </>
                    )}
                </button>

                {error && (
                    <div className="text2image-error">
                        <span>{error}</span>
                        <button onClick={() => setError(null)}>x</button>
                    </div>
                )}
            </div>

            {/* ── 右侧：结果展示区 ── */}
            <div className="text2image-right" ref={previewRef}>
                {loading && (
                    <div className="text2image-loading">
                        <div className="loading-spinner-large" />
                        <p>正在调用 {currentProviderLabel} 生图引擎...</p>
                        <p className="text2image-loading-sub">可能需要 10-60 秒，请耐心等待</p>
                    </div>
                )}

                {!loading && images.length > 0 && (
                    <div className="text2image-result">
                        <div className="text2image-result-grid">
                            {images.map((img, i) => (
                                <div key={i} className="text2image-result-item">
                                    <img
                                        src={img.url}
                                        alt={`生成图片 ${i + 1}`}
                                        onClick={() => window.open(img.url, '_blank')}
                                    />
                                    <div className="text2image-result-actions">
                                        <button onClick={() => handleDownload(img.url, i)}>
                                            下载
                                        </button>
                                        <button onClick={() => window.open(img.url, '_blank')}>
                                            新窗口打开
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="text2image-result-prompt">
                            <span className="text2image-result-prompt-label">提示词:</span>
                            <span>{prompt}</span>
                        </div>
                    </div>
                )}

                {!loading && images.length === 0 && !error && (
                    <div className="text2image-empty">
                        <div className="text2image-empty-icon">
                            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                                <circle cx="8.5" cy="8.5" r="1.5"/>
                                <polyline points="21 15 16 10 5 21"/>
                            </svg>
                        </div>
                        <p>输入提示词，开始创作</p>
                        <p className="text2image-empty-sub">
                            {defaultProvider === 'sensetime'
                                ? '由商汤 SenseMirage 提供生图能力'
                                : '由 Pollinations Flux 提供生图能力（免费）'}
                        </p>
                    </div>
                )}

                {/* 历史记录 */}
                {!loading && history.length > 0 && (
                    <div className="text2image-history">
                        <div className="text2image-history-title">历史生成</div>
                        <div className="text2image-history-grid">
                            {history.map((item, i) => (
                                <div
                                    key={i}
                                    className="text2image-history-item"
                                    onClick={() => { setPrompt(item.prompt); setImages([{ url: item.url, type: 'url' }]) }}
                                    title={item.prompt}
                                >
                                    <img src={item.url} alt={item.prompt} />
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

export default TextToImage
