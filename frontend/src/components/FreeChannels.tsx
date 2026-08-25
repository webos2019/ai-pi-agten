import React, { useState, useEffect, useCallback } from 'react'

// ─── 渠道数据（纯数据，不可能崩溃） ─────────────────────

interface Channel {
    id: string
    name: string
    apiBase: string
    model: string
    freeType: 'unlimited' | 'daily' | 'credits' | 'rate-limited'
    freeQuota: string
    openaiCompatible: boolean
    chinese: 'excellent' | 'good' | 'fair' | 'poor'
    registerUrl: string
    inProject: boolean
    hasKey: boolean
    whitelist: boolean
    blacklist: boolean
    notes: string
}

const ALL_CHANNELS: Channel[] = [
    {
        id: 'zhipu-flash', name: '智谱 GLM-4-Flash',
        apiBase: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash',
        freeType: 'unlimited', freeQuota: '完全免费，无限次',
        openaiCompatible: true, chinese: 'excellent',
        registerUrl: 'https://open.bigmodel.cn', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '中文质量最好，首推备用渠道',
    },
    {
        id: 'groq', name: 'Groq (Llama 3.3 70B)',
        apiBase: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile',
        freeType: 'rate-limited', freeQuota: '免费，30 RPM',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://console.groq.com', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '速度极快，英文强',
    },
    {
        id: 'siliconflow', name: '硅基流动 (DeepSeek-V3)',
        apiBase: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3',
        freeType: 'credits', freeQuota: '注册送14元',
        openaiCompatible: true, chinese: 'excellent',
        registerUrl: 'https://siliconflow.cn', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '可调 DeepSeek-V3 开源版',
    },
    {
        id: 'gemini-flash', name: 'Google Gemini 1.5 Flash',
        apiBase: 'https://generativelanguage.googleapis.com/v1beta/openai', model: 'gemini-1.5-flash',
        freeType: 'daily', freeQuota: '1500次/天',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://ai.google.dev', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '多模态，上下文100万token',
    },
    {
        id: 'deepseek', name: 'DeepSeek',
        apiBase: 'https://api.deepseek.com', model: 'deepseek-chat',
        freeType: 'credits', freeQuota: '注册送10元',
        openaiCompatible: true, chinese: 'excellent',
        registerUrl: 'https://platform.deepseek.com', inProject: true,
        hasKey: true, whitelist: true, blacklist: false,
        notes: '当前主渠道',
    },
    {
        id: 'cerebras', name: 'Cerebras (Llama 3.1 70B)',
        apiBase: 'https://api.cerebras.ai/v1', model: 'llama3.1-70b',
        freeType: 'rate-limited', freeQuota: '免费，有限速',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://cerebras.ai', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '推理速度全网最快',
    },
    {
        id: 'nvidia-nim', name: 'NVIDIA NIM (Llama 405B)',
        apiBase: 'https://integrate.api.nvidia.com/v1', model: 'meta/llama-3.1-405b-instruct',
        freeType: 'daily', freeQuota: '每模型1000次/天',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://build.nvidia.com', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '可调405B巨兽',
    },
    {
        id: 'cloudflare-ai', name: 'Cloudflare Workers AI',
        apiBase: 'https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1', model: '@cf/meta/llama-3.1-8b-instruct',
        freeType: 'daily', freeQuota: '10000次/天',
        openaiCompatible: true, chinese: 'poor',
        registerUrl: 'https://dash.cloudflare.com', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '额度最大，模型偏小',
    },
    {
        id: 'moonshot', name: 'Moonshot Kimi',
        apiBase: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k',
        freeType: 'credits', freeQuota: '新用户送15元',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://platform.moonshot.cn', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '中文长文本能力强',
    },
    {
        id: 'xf-spark', name: '讯飞星火',
        apiBase: 'https://spark-api-open.xf-yun.com/v1', model: 'generalv3.5',
        freeType: 'credits', freeQuota: '新用户送500万token',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://xinghuo.xfyun.cn', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '国内稳定，OpenAI兼容',
    },
    {
        id: 'tencent-hunyuan', name: '腾讯混元',
        apiBase: 'https://api.hunyuan.cloud.tencent.com/v1', model: 'hunyuan-turbo',
        freeType: 'credits', freeQuota: '新用户免费额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://cloud.tencent.com', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '大厂出品，中文好',
    },
    {
        id: 'dashscope', name: '通义千问 Turbo',
        apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-turbo',
        freeType: 'credits', freeQuota: '新用户免费额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://dashscope.aliyun.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '阿里出品，中文好',
    },
    {
        id: 'doubao', name: '豆包 Doubao',
        apiBase: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-pro-32k',
        freeType: 'credits', freeQuota: '新用户免费额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://volcengine.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '字节出品，中文好',
    },
    {
        id: 'baichuan', name: '百川 Baichuan',
        apiBase: 'https://api.baichuan-ai.com/v1', model: 'Baichuan4',
        freeType: 'credits', freeQuota: '新用户送额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://platform.baichuan-ai.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '中文好，额度偏少',
    },
    {
        id: 'minimax', name: 'MiniMax',
        apiBase: 'https://api.minimax.chat/v1', model: 'abab6.5s-chat',
        freeType: 'credits', freeQuota: '新用户送额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://platform.minimaxi.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '中文好，额度偏少',
    },
    {
        id: 'yi', name: '零一万物 Yi',
        apiBase: 'https://api.lingyiwanwu.com/v1', model: 'yi-large',
        freeType: 'credits', freeQuota: '新用户送额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://platform.lingyiwanwu.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '李开复创办',
    },
    {
        id: 'stepfun', name: '阶跃星辰 Step',
        apiBase: 'https://api.stepfun.com/v1', model: 'step-1-8k',
        freeType: 'credits', freeQuota: '新用户送额度',
        openaiCompatible: true, chinese: 'good',
        registerUrl: 'https://platform.stepfun.com', inProject: true,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '中文好，额度偏少',
    },
    {
        id: 'mistral', name: 'Mistral Small',
        apiBase: 'https://api.mistral.ai/v1', model: 'mistral-small-latest',
        freeType: 'credits', freeQuota: '有免费额度',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://console.mistral.ai', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '欧洲团队，英文强',
    },
    {
        id: 'together-ai', name: 'Together AI',
        apiBase: 'https://api.together.xyz/v1', model: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
        freeType: 'credits', freeQuota: '新用户送$5',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://api.together.ai', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '可调多种开源模型',
    },
    {
        id: 'perplexity', name: 'Perplexity',
        apiBase: 'https://api.perplexity.ai/v1', model: 'llama-3.1-sonar-small-128k-online',
        freeType: 'credits', freeQuota: '新用户送$5',
        openaiCompatible: true, chinese: 'fair',
        registerUrl: 'https://perplexity.ai', inProject: false,
        hasKey: false, whitelist: true, blacklist: false,
        notes: '在线搜索增强',
    },
    // ── 黑名单 ──
    {
        id: 'pollinations-text', name: 'Pollinations Text',
        apiBase: 'https://text.pollinations.ai/openai', model: 'openai',
        freeType: 'unlimited', freeQuota: '无限',
        openaiCompatible: true, chinese: 'poor',
        registerUrl: '', inProject: false,
        hasKey: false, whitelist: false, blacklist: true,
        notes: '无需Key但稳定性极差',
    },
    {
        id: 'duckduckgo', name: 'DuckDuckGo AI Chat',
        apiBase: 'https://duckduckgo.com/duckchat/v1/chat', model: 'gpt-4o-mini',
        freeType: 'unlimited', freeQuota: '无限',
        openaiCompatible: false, chinese: 'fair',
        registerUrl: '', inProject: false,
        hasKey: false, whitelist: false, blacklist: true,
        notes: '非标准OpenAI格式',
    },
    {
        id: 'huggingface', name: 'HuggingFace Inference',
        apiBase: 'https://api-inference.huggingface.co/models/', model: '各种开源模型',
        freeType: 'unlimited', freeQuota: '免费，有冷启动',
        openaiCompatible: false, chinese: 'fair',
        registerUrl: 'https://huggingface.co', inProject: false,
        hasKey: false, whitelist: false, blacklist: true,
        notes: '冷启动延迟30秒',
    },
    {
        id: 'openrouter-free', name: 'OpenRouter 免费层',
        apiBase: 'https://openrouter.ai/api/v1', model: 'meta-llama/llama-3.1-8b-instruct:free',
        freeType: 'rate-limited', freeQuota: '免费模型，20RPM',
        openaiCompatible: true, chinese: 'poor',
        registerUrl: 'https://openrouter.ai', inProject: false,
        hasKey: false, whitelist: false, blacklist: true,
        notes: '速率限制过严，免费模型质量差',
    },
    {
        id: 'baidu-wenxin', name: '百度文心一言',
        apiBase: 'https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/', model: 'ernie-speed-128k',
        freeType: 'rate-limited', freeQuota: '有QPS限制',
        openaiCompatible: false, chinese: 'good',
        registerUrl: 'https://cloud.baidu.com', inProject: false,
        hasKey: false, whitelist: false, blacklist: true,
        notes: '非OpenAI兼容格式',
    },
]

const freeTypeLabel: Record<string, string> = {
    unlimited: '完全免费', daily: '每日免费', credits: '送额度', 'rate-limited': '有限速',
}
const freeTypeColor: Record<string, string> = {
    unlimited: '#16a34a', daily: '#2563eb', credits: '#d97706', 'rate-limited': '#7c3aed',
}
const chineseLabel: Record<string, string> = {
    excellent: '五星', good: '四星', fair: '三星', poor: '两星',
}

// ─── 组件 ──────────────────────────────────────────────

interface FreeChannelsProps {
    open: boolean
    onClose: () => void
}

const FreeChannels: React.FC<FreeChannelsProps> = ({ open, onClose }) => {
    const [filter, setFilter] = useState<string>('whitelist')
    const [llmConfig, setLlmConfig] = useState<{ model: string; provider: string; hasKey: boolean } | null>(null)
    const [presets, setPresets] = useState<{ id: string; label: string; apiBase: string; model: string }[]>([])
    const [testResults, setTestResults] = useState<Record<string, any>>({})
    const [testKeyInput, setTestKeyInput] = useState<Record<string, string>>({})

    useEffect(() => {
        if (!open) return
        fetch('/api/settings/llm')
            .then(r => r.json())
            .then(data => {
                if (data.config) {
                    setLlmConfig({
                        model: data.config.model || '',
                        provider: data.config.provider || '',
                        hasKey: data.config.hasKey || false,
                    })
                }
                if (data.presets) {
                    setPresets(data.presets)
                }
            })
            .catch(() => {})
    }, [])

    const isInPresets = useCallback((ch: Channel) => {
        return presets.some(p => p.apiBase === ch.apiBase || p.model === ch.model)
    }, [presets])

    const isCurrent = useCallback((ch: Channel) => {
        if (!llmConfig) return false
        return llmConfig.provider === ch.apiBase && llmConfig.model === ch.model
    }, [llmConfig])

    const copyConfig = (ch: Channel) => {
        const config = 'API Base: ' + ch.apiBase + '\nModel: ' + ch.model + '\n注册地址: ' + ch.registerUrl
        navigator.clipboard.writeText(config).then(() => {
            alert('已复制 ' + ch.name + ' 的配置信息')
        }).catch(() => {
            alert('已复制 ' + ch.name + ' 的配置信息')
        })
    }

    const switchToChannel = (ch: Channel) => {
        if (!ch.openaiCompatible) { alert(ch.name + ' 不兼容 OpenAI 格式'); return }
        if (ch.blacklist) { alert(ch.name + ' 在黑名单中'); return }
        const confirmed = confirm('切换到 ' + ch.name + ' ?')
        if (!confirmed) return
        fetch('/api/settings/llm', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: ch.apiBase, model: ch.model, apiKey: '***' }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.ok) {
                    alert('已切换到 ' + ch.name)
                    return fetch('/api/settings/llm').then(r => r.json())
                }
                throw new Error(data.error || '切换失败')
            })
            .then(data => {
                if (data.config) {
                    setLlmConfig({
                        model: data.config.model || '',
                        provider: data.config.provider || '',
                        hasKey: data.config.hasKey || false,
                    })
                }
            })
            .catch(e => alert('切换失败: ' + e.message))
    }

    const runTest = useCallback(async (ch: Channel) => {
        const apiKey = testKeyInput[ch.id] || ''
        setTestResults(prev => ({ ...prev, [ch.id]: { status: 'testing' } }))
        try {
            const resp = await fetch('/api/free-channels/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ apiBase: ch.apiBase, model: ch.model, apiKey: apiKey || undefined }),
            })
            const data = await resp.json()
            if (data.ok) {
                setTestResults(prev => ({ ...prev, [ch.id]: { status: 'ok', latencyMs: data.latencyMs, response: data.response } }))
            } else {
                setTestResults(prev => ({ ...prev, [ch.id]: { status: 'fail', latencyMs: data.latencyMs, error: data.error } }))
            }
        } catch (e) {
            setTestResults(prev => ({ ...prev, [ch.id]: { status: 'fail', error: (e as Error).message } }))
        }
    }, [testKeyInput])

    // 过滤
    const filtered = ALL_CHANNELS.filter(ch => {
        if (filter === 'whitelist') return ch.whitelist
        if (filter === 'blacklist') return ch.blacklist
        if (filter === 'in-project') return ch.inProject || isInPresets(ch)
        return true
    })

    const sortOrder: Record<string, number> = { unlimited: 0, daily: 1, 'rate-limited': 2, credits: 3 }
    const sorted = [...filtered].sort((a, b) => sortOrder[a.freeType] - sortOrder[b.freeType])

    const whitelistCount = ALL_CHANNELS.filter(c => c.whitelist).length
    const blacklistCount = ALL_CHANNELS.filter(c => c.blacklist).length
    const inProjectCount = ALL_CHANNELS.filter(c => c.inProject || isInPresets(c)).length

    if (!open) return null

    return (
        <div className="fc-overlay" onClick={onClose}>
            <div className="fc-panel" onClick={e => e.stopPropagation()}>
                <div className="fc-panel-header">
                    <h2 className="fc-title">免费 AI API 渠道总表</h2>
                    <button className="fc-close-btn" onClick={onClose} title="关闭">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <path d="M18 6L6 18M6 6l12 12" />
                        </svg>
                    </button>
                </div>
                <div className="fc-panel-body">
                    <div className="fc-subtitle-row">
                        当前主渠道: <strong>{llmConfig ? llmConfig.model : '加载中...'}</strong>
                        {llmConfig && !llmConfig.hasKey && <span className="fc-warn"> (Key 未配置)</span>}
                    </div>

            <div className="fc-stats">
                <div className="fc-stat-card whitelist" onClick={() => setFilter('whitelist')}>
                    <span className="fc-stat-num">{whitelistCount}</span>
                    <span className="fc-stat-label">白名单</span>
                </div>
                <div className="fc-stat-card in-project" onClick={() => setFilter('in-project')}>
                    <span className="fc-stat-num">{inProjectCount}</span>
                    <span className="fc-stat-label">已接入</span>
                </div>
                <div className="fc-stat-card blacklist" onClick={() => setFilter('blacklist')}>
                    <span className="fc-stat-num">{blacklistCount}</span>
                    <span className="fc-stat-label">黑名单</span>
                </div>
                <div className="fc-stat-card all" onClick={() => setFilter('all')}>
                    <span className="fc-stat-num">{ALL_CHANNELS.length}</span>
                    <span className="fc-stat-label">全部</span>
                </div>
            </div>

            <div className="fc-filter-bar">
                <button className={'fc-filter-btn ' + (filter === 'whitelist' ? 'active' : '')} onClick={() => setFilter('whitelist')}>白名单 ({whitelistCount})</button>
                <button className={'fc-filter-btn ' + (filter === 'in-project' ? 'active' : '')} onClick={() => setFilter('in-project')}>已接入 ({inProjectCount})</button>
                <button className={'fc-filter-btn ' + (filter === 'blacklist' ? 'active' : '')} onClick={() => setFilter('blacklist')}>黑名单 ({blacklistCount})</button>
                <button className={'fc-filter-btn ' + (filter === 'all' ? 'active' : '')} onClick={() => setFilter('all')}>全部 ({ALL_CHANNELS.length})</button>
            </div>

            <div className="fc-channel-list">
                {sorted.map(ch => {
                    const current = isCurrent(ch)
                    const inProject = ch.inProject || isInPresets(ch)
                    const tr = testResults[ch.id]
                    return (
                        <div key={ch.id} className={'fc-channel-card ' + (current ? 'current' : '') + ' ' + (ch.blacklist ? 'blacklisted' : '')}>
                            <div className="fc-card-left">
                                {current ? (
                                    <span className="fc-badge current">当前</span>
                                ) : inProject ? (
                                    <span className="fc-badge in-project">已接入</span>
                                ) : ch.whitelist ? (
                                    <span className="fc-badge whitelist">白名单</span>
                                ) : (
                                    <span className="fc-badge blacklist">黑名单</span>
                                )}
                            </div>

                            <div className="fc-card-middle">
                                <div className="fc-card-header">
                                    <span className="fc-channel-name">{ch.name}</span>
                                    <span className="fc-free-type" style={{ background: freeTypeColor[ch.freeType] }}>
                                        {freeTypeLabel[ch.freeType]}
                                    </span>
                                    <span className={'fc-compat ' + (ch.openaiCompatible ? 'ok' : 'no')}>
                                        {ch.openaiCompatible ? 'OpenAI兼容' : '非兼容'}
                                    </span>
                                </div>
                                <div className="fc-card-body">
                                    <div className="fc-info-row">
                                        <span className="fc-info-label">免费额度:</span>
                                        <span className="fc-info-value">{ch.freeQuota}</span>
                                    </div>
                                    <div className="fc-info-row">
                                        <span className="fc-info-label">中文质量:</span>
                                        <span className="fc-info-value">{chineseLabel[ch.chinese]}</span>
                                    </div>
                                    <div className="fc-info-row">
                                        <span className="fc-info-label">API Base:</span>
                                        <code className="fc-info-value fc-code">{ch.apiBase}</code>
                                    </div>
                                    <div className="fc-info-row">
                                        <span className="fc-info-label">模型:</span>
                                        <code className="fc-info-value fc-code">{ch.model}</code>
                                    </div>
                                    <div className="fc-info-row">
                                        <span className="fc-info-label">说明:</span>
                                        <span className="fc-info-value fc-notes">{ch.notes}</span>
                                    </div>
                                </div>
                            </div>

                            <div className="fc-card-right">
                                {ch.registerUrl && (
                                    <a href={ch.registerUrl} target="_blank" rel="noopener noreferrer" className="fc-action-btn register">
                                        {ch.hasKey ? '已有Key' : '去注册'}
                                    </a>
                                )}
                                <button className="fc-action-btn copy" onClick={() => copyConfig(ch)}>复制</button>
                                {ch.whitelist && ch.openaiCompatible && !current && (
                                    <button className="fc-action-btn switch" onClick={() => switchToChannel(ch)}>切换</button>
                                )}
                                {current && <span className="fc-current-label">当前使用中</span>}
                            </div>

                            {ch.whitelist && ch.openaiCompatible && (
                                <div className="fc-sandbox">
                                    <div className="fc-sandbox-header">
                                        <span className="fc-sandbox-title">沙盒测试</span>
                                        {tr && tr.status === 'ok' && (
                                            <span className="fc-test-result ok">成功 {tr.latencyMs}ms - {tr.response ? tr.response.slice(0, 50) : ''}</span>
                                        )}
                                        {tr && tr.status === 'fail' && (
                                            <span className="fc-test-result fail">失败 {tr.error ? String(tr.error).slice(0, 80) : ''}</span>
                                        )}
                                        {tr && tr.status === 'testing' && (
                                            <span className="fc-test-result testing">测试中...</span>
                                        )}
                                    </div>
                                    <div className="fc-sandbox-body">
                                        <input
                                            type="password"
                                            className="fc-sandbox-key"
                                            placeholder={ch.hasKey ? 'API Key (已配置)' : 'API Key (可选)'}
                                            value={testKeyInput[ch.id] || ''}
                                            onChange={e => setTestKeyInput(prev => ({ ...prev, [ch.id]: e.target.value }))}
                                        />
                                        <button
                                            className={'fc-sandbox-btn ' + (tr ? tr.status : '')}
                                            onClick={() => runTest(ch)}
                                            disabled={tr && tr.status === 'testing'}
                                        >
                                            {tr && tr.status === 'testing' ? '测试中...' : '发送测试'}
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>

            <div className="fc-footer">
                <h3 className="fc-footer-title">名单判定标准</h3>
                <div className="fc-footer-grid">
                    <div className="fc-footer-section">
                        <h4>白名单标准</h4>
                        <ul>
                            <li>OpenAI 兼容格式</li>
                            <li>免费额度明确且可靠</li>
                            <li>稳定性好</li>
                            <li>无冷启动延迟</li>
                            <li>速率限制合理</li>
                        </ul>
                    </div>
                    <div className="fc-footer-section">
                        <h4>黑名单标准</h4>
                        <ul>
                            <li>非 OpenAI 兼容格式</li>
                            <li>稳定性差</li>
                            <li>冷启动延迟 30 秒以上</li>
                            <li>速率限制过严</li>
                            <li>数据安全风险</li>
                        </ul>
                    </div>
                </div>
            </div>
            </div>
                </div>
        </div>
    )
}

export default FreeChannels
