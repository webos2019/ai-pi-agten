import { useState, useEffect, useCallback } from 'react'

export interface HistoryItem {
    text: string
    time: string
}

const HISTORY_KEY = 'pi_input_history'
const HISTORY_MAX = 50

export function useInputHistory() {
    const [history, setHistory] = useState<HistoryItem[]>([])

    useEffect(() => {
        try {
            const saved = localStorage.getItem(HISTORY_KEY)
            if (saved) setHistory(JSON.parse(saved))
        } catch { /* ignore */ }
    }, [])

    const persist = useCallback((items: HistoryItem[]) => {
        try { localStorage.setItem(HISTORY_KEY, JSON.stringify(items)) } catch { /* ignore */ }
    }, [])

    const addToHistory = useCallback((text: string) => {
        if (!text || !text.trim()) return
        setHistory(prev => {
            const filtered = prev.filter(h => h.text !== text)
            const item: HistoryItem = {
                text,
                time: new Date().toLocaleString('zh-CN', {
                    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                }),
            }
            const next = [item, ...filtered].slice(0, HISTORY_MAX)
            persist(next)
            return next
        })
    }, [persist])

    const deleteHistoryItem = useCallback((index: number) => {
        setHistory(prev => {
            const next = prev.filter((_, i) => i !== index)
            persist(next)
            return next
        })
    }, [persist])

    const clearHistory = useCallback(() => {
        setHistory([])
        persist([])
    }, [persist])

    return { history, addToHistory, deleteHistoryItem, clearHistory }
}
