import React, { useRef, useCallback, useState } from 'react'
import AIInputEditor, { AIInputEditorRef } from './AIInputEditor'
import type { SlashCommand, AtReference, UploadedFile, StructuredRequest } from '../types'

interface Props {
    isStreaming: boolean
    isRetrying: boolean
    slashCommands: SlashCommand[]
    atReferences: AtReference[]
    onSend: (rawText: string, structured: StructuredRequest) => void
    onFileDrop: (files: FileList) => void
    onFileSelect: (files: FileList) => void
    onFileRemove: (index: number) => void
    uploadedFiles: UploadedFile[]
    onCancel: () => void
    steerQueueId: string | null
    steerError: string | null
    onSteer: (text: string) => void
}

function formatSize(bytes: number): string {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

const Footer: React.FC<Props> = ({ isStreaming, isRetrying, slashCommands, atReferences, onSend, onFileDrop, onFileSelect, onFileRemove, uploadedFiles, onCancel, steerQueueId, steerError, onSteer }) => {
    const editorRef = useRef<AIInputEditorRef>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const [steerText, setSteerText] = useState('')

    const handleSend = useCallback(() => {
        if (editorRef.current) {
            const text = editorRef.current.getText()
            if (text.trim()) {
                onSend(text, editorRef.current.getStructuredRequest())
            }
        }
    }, [onSend])

    const handleSteer = useCallback(() => {
        const text = steerText.trim()
        if (!text) return
        onSteer(text)
        setSteerText('')
    }, [steerText, onSteer])

    const handleSteerKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSteer()
        }
    }, [handleSteer])

    // Expose send function globally for retry/regenerate
    React.useEffect(() => {
        ;(window as any).__editorClear = () => editorRef.current?.clear()
        ;(window as any).__editorSetValue = (text: string) => editorRef.current?.setValue(text)
    }, [])

    if (isStreaming) {
        return (
            <div className="footer-container shrink-0 relative z-10">
                {isRetrying ? (
                    <div className="mx-auto flex max-w-2xl items-center justify-center gap-2 px-3 py-2.5 text-sm text-[var(--text-secondary)] sm:px-4">
                        <svg className="spinner h-4 w-4" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        正在尝试恢复，请稍候...
                    </div>
                ) : (
                    <div className="mx-auto max-w-2xl px-3 py-2 sm:px-4">
                        {/* steer 输入区 — Agent 流式输出期间可中途插话 */}
                        {steerQueueId && (
                            <div className="steer-input-wrapper mb-2">
                                <div className="steer-input-row flex items-center gap-1.5">
                                    <input
                                        className="steer-input flex-1"
                                        placeholder="中途插话调整 Agent 方向... (Enter 发送)"
                                        value={steerText}
                                        onChange={(e) => setSteerText(e.target.value)}
                                        onKeyDown={handleSteerKeyDown}
                                    />
                                    <button
                                        className="steer-btn shrink-0"
                                        onClick={handleSteer}
                                        disabled={!steerText.trim()}
                                    >
                                        🔄 插话
                                    </button>
                                </div>
                                {steerError && (
                                    <div className="steer-error mt-1 text-[11px] text-[var(--red-err)]">⚠️ {steerError}</div>
                                )}
                            </div>
                        )}
                        <div className="flex items-center justify-center">
                            <button className="stop-btn flex items-center gap-2 rounded-lg px-5 py-2 text-sm font-medium" onClick={onCancel}>
                                <svg className="stop-icon h-4 w-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
                                停止生成
                            </button>
                        </div>
                    </div>
                )}
            </div>
        )
    }

    return (
        <div className="footer-container shrink-0 relative z-10">
            <div className="mx-auto max-w-2xl px-3 py-2 sm:px-4 sm:py-2.5">
                {/* File Upload */}
                <div className="mb-2">
                    <div className="file-upload-area flex cursor-pointer items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5" onClick={() => fileInputRef.current?.click()}>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            style={{ display: 'none' }}
                            onChange={(e) => { if (e.target.files) onFileSelect(e.target.files); e.target.value = '' }}
                            accept=".py,.js,.ts,.jsx,.tsx,.go,.rs,.java,.md,.json,.yaml,.yml,.css,.scss,.sql,.sh,.bash,.html,.vue,.svelte,.c,.cpp,.h,.hpp,.rb,.php,.swift,.kt,.dart,.toml,.xml,.txt"
                        />
                        <svg className="h-4 w-4 flex-shrink-0 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" /></svg>
                        <span className="text-[11px] text-[var(--text-muted)]">拖拽代码文件到此处，或点击选择文件</span>
                    </div>
                    {uploadedFiles.length > 0 && (
                        <div className="mt-1.5 flex flex-col gap-1">
                            {uploadedFiles.map((f, i) => (
                                <div key={i} className="file-item flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs">
                                    <svg className="h-3.5 w-3.5 flex-shrink-0 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                                    <span className="file-item-name">{f.name}</span>
                                    <span className="text-xs text-[var(--text-muted)] flex-shrink-0">{formatSize(f.size)}</span>
                                    <button className="file-item-remove ml-auto flex-shrink-0 cursor-pointer bg-transparent border-none text-[var(--text-muted)]" onClick={() => onFileRemove(i)}>
                                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
                {/* Rich Text Editor */}
                <AIInputEditor
                    ref={editorRef}
                    placeholder="输入问题，/ 调出命令菜单，@ 引用版本方案或工具 (Ctrl+Enter 发送)"
                    slashCommands={slashCommands}
                    atReferences={atReferences}
                    onSend={onSend}
                    onFileDrop={onFileDrop}
                />
                <p className="input-hint mt-1.5 text-center text-[11px] text-[var(--text-muted)]">支持 / 斜杠命令、@ 引用版本方案或工具、Ctrl+Enter 发送</p>
            </div>
        </div>
    )
}

export default Footer
