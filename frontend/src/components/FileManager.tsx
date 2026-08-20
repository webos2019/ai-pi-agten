import React, { useState, useEffect, useCallback } from 'react'

interface FileItem {
    filename: string
    size: number
    sizeHuman: string
    extension: string
    createdAt: number
    modifiedAt: number
    downloadUrl: string
}

interface FileListResponse {
    count: number
    totalSize: number
    totalSizeHuman: string
    files: FileItem[]
}

const FileManager: React.FC = () => {
    const [files, setFiles] = useState<FileItem[]>([])
    const [totalSize, setTotalSize] = useState('')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [downloadUrl, setDownloadUrl] = useState('')
    const [downloadName, setDownloadName] = useState('')
    const [downloading, setDownloading] = useState(false)

    const loadFiles = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const resp = await fetch('/api/files/list')
            const data: FileListResponse = await resp.json()
            setFiles(data.files || [])
            setTotalSize(data.totalSizeHuman || '')
        } catch (e) {
            setError((e as Error).message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        loadFiles()
    }, [loadFiles])

    const handleDownload = useCallback(async () => {
        if (!downloadUrl.trim()) return
        setDownloading(true)
        setError(null)
        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: [{ role: 'user', content: `请下载这个文件: ${downloadUrl.trim()}${downloadName.trim() ? `，保存为 ${downloadName.trim()}` : ''}` }],
                    sessionId: 'file_manager_' + Date.now().toString(36),
                }),
            })

            if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

            const reader = resp.body?.getReader()
            if (!reader) throw new Error('无法读取响应流')

            const decoder = new TextDecoder()
            let buffer = ''

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
                        if (data.type === 'error') {
                            setError(data.error || '下载失败')
                        }
                    } catch { /* skip */ }
                }
            }

            setDownloadUrl('')
            setDownloadName('')
            await loadFiles()
        } catch (e) {
            setError((e as Error).message)
        } finally {
            setDownloading(false)
        }
    }, [downloadUrl, downloadName, loadFiles])

    const handleDelete = useCallback(async (filename: string) => {
        if (!confirm(`确定要删除文件 "${filename}" 吗？`)) return
        try {
            const resp = await fetch(`/api/files/${encodeURIComponent(filename)}`, {
                method: 'DELETE',
            })
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({ error: '删除失败' }))
                throw new Error(data.error)
            }
            await loadFiles()
        } catch (e) {
            setError((e as Error).message)
        }
    }, [loadFiles])

    const formatTime = (ts: number) => {
        return new Date(ts * 1000).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        })
    }

    const getFileIcon = (ext: string) => {
        const iconMap: Record<string, string> = {
            '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️',
            '.webp': '🖼️', '.svg': '🖼️', '.bmp': '🖼️',
            '.pdf': '📄', '.doc': '📝', '.docx': '📝',
            '.xls': '📊', '.xlsx': '📊',
            '.ppt': '📑', '.pptx': '📑',
            '.mp3': '🎵', '.wav': '🎵', '.ogg': '🎵', '.m4a': '🎵',
            '.mp4': '🎬', '.avi': '🎬', '.mov': '🎬', '.mkv': '🎬',
            '.zip': '🗜️', '.rar': '🗜️', '.7z': '🗜️', '.gz': '🗜️', '.tar': '🗜️',
            '.txt': '📃', '.md': '📃', '.json': '📃', '.csv': '📃',
            '.py': '🐍', '.js': '📜', '.ts': '📜',
            '.html': '🌐', '.css': '🎨',
            '.epub': '📖', '.mobi': '📖',
        }
        return iconMap[ext] || '📦'
    }

    return (
        <div className="file-manager-page">
            {/* 下载区域 */}
            <div className="file-download-section">
                <h2 className="file-section-title">📥 下载文件</h2>
                <p className="file-section-desc">输入文件 URL，自动下载并保存到服务器</p>
                <div className="file-download-form">
                    <input
                        type="text"
                        className="file-url-input"
                        placeholder="https://example.com/file.pdf"
                        value={downloadUrl}
                        onChange={e => setDownloadUrl(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && !downloading) handleDownload() }}
                    />
                    <input
                        type="text"
                        className="file-name-input"
                        placeholder="自定义文件名（可选）"
                        value={downloadName}
                        onChange={e => setDownloadName(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && !downloading) handleDownload() }}
                    />
                    <button
                        className="file-download-btn"
                        onClick={handleDownload}
                        disabled={!downloadUrl.trim() || downloading}
                    >
                        {downloading ? '⏳ 下载中...' : '⬇ 下载'}
                    </button>
                </div>
            </div>

            {/* 错误提示 */}
            {error && (
                <div className="file-error-banner">
                    <span>⚠️ {error}</span>
                    <button onClick={() => setError(null)}>✕</button>
                </div>
            )}

            {/* 文件列表 */}
            <div className="file-list-section">
                <div className="file-list-header">
                    <h2 className="file-section-title">📂 已下载文件</h2>
                    {totalSize && <span className="file-total-size">共 {files.length} 个文件 · {totalSize}</span>}
                    <button className="file-refresh-btn" onClick={loadFiles} title="刷新">
                        🔄
                    </button>
                </div>

                {loading ? (
                    <div className="file-loading">加载中...</div>
                ) : files.length === 0 ? (
                    <div className="file-empty">
                        <div className="file-empty-icon">📁</div>
                        <p>还没有下载任何文件</p>
                        <p className="file-empty-hint">在上方输入 URL 开始下载</p>
                    </div>
                ) : (
                    <div className="file-grid">
                        {files.map(file => (
                            <div key={file.filename} className="file-card">
                                <div className="file-card-icon">{getFileIcon(file.extension)}</div>
                                <div className="file-card-info">
                                    <div className="file-card-name" title={file.filename}>
                                        {file.filename}
                                    </div>
                                    <div className="file-card-meta">
                                        <span className="file-card-size">{file.sizeHuman}</span>
                                        <span className="file-card-time">{formatTime(file.createdAt)}</span>
                                    </div>
                                </div>
                                <div className="file-card-actions">
                                    <a
                                        href={file.downloadUrl}
                                        download={file.filename}
                                        className="file-card-btn file-card-btn-download"
                                        title="下载到本地"
                                    >
                                        ⬇
                                    </a>
                                    <button
                                        className="file-card-btn file-card-btn-delete"
                                        onClick={() => handleDelete(file.filename)}
                                        title="删除"
                                    >
                                        🗑
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

export default FileManager
