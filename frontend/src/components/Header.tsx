import React from 'react'

interface Props {
    mode: string
    onSetMode: (mode: string) => void
    hasMessages: boolean
    onRegenerate: () => void
}

const Header: React.FC<Props> = ({ mode, onSetMode, hasMessages, onRegenerate }) => {
    const subtitle = mode === 'utility-skill' ? '实用工具模式 · 工具调用 + 流式输出' : '文件与天气模式 · 本地读取 + 实时查询'
    return (
        <header className="header flex shrink-0 items-center justify-between px-3 py-2 relative z-10 sm:px-4 sm:py-3">
            <div className="flex items-center gap-2 sm:gap-3">
                <div className="header-icon" title="Pi Agent">π</div>
                <div>
                    <h1 className="header-title">Pi Agent</h1>
                    <p className="header-subtitle hidden sm:block">{subtitle}</p>
                </div>
            </div>
            <div className="flex items-center gap-1.5 sm:gap-2 relative z-10">
                <div className="mode-selector flex overflow-hidden rounded-md">
                    <button
                        className={`mode-btn px-2.5 py-1.5 text-xs sm:px-3 ${mode === 'utility-skill' ? 'active' : ''}`}
                        onClick={() => onSetMode('utility-skill')}
                    >工具</button>
                    <button
                        className={`mode-btn px-3 py-1.5 text-xs ${mode === 'reader-skill' ? 'active' : ''}`}
                        onClick={() => onSetMode('reader-skill')}
                    >文件</button>
                </div>
                {hasMessages && (
                    <button className="action-btn flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs" onClick={onRegenerate} title="重新生成上一个回答">
                        <svg className="icon h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                        重新生成
                    </button>
                )}
            </div>
        </header>
    )
}

export default Header
