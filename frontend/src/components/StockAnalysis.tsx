import React, { useState, useCallback, useEffect, useRef } from 'react'

// ─── Types ────────────────────────────────────────────

interface FactorResult {
    name: string
    values: Record<string, number | string>
    score: number
    is_bullish: boolean
    signals: string[]
}

interface ESGDimension {
    name: string
    score: number
    max: number
    signals: string[]
}

interface ESGResult {
    name: string
    dimensions: ESGDimension[]
    e_score: number
    s_score: number
    g_score: number
    total_score: number
    max_score: number
    verdict: string
    verdict_level: string
    fundamentals: Record<string, number | string>
    stock_name: string
}

interface AnalysisReport {
    code: string
    stock_name?: string
    date: string
    close: number
    kline_count: number
    ma: {
        ma5: number | null
        ma10: number | null
        ma20: number | null
        ma60: number | null
    }
    recent: {
        '5d_pct': number
        '20d_pct': number
        '120d_high': number
        '120d_low': number
    }
    factors: FactorResult[]
    summary: {
        bullish_count: number
        total_factors: number
        total_score: number
        verdict: string
        verdict_level: string
    }
    esg?: ESGResult
    esg_error?: string
    error?: string
}

// ─── Search Result Type ───────────────────────────────

interface StockSearchResult {
    code: string
    name: string
    type: string
    market: string
    full_code: string
}

// ─── Component ─────────────────────────────────────────

const StockAnalysis: React.FC = () => {
    const [code, setCode] = useState('')
    const [loading, setLoading] = useState(false)
    const [report, setReport] = useState<AnalysisReport | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [history, setHistory] = useState<Array<{ code: string; name: string; verdict: string }>>([])

    // ── 搜索自动补全状态 ──
    const [searchKeyword, setSearchKeyword] = useState('')
    const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
    const [searchLoading, setSearchLoading] = useState(false)
    const [showDropdown, setShowDropdown] = useState(false)
    const [highlightIndex, setHighlightIndex] = useState(-1)
    const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const searchInputRef = useRef<HTMLInputElement>(null)

    // ── 搜索防抖 ──
    const doSearch = useCallback((keyword: string) => {
        if (!keyword.trim() || keyword.trim().length < 1) {
            setSearchResults([])
            setShowDropdown(false)
            return
        }
        setSearchLoading(true)
        fetch(`/api/stock-search?keyword=${encodeURIComponent(keyword.trim())}`)
            .then(r => r.json())
            .then(data => {
                if (data.results) {
                    setSearchResults(data.results)
                    setShowDropdown(true)
                    setHighlightIndex(-1)
                } else {
                    setSearchResults([])
                    setShowDropdown(false)
                }
            })
            .catch(() => {
                setSearchResults([])
                setShowDropdown(false)
            })
            .finally(() => setSearchLoading(false))
    }, [])

    const handleSearchChange = useCallback((value: string) => {
        setSearchKeyword(value)
        if (debounceTimer.current) {
            clearTimeout(debounceTimer.current)
        }
        debounceTimer.current = setTimeout(() => {
            doSearch(value)
        }, 300)
    }, [doSearch])

    // ── 选中搜索结果 ──
    const handleSelectResult = useCallback((result: StockSearchResult) => {
        setCode(result.code)
        setSearchKeyword(`${result.name} ${result.code}`)
        setShowDropdown(false)
        // 自动触发分析
        setLoading(true)
        setError(null)
        setReport(null)
        fetch('/api/stock-analysis', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: result.code, limit: 120 }),
        })
            .then(r => r.json())
            .then((data: AnalysisReport) => {
                if (data.error) {
                    setError(data.error)
                } else {
                    setReport(data)
                    setHistory(prev => {
                        const filtered = prev.filter(h => h.code !== data.code)
                        const name = data.stock_name || data.esg?.stock_name || data.code
                        const verdict = data.summary?.verdict || '?'
                        return [{ code: data.code, name, verdict }, ...filtered].slice(0, 12)
                    })
                }
            })
            .catch(e => setError(`请求失败: ${e instanceof Error ? e.message : String(e)}`))
            .finally(() => setLoading(false))
    }, [])

    // ── 键盘导航 ──
    const handleSearchKeyDown = (e: React.KeyboardEvent) => {
        if (!showDropdown || searchResults.length === 0) return

        if (e.key === 'ArrowDown') {
            e.preventDefault()
            setHighlightIndex(prev => (prev + 1) % searchResults.length)
        } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setHighlightIndex(prev => prev <= 0 ? searchResults.length - 1 : prev - 1)
        } else if (e.key === 'Enter') {
            e.preventDefault()
            if (highlightIndex >= 0 && highlightIndex < searchResults.length) {
                handleSelectResult(searchResults[highlightIndex])
            } else if (searchResults.length > 0) {
                handleSelectResult(searchResults[0])
            }
        } else if (e.key === 'Escape') {
            setShowDropdown(false)
        }
    }

    // ── 点击外部关闭下拉 ──
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setShowDropdown(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const analyze = useCallback(async (stockCode?: string) => {
        const targetCode = (stockCode || code).trim()
        if (!targetCode) return

        setLoading(true)
        setError(null)
        setReport(null)

        try {
            const resp = await fetch('/api/stock-analysis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: targetCode, limit: 120 }),
            })
            const data: AnalysisReport = await resp.json()

            if (data.error) {
                setError(data.error)
            } else {
                setReport(data)
                setHistory(prev => {
                    const filtered = prev.filter(h => h.code !== data.code)
                    const name = data.stock_name || data.esg?.stock_name || data.code
                    const verdict = data.summary?.verdict || '?'
                    return [{ code: data.code, name, verdict }, ...filtered].slice(0, 12)
                })
            }
        } catch (e) {
            setError(`请求失败: ${e instanceof Error ? e.message : String(e)}`)
        } finally {
            setLoading(false)
        }
    }, [code])

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !loading) {
            analyze()
        }
    }

    return (
        <div className="stock-page">
            {/* ─── 搜索栏（支持中文自动匹配） ─── */}
            <div className="stock-search-bar">
                <div className="stock-search-input-wrap" ref={dropdownRef}>
                    <svg className="stock-search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="11" cy="11" r="8"/>
                        <path d="m21 21-4.35-4.35"/>
                    </svg>
                    <input
                        ref={searchInputRef}
                        type="text"
                        className="stock-search-input"
                        placeholder="输入股票名称或代码，如 贵州茅台 / 000725 / maotai"
                        value={searchKeyword}
                        onChange={e => handleSearchChange(e.target.value)}
                        onKeyDown={handleSearchKeyDown}
                        onFocus={() => { if (searchResults.length > 0) setShowDropdown(true) }}
                        disabled={loading}
                        autoComplete="off"
                    />
                    {searchLoading && (
                        <span className="stock-search-loading">
                            <span className="stock-mini-spinner" />
                        </span>
                    )}
                    <button
                        className="stock-search-btn"
                        onClick={() => {
                            if (searchResults.length > 0) {
                                handleSelectResult(searchResults[highlightIndex >= 0 ? highlightIndex : 0])
                            } else if (searchKeyword.trim()) {
                                // 如果输入的是纯代码，直接分析
                                const codeMatch = searchKeyword.match(/\d{6}/)
                                if (codeMatch) {
                                    setCode(codeMatch[0])
                                    analyze(codeMatch[0])
                                }
                            }
                        }}
                        disabled={loading || (!searchKeyword.trim() && !code)}
                    >
                        {loading ? '分析中...' : '分析'}
                    </button>

                    {/* ── 自动补全下拉框 ── */}
                    {showDropdown && searchResults.length > 0 && (
                        <div className="stock-search-dropdown">
                            {searchResults.map((item, i) => (
                                <div
                                    key={item.full_code}
                                    className={`stock-search-item ${i === highlightIndex ? 'highlighted' : ''}`}
                                    onMouseEnter={() => setHighlightIndex(i)}
                                    onMouseDown={(e) => {
                                        e.preventDefault()
                                        handleSelectResult(item)
                                    }}
                                >
                                    <span className="stock-search-item-name">{item.name}</span>
                                    <span className="stock-search-item-code">{item.code}</span>
                                    <span className={`stock-search-item-type type-${item.type}`}>{item.type}</span>
                                </div>
                            ))}
                            {searchResults.length === 0 && !searchLoading && (
                                <div className="stock-search-empty">无匹配结果</div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* ─── 快捷股票 ─── */}
            <div className="stock-quick-list">
                {[
                    { code: '000725', name: '京东方A' },
                    { code: '600519', name: '贵州茅台' },
                    { code: '300750', name: '宁德时代' },
                    { code: '000977', name: '浪潮信息' },
                    { code: '300346', name: '南大光电' },
                    { code: '600602', name: '云赛智联' },
                ].map(s => (
                    <button
                        key={s.code}
                        className="stock-quick-btn"
                        onClick={() => { setCode(s.code); setSearchKeyword(`${s.name} ${s.code}`); analyze(s.code) }}
                        disabled={loading}
                    >
                        {s.name} {s.code}
                    </button>
                ))}
            </div>

            {/* ─── 历史记录 ─── */}
            {history.length > 0 && (
                <div className="stock-history">
                    <span className="stock-history-label">最近分析:</span>
                    {history.map(h => (
                        <button
                            key={h.code}
                            className="stock-history-btn"
                            onClick={() => { setCode(h.code); setSearchKeyword(`${h.name} ${h.code}`); analyze(h.code) }}
                            disabled={loading}
                            title={h.verdict}
                        >
                            {h.name} · {h.verdict}
                        </button>
                    ))}
                </div>
            )}

            {/* ─── 错误提示 ─── */}
            {error && (
                <div className="stock-error">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    <span>{error}</span>
                </div>
            )}

            {/* ─── Loading ─── */}
            {loading && (
                <div className="stock-loading">
                    <div className="stock-loading-spinner" />
                    <span>正在获取K线数据和财务指标，计算7因子 + ESG评分...</span>
                </div>
            )}

            {/* ─── 报告 ─── */}
            {report && !loading && <ReportView report={report} />}
        </div>
    )
}

// ─── Report View ───────────────────────────────────────

const ReportView: React.FC<{ report: AnalysisReport }> = ({ report }) => {
    const { ma, recent, factors, summary, esg } = report
    const stockName = report.stock_name || esg?.stock_name || ''

    const verdictClass = summary.verdict_level === 'bullish_strong' ? 'verdict-bullish-strong'
        : summary.verdict_level === 'bullish_weak' ? 'verdict-bullish-weak'
        : summary.verdict_level === 'neutral' ? 'verdict-neutral'
        : 'verdict-bearish'

    return (
        <div className="stock-report">
            {/* ─── 头部信息 ─── */}
            <div className="stock-report-header">
                <div className="stock-report-title">
                    <span className="stock-report-code">{report.code}</span>
                    {stockName && <span className="stock-report-name">{stockName}</span>}
                </div>
                <div className="stock-report-meta">
                    <span>日期: {report.date}</span>
                    <span>收盘: <strong>{report.close}</strong></span>
                    <span>K线: {report.kline_count}条</span>
                </div>
            </div>

            {/* ─── 概览卡片 ─── */}
            <div className="stock-overview-grid">
                <div className={`stock-verdict-card ${verdictClass}`}>
                    <div className="stock-verdict-label">技术面评判</div>
                    <div className="stock-verdict-value">{summary.verdict}</div>
                    <div className="stock-verdict-detail">
                        偏多 {summary.bullish_count}/{summary.total_factors} · 得分 {summary.total_score}
                    </div>
                </div>

                {esg && (
                    <div className={`stock-verdict-card ${esg.verdict_level === 'positive' ? 'verdict-bullish-strong' : esg.verdict_level === 'neutral' ? 'verdict-neutral' : 'verdict-bearish'}`}>
                        <div className="stock-verdict-label">ESG 评判</div>
                        <div className="stock-verdict-value">{esg.verdict}</div>
                        <div className="stock-verdict-detail">
                            ESG {esg.total_score}/{esg.max_score} · E{esg.e_score} S{esg.s_score} G{esg.g_score}
                        </div>
                    </div>
                )}

                {/* 近期走势 */}
                <div className="stock-mini-card">
                    <div className="stock-mini-label">近5日</div>
                    <div className={`stock-mini-value ${recent['5d_pct'] >= 0 ? 'text-up' : 'text-down'}`}>
                        {recent['5d_pct'] >= 0 ? '+' : ''}{recent['5d_pct']}%
                    </div>
                </div>
                <div className="stock-mini-card">
                    <div className="stock-mini-label">近20日</div>
                    <div className={`stock-mini-value ${recent['20d_pct'] >= 0 ? 'text-up' : 'text-down'}`}>
                        {recent['20d_pct'] >= 0 ? '+' : ''}{recent['20d_pct']}%
                    </div>
                </div>
                <div className="stock-mini-card">
                    <div className="stock-mini-label">120日范围</div>
                    <div className="stock-mini-value stock-mini-range">
                        {recent['120d_low']} ~ {recent['120d_high']}
                    </div>
                </div>
            </div>

            {/* ─── 均线 ─── */}
            <div className="stock-section">
                <h3 className="stock-section-title">均线系统</h3>
                <div className="stock-ma-grid">
                    {[
                        { label: 'MA5', value: ma.ma5 },
                        { label: 'MA10', value: ma.ma10 },
                        { label: 'MA20', value: ma.ma20 },
                        { label: 'MA60', value: ma.ma60 },
                    ].map(m => (
                        <div key={m.label} className="stock-ma-item">
                            <span className="stock-ma-label">{m.label}</span>
                            <span className="stock-ma-value">{m.value ?? '-'}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* ─── 7因子分析 ─── */}
            <div className="stock-section">
                <h3 className="stock-section-title">技术面 7 因子分析（趋势/动量）</h3>
                <div className="stock-factors-grid">
                    {factors.map((f, i) => (
                        <FactorCard key={i} factor={f} />
                    ))}
                </div>
            </div>

            {/* ─── ESG 分析 ─── */}
            {esg && (
                <div className="stock-section">
                    <h3 className="stock-section-title">ESG 有效性分析（基本面）</h3>

                    {/* 基本面数据 */}
                    {Object.keys(esg.fundamentals).length > 0 && (
                        <div className="stock-fundamentals">
                            {Object.entries(esg.fundamentals).map(([k, v]) => (
                                <div key={k} className="stock-fund-item">
                                    <span className="stock-fund-label">{k}</span>
                                    <span className="stock-fund-value">{typeof v === 'number' ? v.toFixed(2) : v}</span>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* ESG 三维度 */}
                    <div className="stock-esg-grid">
                        {esg.dimensions.map((dim, i) => (
                            <ESGCard key={i} dimension={dim} />
                        ))}
                    </div>

                    {/* ESG 总分 */}
                    <div className="stock-esg-total">
                        <div className="stock-esg-score-bar">
                            <div
                                className={`stock-esg-score-fill ${esg.verdict_level}`}
                                style={{ width: `${(esg.total_score / esg.max_score) * 100}%` }}
                            />
                        </div>
                        <div className="stock-esg-score-text">
                            ESG 总分: <strong>{esg.total_score}</strong> / {esg.max_score} · {esg.verdict}
                        </div>
                    </div>
                </div>
            )}

            {report.esg_error && (
                <div className="stock-section">
                    <div className="stock-esg-error">[ESG] {report.esg_error}</div>
                </div>
            )}

            {/* ─── 综合结论 ─── */}
            <div className="stock-conclusion">
                <h3 className="stock-section-title">综合结论</h3>
                <div className="stock-conclusion-body">
                    <div className="stock-conclusion-row">
                        <span className="stock-conclusion-label">技术面:</span>
                        <span className={`stock-conclusion-value ${verdictClass}`}>{summary.verdict}</span>
                    </div>
                    {esg && (
                        <div className="stock-conclusion-row">
                            <span className="stock-conclusion-label">ESG:</span>
                            <span className={`stock-conclusion-value ${esg.verdict_level === 'positive' ? 'verdict-bullish-strong' : esg.verdict_level === 'neutral' ? 'verdict-neutral' : 'verdict-bearish'}`}>
                                {esg.verdict}
                            </span>
                        </div>
                    )}
                    <div className="stock-conclusion-final">
                        {getConclusion(summary.verdict, esg?.verdict_level)}
                    </div>
                </div>
                <div className="stock-disclaimer">以上仅为技术面+基本面分析，不构成投资建议。</div>
            </div>
        </div>
    )
}

// ─── Factor Card ───────────────────────────────────────

const FactorCard: React.FC<{ factor: FactorResult }> = ({ factor }) => {
    const icon = factor.is_bullish ? '[多]' : '[空]'
    const cardClass = factor.is_bullish ? 'factor-card-bullish' : 'factor-card-bearish'

    return (
        <div className={`stock-factor-card ${cardClass}`}>
            <div className="stock-factor-header">
                <span className="stock-factor-icon">{icon}</span>
                <span className="stock-factor-name">{factor.name}</span>
                <span className="stock-factor-score">得分: {factor.score}</span>
            </div>
            {Object.keys(factor.values).length > 0 && (
                <div className="stock-factor-values">
                    {Object.entries(factor.values).map(([k, v]) => (
                        <span key={k} className="stock-factor-value">
                            {k}=<strong>{v}</strong>
                        </span>
                    ))}
                </div>
            )}
            <div className="stock-factor-signals">
                {factor.signals.map((s, i) => (
                    <div key={i} className="stock-factor-signal">→ {s}</div>
                ))}
            </div>
        </div>
    )
}

// ─── ESG Card ──────────────────────────────────────────

const ESGCard: React.FC<{ dimension: ESGDimension }> = ({ dimension }) => {
    const pct = (dimension.score / dimension.max) * 100
    const colorClass = dimension.score >= 3 ? 'esg-bar-high' : dimension.score >= 2 ? 'esg-bar-mid' : dimension.score >= 1 ? 'esg-bar-low' : 'esg-bar-zero'

    return (
        <div className="stock-esg-card">
            <div className="stock-esg-card-header">
                <span className="stock-esg-card-name">{dimension.name}</span>
                <span className="stock-esg-card-score">{dimension.score}/{dimension.max}</span>
            </div>
            <div className="stock-esg-bar">
                <div className={`stock-esg-bar-fill ${colorClass}`} style={{ width: `${pct}%` }} />
            </div>
            <div className="stock-esg-card-signals">
                {dimension.signals.map((s, i) => (
                    <div key={i} className="stock-esg-card-signal">→ {s}</div>
                ))}
            </div>
        </div>
    )
}

// ─── Helper ────────────────────────────────────────────

function getConclusion(techVerdict: string, esgLevel?: string): string {
    const isStrongBull = techVerdict.includes('强信号')
    const isBearish = techVerdict.includes('偏空')

    if (isStrongBull && esgLevel === 'positive') return '技术面+ESG双重利好，强势确认'
    if (isStrongBull && esgLevel === 'negative') return '技术面强但ESG存疑，需警惕基本面风险'
    if (isBearish && esgLevel === 'positive') return '技术面偏空但ESG正面，可能超跌待反弹'
    if (isBearish && esgLevel === 'negative') return '技术面+ESG双重偏空，规避'
    return '技术面与ESG信号混合，中性观望'
}

export default StockAnalysis
