import React, { useState, useEffect, useCallback } from 'react'

interface LLMConfig {
    provider: string
    model: string
    apiKeyMasked: string
    hasKey: boolean
}

interface LLMPreset {
    id: string
    label: string
    apiBase: string
    model: string
}

interface Props {
    open: boolean
    onClose: () => void
    sessionId?: string
    workspaceName?: string
}

const SettingsPanel: React.FC<Props> = ({ open, onClose, sessionId, workspaceName }) => {
    const [config, setConfig] = useState<LLMConfig | null>(null)
    const [presets, setPresets] = useState<LLMPreset[]>([])
    const [provider, setProvider] = useState('')
    const [model, setModel] = useState('')
    const [apiKey, setApiKey] = useState('')
    const [loading, setLoading] = useState(false)
    const [saved, setSaved] = useState(false)
    const [error, setError] = useState<string | null>(null)

    // 加载配置
    const loadConfig = useCallback(async () => {
        try {
            const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
            const resp = await fetch(`/api/settings/llm${query}`)
            const data = await resp.json()
            setConfig(data.config)
            setPresets(data.presets || [])
            setProvider(data.config.provider)
            setModel(data.config.model)
            setApiKey('')
        } catch {
            setError('加载配置失败')
        }
    }, [sessionId])

    useEffect(() => {
        if (open) {
            loadConfig()
            setError(null)
            setSaved(false)
        }
    }, [open, loadConfig])

    // 选择预设
    const handlePresetSelect = (preset: LLMPreset) => {
        setProvider(preset.apiBase)
        setModel(preset.model)
    }

    // 保存配置
    const handleSave = async () => {
        setLoading(true)
        setError(null)
        setSaved(false)
        try {
            const resp = await fetch('/api/settings/llm', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider,
                    model,
                    apiKey: apiKey || '***',
                    sessionId: sessionId || '',
                }),
            })
            const data = await resp.json()
            if (!resp.ok) {
                setError(data.error || '保存失败')
            } else {
                setConfig(data.config)
                setApiKey('')
                setSaved(true)
                setTimeout(() => setSaved(false), 2000)
            }
        } catch {
            setError('网络错误，保存失败')
        } finally {
            setLoading(false)
        }
    }

    if (!open) return null

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div className="settings-panel" onClick={e => e.stopPropagation()}>
                {/* 头部 */}
                <div className="settings-header">
                    <h2 className="settings-title">
                        LLM 设置
                        {workspaceName && (
                            <span className="settings-workspace-tag">{workspaceName}</span>
                        )}
                    </h2>
                    <button className="settings-close" onClick={onClose} title="关闭">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <path d="M18 6L6 18M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* 当前状态 */}
                {config && (
                    <div className="settings-status-bar">
                        <span className={`settings-status-dot ${config.hasKey ? 'ok' : 'warn'}`} />
                        <span className="settings-status-text">
                            {config.hasKey ? `当前: ${config.model}` : '未配置 API Key'}
                        </span>
                        {config.hasKey && (
                            <span className="settings-status-key">{config.apiKeyMasked}</span>
                        )}
                    </div>
                )}

                {/* 预设提供商 */}
                <div className="settings-section">
                    <label className="settings-label">LLM 提供商</label>
                    <div className="preset-grid">
                        {presets.map(preset => {
                            const isActive = provider === preset.apiBase
                            return (
                                <button
                                    key={preset.id}
                                    className={`preset-card ${isActive ? 'active' : ''}`}
                                    onClick={() => handlePresetSelect(preset)}
                                    title={preset.apiBase}
                                >
                                    <span className="preset-label">{preset.label}</span>
                                    {preset.model && (
                                        <span className="preset-model">{preset.model}</span>
                                    )}
                                </button>
                            )
                        })}
                    </div>
                </div>

                {/* API Base */}
                <div className="settings-section">
                    <label className="settings-label" htmlFor="llm-provider">API Base URL</label>
                    <input
                        id="llm-provider"
                        type="text"
                        className="settings-input"
                        value={provider}
                        onChange={e => setProvider(e.target.value)}
                        placeholder="https://api.deepseek.com"
                    />
                </div>

                {/* 模型 */}
                <div className="settings-section">
                    <label className="settings-label" htmlFor="llm-model">模型名称</label>
                    <input
                        id="llm-model"
                        type="text"
                        className="settings-input"
                        value={model}
                        onChange={e => setModel(e.target.value)}
                        placeholder="deepseek-chat"
                    />
                </div>

                {/* API Key */}
                <div className="settings-section">
                    <label className="settings-label" htmlFor="llm-key">API Key</label>
                    <input
                        id="llm-key"
                        type="password"
                        className="settings-input"
                        value={apiKey}
                        onChange={e => setApiKey(e.target.value)}
                        placeholder={config?.hasKey ? `已配置 (${config.apiKeyMasked})，留空不修改` : '输入 API Key'}
                    />
                </div>

                {/* 错误/成功提示 */}
                {error && (
                    <div className="settings-alert error">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" />
                        </svg>
                        {error}
                    </div>
                )}
                {saved && (
                    <div className="settings-alert success">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M20 6L9 17l-5-5" />
                        </svg>
                        配置已保存，下次对话生效
                    </div>
                )}

                {/* 操作按钮 */}
                <div className="settings-actions">
                    <button className="settings-btn secondary" onClick={onClose}>
                        取消
                    </button>
                    <button
                        className="settings-btn primary"
                        onClick={handleSave}
                        disabled={loading || (!provider && !model)}
                    >
                        {loading ? '保存中...' : '保存配置'}
                    </button>
                </div>
            </div>
        </div>
    )
}

export default SettingsPanel
