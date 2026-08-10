import { useState, useCallback, useRef, useEffect } from 'react'

interface KlineData {
    dates: string[]
    opens: number[]
    closes: number[]
    highs: number[]
    lows: number[]
    volumes: number[]
}

interface Fractal {
    index: number
    date: string
    type: 'top' | 'bottom'
    high: number
    low: number
}

interface Stroke {
    start_idx: number
    end_idx: number
    start_date: string
    end_date: string
    type: 'up' | 'down'
    high: number
    low: number
    start_price: number
    end_price: number
    kline_count: number
}

interface Segment {
    start_idx: number
    end_idx: number
    start_date: string
    end_date: string
    type: 'up' | 'down'
    high: number
    low: number
    stroke_count: number
    start_price: number
    end_price: number
}

interface Pivot {
    start_idx: number
    end_idx: number
    start_date: string
    end_date: string
    upper: number
    lower: number
    mid: number
    height: number
    type: 'up' | 'down'
}

interface Divergence {
    type: string
    direction: 'up' | 'down'
    seg1_idx: number
    seg2_idx: number
    strength_ratio: number
    vol_shrink: boolean
    signal: string
    start_date: string
    end_date: string
}

interface BuySellPoint {
    type: string
    category: 'buy' | 'sell'
    level: number
    date: string
    index: number
    price: number
    signal: string
    description: string
}

interface Summary {
    code: string
    latest_price: number
    latest_date: string
    trend: string
    position: string
    latest_signal: string
    divergence_status: string
    fractal_count: number
    stroke_count: number
    segment_count: number
    pivot_count: number
    divergence_count: number
    point_count: number
    buy_points: BuySellPoint[]
    sell_points: BuySellPoint[]
}

interface ChanlunResult {
    code: string
    klines: KlineData
    fractals: Fractal[]
    strokes: Stroke[]
    segments: Segment[]
    pivots: Pivot[]
    divergences: Divergence[]
    points: BuySellPoint[]
    summary: Summary
}

const PRESET_STOCKS = [
    { code: '000725', name: '京东方A' },
    { code: '300274', name: '阳光电源' },
    { code: '600008', name: '首创环保' },
    { code: '605117', name: '德业股份' },
    { code: '688248', name: '南网科技' },
    { code: '300093', name: '金刚光伏' },
    { code: '600666', name: '奥瑞德' },
    { code: '603444', name: '吉比特' },
]

export default function ChanlunAnalysis() {
    const [code, setCode] = useState('')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [result, setResult] = useState<ChanlunResult | null>(null)
    const [history, setHistory] = useState<{ code: string; name: string; date: string }[]>([])
    const canvasRef = useRef<HTMLCanvasElement>(null)

    // 从localStorage读取历史
    useEffect(() => {
        const saved = localStorage.getItem('chanlun_history')
        if (saved) {
            try { setHistory(JSON.parse(saved)) } catch { /* ignore */ }
        }
    }, [])

    const saveHistory = useCallback((code: string, name: string) => {
        setHistory(prev => {
            const filtered = prev.filter(h => h.code !== code)
            const updated = [{ code, name, date: new Date().toLocaleString('zh-CN') }, ...filtered].slice(0, 10)
            localStorage.setItem('chanlun_history', JSON.stringify(updated))
            return updated
        })
    }, [])

    const analyze = useCallback(async (stockCode: string) => {
        if (!stockCode.trim()) return
        setLoading(true)
        setError('')
        setResult(null)

        try {
            const res = await fetch(`/api/chanlun-analysis?code=${encodeURIComponent(stockCode.trim())}&limit=120`)
            const data = await res.json()
            if (data.error) {
                setError(data.error)
                return
            }
            setResult(data)
            saveHistory(data.code, data.summary?.latest_date ? `${data.code}` : data.code)
        } catch (e) {
            setError('请求失败，请检查网络或股票代码')
        } finally {
            setLoading(false)
        }
    }, [saveHistory])

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault()
        analyze(code)
    }

    // 绘制K线图 + 缠论结构
    useEffect(() => {
        if (!result || !canvasRef.current) return
        const canvas = canvasRef.current
        const ctx = canvas.getContext('2d')
        if (!ctx) return

        const dpr = window.devicePixelRatio || 1
        const width = canvas.clientWidth
        const height = canvas.clientHeight
        canvas.width = width * dpr
        canvas.height = height * dpr
        ctx.scale(dpr, dpr)

        const klines = result.klines
        const n = klines.dates.length
        if (n === 0) return

        const volRatio = 0.22  // 成交量区域占22%
        const padding = { top: 20, right: 60, bottom: 40, left: 10 }
        const chartW = width - padding.left - padding.right
        const chartH = height - padding.top - padding.bottom
        const volH = Math.round(chartH * volRatio)
        const priceH = chartH - volH - 10  // 10px 分隔间距
        const priceTop = padding.top
        const volTop = priceTop + priceH + 10

        const allHighs = klines.highs
        const allLows = klines.lows
        const maxPrice = Math.max(...allHighs) * 1.02
        const minPrice = Math.min(...allLows) * 0.98
        const priceRange = maxPrice - minPrice

        const candleW = Math.max(2, Math.min(12, chartW / n - 1))
        const gap = Math.max(1, chartW / n - candleW)

        // 价格转Y坐标
        const priceToY = (p: number) => priceTop + priceH - ((p - minPrice) / priceRange) * priceH

        // 成交量
        const volumes = klines.volumes
        const maxVol = Math.max(...volumes) * 1.05
        const volToY = (v: number) => volTop + volH - (v / maxVol) * volH

        // 清空
        ctx.clearRect(0, 0, width, height)

        // 绘制价格区网格线
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.15)'
        ctx.lineWidth = 0.5
        for (let i = 0; i <= 5; i++) {
            const y = priceTop + (priceH / 5) * i
            ctx.beginPath()
            ctx.moveTo(padding.left, y)
            ctx.lineTo(width - padding.right, y)
            ctx.stroke()

            const price = maxPrice - (priceRange / 5) * i
            ctx.fillStyle = '#94a3b8'
            ctx.font = '10px sans-serif'
            ctx.textAlign = 'left'
            ctx.fillText(price.toFixed(2), width - padding.right + 4, y + 3)
        }

        // 分隔线
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.25)'
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(padding.left, volTop - 5)
        ctx.lineTo(width - padding.right, volTop - 5)
        ctx.stroke()

        // 成交量区网格线 (3条)
        for (let i = 0; i <= 2; i++) {
            const y = volTop + (volH / 2) * i
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.08)'
            ctx.lineWidth = 0.5
            ctx.beginPath()
            ctx.moveTo(padding.left, y)
            ctx.lineTo(width - padding.right, y)
            ctx.stroke()
        }
        // 成交量最大值标签
        ctx.fillStyle = '#64748b'
        ctx.font = '9px sans-serif'
        ctx.textAlign = 'left'
        ctx.fillText((maxVol / 1e4).toFixed(0) + '万', width - padding.right + 4, volTop + 9)

        // 绘制K线
        for (let i = 0; i < n; i++) {
            const x = padding.left + i * (candleW + gap) + candleW / 2
            const open = klines.opens[i]
            const close = klines.closes[i]
            const high = klines.highs[i]
            const low = klines.lows[i]

            const isUp = close >= open
            const color = isUp ? '#ef4444' : '#22c55e'

            // 影线
            ctx.strokeStyle = color
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x, priceToY(high))
            ctx.lineTo(x, priceToY(low))
            ctx.stroke()

            // 实体
            const bodyTop = priceToY(Math.max(open, close))
            const bodyBottom = priceToY(Math.min(open, close))
            const bodyH = Math.max(1, bodyBottom - bodyTop)

            ctx.fillStyle = color
            ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH)
        }

        // 绘制分型标记
        result.fractals.forEach(f => {
            const x = padding.left + f.index * (candleW + gap) + candleW / 2
            const y = priceToY(f.type === 'top' ? f.high : f.low)
            ctx.fillStyle = f.type === 'top' ? '#a855f7' : '#3b82f6'
            ctx.font = 'bold 11px sans-serif'
            ctx.textAlign = 'center'
            const label = f.type === 'top' ? '顶' : '底'
            const offset = f.type === 'top' ? -12 : 16
            ctx.fillText(label, x, y + offset)
        })

        // 绘制笔 (连接分型)
        ctx.lineWidth = 1.5
        result.strokes.forEach(s => {
            const x1 = padding.left + s.start_idx * (candleW + gap) + candleW / 2
            const x2 = padding.left + s.end_idx * (candleW + gap) + candleW / 2
            const y1 = priceToY(s.start_price)
            const y2 = priceToY(s.end_price)

            ctx.strokeStyle = s.type === 'up' ? '#ef4444' : '#22c55e'
            ctx.setLineDash([4, 2])
            ctx.beginPath()
            ctx.moveTo(x1, y1)
            ctx.lineTo(x2, y2)
            ctx.stroke()
            ctx.setLineDash([])
        })

        // 绘制线段 (粗实线)
        ctx.lineWidth = 2.5
        result.segments.forEach(seg => {
            const x1 = padding.left + seg.start_idx * (candleW + gap) + candleW / 2
            const x2 = padding.left + seg.end_idx * (candleW + gap) + candleW / 2
            const y1 = priceToY(seg.start_price)
            const y2 = priceToY(seg.end_price)

            ctx.strokeStyle = seg.type === 'up' ? '#dc2626' : '#16a34a'
            ctx.beginPath()
            ctx.moveTo(x1, y1)
            ctx.lineTo(x2, y2)
            ctx.stroke()
        })

        // 绘制中枢 (矩形框)
        result.pivots.forEach((p, idx) => {
            const x1 = padding.left + p.start_idx * (candleW + gap)
            const x2 = padding.left + p.end_idx * (candleW + gap) + candleW
            const y1 = priceToY(p.upper)
            const y2 = priceToY(p.lower)

            const colors = ['rgba(234, 179, 8, 0.15)', 'rgba(168, 85, 247, 0.15)', 'rgba(59, 130, 246, 0.15)']
            const borderColors = ['#eab308', '#a855f7', '#3b82f6']
            const ci = idx % colors.length

            ctx.fillStyle = colors[ci]
            ctx.fillRect(x1, y1, x2 - x1, y2 - y1)
            ctx.strokeStyle = borderColors[ci]
            ctx.lineWidth = 1
            ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)

            // 中枢标签
            ctx.fillStyle = borderColors[ci]
            ctx.font = 'bold 10px sans-serif'
            ctx.textAlign = 'center'
            ctx.fillText(`ZG${idx + 1}`, (x1 + x2) / 2, y1 - 4)
        })

        // 绘制买卖点标记
        result.points.forEach(p => {
            const x = padding.left + p.index * (candleW + gap) + candleW / 2
            const y = priceToY(p.price)

            const isBuy = p.category === 'buy'
            ctx.fillStyle = isBuy ? '#dc2626' : '#16a34a'
            ctx.beginPath()
            if (isBuy) {
                // 向上箭头
                ctx.moveTo(x, y - 18)
                ctx.lineTo(x - 5, y - 8)
                ctx.lineTo(x + 5, y - 8)
            } else {
                // 向下箭头
                ctx.moveTo(x, y + 18)
                ctx.lineTo(x - 5, y + 8)
                ctx.lineTo(x + 5, y + 8)
            }
            ctx.closePath()
            ctx.fill()

            ctx.fillStyle = isBuy ? '#dc2626' : '#16a34a'
            ctx.font = 'bold 9px sans-serif'
            ctx.textAlign = 'center'
            ctx.fillText(`买${p.level}` , x, isBuy ? y - 22 : y + 28)
        })

        // 绘制成交量柱状图
        for (let i = 0; i < n; i++) {
            const x = padding.left + i * (candleW + gap) + candleW / 2
            const open = klines.opens[i]
            const close = klines.closes[i]
            const vol = volumes[i]
            const isUp = close >= open
            const color = isUp ? 'rgba(239, 68, 68, 0.55)' : 'rgba(34, 197, 94, 0.55)'

            const barH = Math.max(1, (vol / maxVol) * volH)
            const y = volTop + volH - barH

            ctx.fillStyle = color
            ctx.fillRect(x - candleW / 2, y, candleW, barH)
        }

        // X轴日期标签 (每20根显示一个)
        ctx.fillStyle = '#64748b'
        ctx.font = '9px sans-serif'
        ctx.textAlign = 'center'
        for (let i = 0; i < n; i += Math.ceil(n / 8)) {
            const x = padding.left + i * (candleW + gap) + candleW / 2
            const dateStr = klines.dates[i].slice(5) // MM-DD
            ctx.fillText(dateStr, x, height - 10)
        }

    }, [result])

    return (
        <div className="chanlun-container">
            {/* 搜索栏 */}
            <div className="chanlun-search-bar">
                <form onSubmit={handleSubmit} className="chanlun-search-form">
                    <input
                        type="text"
                        className="chanlun-search-input"
                        placeholder="输入股票代码 (如 000725、300274)"
                        value={code}
                        onChange={e => setCode(e.target.value)}
                    />
                    <button type="submit" className="chanlun-search-btn" disabled={loading}>
                        {loading ? '分析中...' : '缠论分析'}
                    </button>
                </form>
                <div className="chanlun-quick-picks">
                    {PRESET_STOCKS.map(s => (
                        <button
                            key={s.code}
                            className="chanlun-quick-btn"
                            onClick={() => { setCode(s.code); analyze(s.code) }}
                        >
                            {s.name}
                        </button>
                    ))}
                </div>
                {history.length > 0 && (
                    <div className="chanlun-history">
                        <span className="chanlun-history-label">历史:</span>
                        {history.slice(0, 5).map(h => (
                            <button
                                key={h.code}
                                className="chanlun-history-btn"
                                onClick={() => { setCode(h.code); analyze(h.code) }}
                            >
                                {h.code}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {error && <div className="chanlun-error">{error}</div>}

            {result && (
                <div className="chanlun-content">
                    {/* 摘要卡片 */}
                    <div className="chanlun-summary-grid">
                        <div className="chanlun-summary-card">
                            <div className="chanlun-summary-label">最新价</div>
                            <div className="chanlun-summary-value">{result.summary.latest_price}</div>
                            <div className="chanlun-summary-date">{result.summary.latest_date}</div>
                        </div>
                        <div className="chanlun-summary-card">
                            <div className="chanlun-summary-label">趋势判断</div>
                            <div className={`chanlun-summary-value ${result.summary.trend.includes('上') ? 'up' : result.summary.trend.includes('下') ? 'down' : ''}`}>
                                {result.summary.trend}
                            </div>
                        </div>
                        <div className="chanlun-summary-card">
                            <div className="chanlun-summary-label">当前位置</div>
                            <div className="chanlun-summary-value">{result.summary.position}</div>
                        </div>
                        <div className="chanlun-summary-card">
                            <div className="chanlun-summary-label">背驰状态</div>
                            <div className={`chanlun-summary-value ${result.summary.divergence_status.includes('背驰') ? 'highlight' : ''}`}>
                                {result.summary.divergence_status}
                            </div>
                        </div>
                        <div className="chanlun-summary-card">
                            <div className="chanlun-summary-label">最新信号</div>
                            <div className={`chanlun-summary-value ${result.summary.latest_signal.includes('买') ? 'buy' : result.summary.latest_signal.includes('卖') ? 'sell' : ''}`}>
                                {result.summary.latest_signal}
                            </div>
                        </div>
                    </div>

                    {/* K线图 */}
                    <div className="chanlun-chart-section">
                        <h3 className="chanlun-section-title">K线 + 缠论结构图</h3>
                        <div className="chanlun-chart-legend">
                            <span className="legend-item"><span className="legend-dot" style={{ background: '#dc2626' }}></span>向上线段</span>
                            <span className="legend-item"><span className="legend-dot" style={{ background: '#16a34a' }}></span>向下线段</span>
                            <span className="legend-item"><span className="legend-dot" style={{ background: '#a855f7' }}></span>顶分型</span>
                            <span className="legend-item"><span className="legend-dot" style={{ background: '#3b82f6' }}></span>底分型</span>
                            <span className="legend-item"><span className="legend-box" style={{ borderColor: '#eab308', background: 'rgba(234,179,8,0.15)' }}></span>中枢</span>
                            <span className="legend-item"><span className="legend-arrow" style={{ color: '#dc2626' }}>▲</span>买点</span>
                            <span className="legend-item"><span className="legend-arrow" style={{ color: '#16a34a' }}>▼</span>卖点</span>
                        </div>
                        <canvas
                            ref={canvasRef}
                            className="chanlun-chart-canvas"
                            style={{ width: '100%', height: '460px' }}
                        />
                    </div>

                    {/* 结构统计 */}
                    <div className="chanlun-stats-grid">
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.fractal_count}</div>
                            <div className="chanlun-stat-label">分型</div>
                        </div>
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.stroke_count}</div>
                            <div className="chanlun-stat-label">笔</div>
                        </div>
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.segment_count}</div>
                            <div className="chanlun-stat-label">线段</div>
                        </div>
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.pivot_count}</div>
                            <div className="chanlun-stat-label">中枢</div>
                        </div>
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.divergence_count}</div>
                            <div className="chanlun-stat-label">背驰</div>
                        </div>
                        <div className="chanlun-stat-card">
                            <div className="chanlun-stat-num">{result.summary.point_count}</div>
                            <div className="chanlun-stat-label">买卖点</div>
                        </div>
                    </div>

                    {/* 买卖点详情 */}
                    {(result.summary.buy_points.length > 0 || result.summary.sell_points.length > 0) && (
                        <div className="chanlun-points-section">
                            <h3 className="chanlun-section-title">买卖点详情</h3>
                            {result.summary.buy_points.length > 0 && (
                                <div className="chanlun-points-group">
                                    <h4 className="chanlun-points-group-title buy">买点信号</h4>
                                    <div className="chanlun-points-list">
                                        {result.summary.buy_points.map((p, i) => (
                                            <div key={i} className="chanlun-point-item buy">
                                                <span className="chanlun-point-type">{p.type}</span>
                                                <span className="chanlun-point-date">{p.date}</span>
                                                <span className="chanlun-point-price">@{p.price}</span>
                                                <span className="chanlun-point-signal">{p.signal}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {result.summary.sell_points.length > 0 && (
                                <div className="chanlun-points-group">
                                    <h4 className="chanlun-points-group-title sell">卖点信号</h4>
                                    <div className="chanlun-points-list">
                                        {result.summary.sell_points.map((p, i) => (
                                            <div key={i} className="chanlun-point-item sell">
                                                <span className="chanlun-point-type">{p.type}</span>
                                                <span className="chanlun-point-date">{p.date}</span>
                                                <span className="chanlun-point-price">@{p.price}</span>
                                                <span className="chanlun-point-signal">{p.signal}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* 中枢详情 */}
                    {result.pivots.length > 0 && (
                        <div className="chanlun-pivots-section">
                            <h3 className="chanlun-section-title">中枢详情</h3>
                            <div className="chanlun-pivots-list">
                                {result.pivots.map((p, i) => (
                                    <div key={i} className="chanlun-pivot-item">
                                        <span className="chanlun-pivot-name">中枢{i + 1}</span>
                                        <span className="chanlun-pivot-range">{p.start_date} ~ {p.end_date}</span>
                                        <span className="chanlun-pivot-price">[{p.lower} - {p.upper}]</span>
                                        <span className="chanlun-pivot-mid">中轴 {p.mid}</span>
                                        <span className="chanlun-pivot-height">高度 {p.height}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 背驰详情 */}
                    {result.divergences.length > 0 && (
                        <div className="chanlun-divs-section">
                            <h3 className="chanlun-section-title">背驰详情</h3>
                            <div className="chanlun-divs-list">
                                {result.divergences.map((d, i) => (
                                    <div key={i} className="chanlun-div-item">
                                        <span className={`chanlun-div-dir ${d.direction}`}>{d.direction === 'up' ? '上涨' : '下跌'}背驰</span>
                                        <span className="chanlun-div-date">{d.start_date} ~ {d.end_date}</span>
                                        <span className="chanlun-div-ratio">力度比 {d.strength_ratio}</span>
                                        {d.vol_shrink && <span className="chanlun-div-vol">成交量萎缩确认</span>}
                                        <span className="chanlun-div-signal">{d.signal}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
