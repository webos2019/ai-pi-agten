/**
 * errorMonitor.ts — 前端错误监测与诊断工具
 *
 * 功能:
 * 1. 捕获全局 JS 错误、未处理的 Promise 拒绝
 * 2. 捕获 React 组件渲染错误（通过 ErrorBoundary）
 * 3. 捕获资源加载失败（CSS/JS/图片）
 * 4. 记录 IndexedDB / localStorage 操作异常
 * 5. 提供一键导出错误日志功能
 * 6. 白屏检测：页面加载后检查 root 是否有内容
 */

interface ErrorRecord {
    type: 'js' | 'promise' | 'resource' | 'react' | 'storage' | 'white-screen'
    message: string
    stack?: string
    source?: string
    lineno?: number
    colno?: number
    timestamp: number
    url: string
    userAgent: string
}

const STORAGE_KEY = 'pi_error_log'
const MAX_RECORDS = 100

let errorLog: ErrorRecord[] = []

// ─── 从 localStorage 恢复历史日志 ─────────────────────
try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) errorLog = parsed.slice(-MAX_RECORDS)
    }
} catch { /* ignore */ }

function saveLog() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(errorLog.slice(-MAX_RECORDS)))
    } catch { /* ignore */ }
}

function push(record: ErrorRecord) {
    errorLog.push(record)
    saveLog()
    // 同时输出到控制台，方便开发调试
    console.error(`[ErrorMonitor][${record.type}]`, record.message, record.stack || '')
}

// ─── 初始化错误监测 ───────────────────────────────────
function initErrorMonitor() {
    try {
        // 1. 全局 JS 错误
        window.addEventListener('error', (e) => {
            try {
                if (e.filename && (e.filename.includes('chrome-extension') || e.filename.includes('moz-extension'))) return
                push({
                    type: 'js',
                    message: e.message,
                    source: e.filename,
                    lineno: e.lineno,
                    colno: e.colno,
                    stack: e.error?.stack,
                    timestamp: Date.now(),
                    url: location.href,
                    userAgent: navigator.userAgent,
                })
            } catch { /* ignore */ }
        })

        // 2. 未处理的 Promise 拒绝
        window.addEventListener('unhandledrejection', (e) => {
            try {
                const reason = e.reason
                const msg = reason instanceof Error ? reason.message : String(reason)
                const stack = reason instanceof Error ? reason.stack : undefined
                push({
                    type: 'promise',
                    message: msg,
                    stack,
                    timestamp: Date.now(),
                    url: location.href,
                    userAgent: navigator.userAgent,
                })
            } catch { /* ignore */ }
        })

        // 3. 资源加载失败
        window.addEventListener('error', (e) => {
            try {
                const target = e.target as HTMLElement
                if (target && (target.tagName === 'SCRIPT' || target.tagName === 'LINK' || target.tagName === 'IMG')) {
                    const src = (target as any).src || (target as any).href || ''
                    push({
                        type: 'resource',
                        message: `资源加载失败: ${target.tagName} ${src}`,
                        source: src,
                        timestamp: Date.now(),
                        url: location.href,
                        userAgent: navigator.userAgent,
                    })
                }
            } catch { /* ignore */ }
        }, true)

        // 4. 白屏检测
        function checkWhiteScreen() {
            const root = document.getElementById('root')
            setTimeout(() => {
                try {
                    if (!root) {
                        push({
                            type: 'white-screen',
                            message: '白屏检测: #root 元素不存在',
                            timestamp: Date.now(),
                            url: location.href,
                            userAgent: navigator.userAgent,
                        })
                        return
                    }
                    const hasContent = root.children.length > 0 || root.textContent?.trim().length
                    if (!hasContent) {
                        push({
                            type: 'white-screen',
                            message: `白屏检测: #root 无内容 (children=${root.children.length}, text=${root.textContent?.length || 0})`,
                            timestamp: Date.now(),
                            url: location.href,
                            userAgent: navigator.userAgent,
                        })
                    }
                } catch { /* ignore */ }
            }, 3000)
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', checkWhiteScreen)
        } else {
            checkWhiteScreen()
        }
    } catch { /* ignore */ }
}

initErrorMonitor()

// ─── React 错误边界回调 ────────────────────────────
export function reportReactError(error: Error, componentStack: string) {
    try {
        push({
            type: 'react',
            message: error.message,
            stack: (error.stack || '') + '\n\n组件栈:\n' + componentStack,
            timestamp: Date.now(),
            url: location.href,
            userAgent: navigator.userAgent,
        })
    } catch { /* ignore */ }
}

// ─── 存储操作异常 ──────────────────────────────────
export function reportStorageError(operation: string, error: any) {
    try {
        push({
            type: 'storage',
            message: `IndexedDB/localStorage 操作失败 [${operation}]: ${error?.message || String(error)}`,
            stack: error?.stack,
            timestamp: Date.now(),
            url: location.href,
            userAgent: navigator.userAgent,
        })
    } catch { /* ignore */ }
}

// ─── 导出日志 ─────────────────────────────────────────
export function exportErrorLog(): string {
    const lines = errorLog.map(r => {
        const time = new Date(r.timestamp).toLocaleString()
        return `[${time}][${r.type}] ${r.message}${r.source ? ` @ ${r.source}:${r.lineno}:${r.colno}` : ''}${r.stack ? '\n' + r.stack : ''}`
    })
    return lines.join('\n---\n')
}

export function downloadErrorLog() {
    const blob = new Blob([exportErrorLog()], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `pi-error-log-${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(url)
}

export function getErrorLog(): ErrorRecord[] {
    return [...errorLog]
}

export function clearErrorLog() {
    errorLog = []
    localStorage.removeItem(STORAGE_KEY)
}

// ─── 暴露到全局，方便控制台调用 ───────────────────────
;(window as any).__errorMonitor = {
    getLog: getErrorLog,
    exportLog: exportErrorLog,
    downloadLog: downloadErrorLog,
    clearLog: clearErrorLog,
}
