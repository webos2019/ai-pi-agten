import React, { useState, useEffect, useCallback } from 'react'

interface KBDocument {
    docId: string
    title: string
    sourceType: string
    sourcePath: string
    chunkCount: number
    charCount: number
    createdAt: number
}

interface KBSearchResult {
    docId: string
    docTitle: string
    chunkIndex: number
    text: string
    score: number
}

interface Props {
    sessionId: string
}

const KnowledgeBase: React.FC<Props> = ({ sessionId }) => {
    const [documents, setDocuments] = useState<KBDocument[]>([])
    const [loading, setLoading] = useState(false)
    const [uploadMode, setUploadMode] = useState<'text' | 'file' | 'url'>('text')
    const [title, setTitle] = useState('')
    const [textContent, setTextContent] = useState('')
    const [filePath, setFilePath] = useState('')
    const [url, setUrl] = useState('')
    const [uploading, setUploading] = useState(false)
    const [uploadMsg, setUploadMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

    // 搜索
    const [searchQuery, setSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState<KBSearchResult[]>([])
    const [searching, setSearching] = useState(false)

    // 加载文档列表
    const loadDocuments = useCallback(async () => {
        if (!sessionId) return
        try {
            const resp = await fetch(`/api/kb/documents?session_id=${sessionId}`)
            const data = await resp.json()
            if (data.documents) setDocuments(data.documents)
        } catch { /* ignore */ }
    }, [sessionId])

    useEffect(() => {
        loadDocuments()
    }, [loadDocuments])

    // 上传文档
    const handleUpload = async () => {
        if (!sessionId) return
        setUploading(true)
        setUploadMsg(null)
        try {
            const body: Record<string, string> = { sessionId, title: title || '未命名文档' }
            if (uploadMode === 'text') {
                if (!textContent.trim()) { setUploadMsg({ type: 'error', text: '请输入文本内容' }); return }
                body.text = textContent
            } else if (uploadMode === 'file') {
                if (!filePath.trim()) { setUploadMsg({ type: 'error', text: '请输入文件路径' }); return }
                body.filePath = filePath
            } else if (uploadMode === 'url') {
                if (!url.trim()) { setUploadMsg({ type: 'error', text: '请输入URL' }); return }
                body.url = url
            }
            const resp = await fetch('/api/kb/upload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            })
            const data = await resp.json()
            if (!resp.ok) {
                setUploadMsg({ type: 'error', text: data.error || '上传失败' })
            } else {
                setUploadMsg({ type: 'success', text: `上传成功: ${data.document.chunkCount} 个分块, ${data.document.charCount} 字` })
                setTitle(''); setTextContent(''); setFilePath(''); setUrl('')
                await loadDocuments()
            }
        } catch {
            setUploadMsg({ type: 'error', text: '网络错误' })
        } finally {
            setUploading(false)
        }
    }

    // 删除文档
    const handleDelete = async (docId: string) => {
        if (!sessionId) return
        try {
            await fetch(`/api/kb/documents/${docId}?session_id=${sessionId}`, { method: 'DELETE' })
            await loadDocuments()
        } catch { /* ignore */ }
    }

    // 搜索知识库
    const handleSearch = async () => {
        if (!sessionId || !searchQuery.trim()) return
        setSearching(true)
        try {
            const resp = await fetch('/api/kb/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionId, query: searchQuery }),
            })
            const data = await resp.json()
            setSearchResults(data.results || [])
        } catch { /* ignore */ }
        finally { setSearching(false) }
    }

    const sourceIcon = (type: string) => {
        switch (type) {
            case 'pdf': return '📄'
            case 'url': return '🌐'
            case 'markdown': return '📝'
            default: return '📃'
        }
    }

    return (
        <div className="kb-page">
            <div className="kb-header">
                <h2 className="kb-title">📚 知识库管理</h2>
                <span className="kb-count">{documents.length} 个文档</span>
            </div>

            {/* 上传区域 */}
            <div className="kb-card">
                <div className="kb-section-title">添加文档</div>
                <div className="kb-tabs">
                    <button className={`kb-tab ${uploadMode === 'text' ? 'active' : ''}`} onClick={() => setUploadMode('text')}>📝 粘贴文本</button>
                    <button className={`kb-tab ${uploadMode === 'file' ? 'active' : ''}`} onClick={() => setUploadMode('file')}>📁 本地文件</button>
                    <button className={`kb-tab ${uploadMode === 'url' ? 'active' : ''}`} onClick={() => setUploadMode('url')}>🌐 URL</button>
                </div>

                <input
                    type="text"
                    className="kb-input"
                    placeholder="文档标题（如：产品说明书）"
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                />

                {uploadMode === 'text' && (
                    <textarea
                        className="kb-textarea"
                        placeholder="粘贴文档内容..."
                        value={textContent}
                        onChange={e => setTextContent(e.target.value)}
                        rows={6}
                    />
                )}
                {uploadMode === 'file' && (
                    <input
                        type="text"
                        className="kb-input"
                        placeholder="本地文件路径（如：c:\\docs\\manual.pdf）"
                        value={filePath}
                        onChange={e => setFilePath(e.target.value)}
                    />
                )}
                {uploadMode === 'url' && (
                    <input
                        type="text"
                        className="kb-input"
                        placeholder="https://example.com/article.html"
                        value={url}
                        onChange={e => setUrl(e.target.value)}
                    />
                )}

                <button className="kb-btn-primary" onClick={handleUpload} disabled={uploading}>
                    {uploading ? '上传中...' : '🚀 上传到知识库'}
                </button>

                {uploadMsg && (
                    <div className={`kb-alert ${uploadMsg.type}`}>{uploadMsg.text}</div>
                )}
            </div>

            {/* 文档列表 */}
            <div className="kb-card">
                <div className="kb-section-title">已上传文档</div>
                {documents.length === 0 ? (
                    <div className="kb-empty">暂无文档，请上传</div>
                ) : (
                    <div className="kb-doc-list">
                        {documents.map(doc => (
                            <div key={doc.docId} className="kb-doc-item">
                                <div className="kb-doc-icon">{sourceIcon(doc.sourceType)}</div>
                                <div className="kb-doc-info">
                                    <div className="kb-doc-name">{doc.title}</div>
                                    <div className="kb-doc-meta">
                                        {doc.chunkCount} 块 · {doc.charCount} 字 · {doc.sourceType}
                                    </div>
                                </div>
                                <button
                                    className="kb-doc-delete"
                                    onClick={() => handleDelete(doc.docId)}
                                    title="删除"
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                    </svg>
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* 搜索测试 */}
            <div className="kb-card">
                <div className="kb-section-title">搜索测试</div>
                <div className="kb-search-row">
                    <input
                        type="text"
                        className="kb-input"
                        placeholder="输入问题测试召回..."
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleSearch() }}
                    />
                    <button className="kb-btn-primary" onClick={handleSearch} disabled={searching}>
                        {searching ? '搜索中...' : '🔍 搜索'}
                    </button>
                </div>
                {searchResults.length > 0 && (
                    <div className="kb-search-results">
                        {searchResults.map((r, i) => (
                            <div key={i} className="kb-search-item">
                                <div className="kb-search-header">
                                    <span className="kb-search-doc">{r.docTitle} · 片段{r.chunkIndex + 1}</span>
                                    <span className="kb-search-score">score: {r.score}</span>
                                </div>
                                <div className="kb-search-text">{r.text}</div>
                            </div>
                        ))}
                    </div>
                )}
                {searchResults.length === 0 && searchQuery && !searching && (
                    <div className="kb-empty">无匹配结果</div>
                )}
            </div>
        </div>
    )
}

export default KnowledgeBase
