import React from 'react'

interface Props {
    children: React.ReactNode
    fallback?: React.ReactNode | ((error: Error, reset: () => void) => React.ReactNode)
}

interface State {
    error: Error | null
}

/**
 * 错误边界 — 捕获子组件渲染异常，防止整页白屏
 *
 * 用法:
 * <ErrorBoundary>
 *     <FreeChannels />
 * </ErrorBoundary>
 *
 * 自定义 fallback:
 * <ErrorBoundary fallback={(error, reset) => <div>出错了: {error.message}</div>}>
 *     <FreeChannels />
 * </ErrorBoundary>
 */
class ErrorBoundary extends React.Component<Props, State> {
    constructor(props: Props) {
        super(props)
        this.state = { error: null }
    }

    static getDerivedStateFromError(error: Error): State {
        return { error }
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('[ErrorBoundary] 组件渲染崩溃:', error)
        console.error('[ErrorBoundary] 组件栈:', errorInfo.componentStack)
        // 上报到错误监测系统
        try {
            const { reportReactError } = require('../lib/errorMonitor')
            reportReactError(error, errorInfo.componentStack)
        } catch { /* ignore */ }
    }

    reset = () => {
        this.setState({ error: null })
    }

    render() {
        if (this.state.error) {
            const { fallback } = this.props
            if (typeof fallback === 'function') {
                return fallback(this.state.error, this.reset)
            }
            if (fallback) {
                return fallback
            }
            return (
                <div style={{ padding: '40px', textAlign: 'center', color: '#666' }}>
                    <div style={{ fontSize: '48px', marginBottom: '16px' }}>⚠</div>
                    <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '8px', color: '#333' }}>
                        页面加载出错
                    </h2>
                    <p style={{ fontSize: '13px', color: '#999', marginBottom: '20px', wordBreak: 'break-all' }}>
                        {this.state.error.message}
                    </p>
                    <button
                        onClick={this.reset}
                        style={{
                            padding: '8px 24px', fontSize: '13px', fontWeight: 600,
                            background: '#4b607c', color: '#fff', border: 'none',
                            borderRadius: '8px', cursor: 'pointer',
                        }}
                    >
                        重试
                    </button>
                </div>
            )
        }
        return this.props.children
    }
}

export default ErrorBoundary
