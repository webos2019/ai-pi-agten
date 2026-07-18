import React, { useRef, useEffect, useCallback, useImperativeHandle, forwardRef } from 'react'
import type { SlashCommand, AtReference, StructuredRequest } from '../types'

export interface AIInputEditorRef {
    clear: () => void
    setValue: (text: string) => void
    insertToolReference: (toolName: string) => void
    insertSkillReference: (skillName: string) => void
    insertDocReference: (docUri: string) => void
    getText: () => string
    getStructuredRequest: () => StructuredRequest
    isEmpty: () => boolean
}

interface Props {
    placeholder: string
    slashCommands: SlashCommand[]
    atReferences: AtReference[]
    onSend: (rawText: string, structured: StructuredRequest) => void
    onFileDrop: (files: FileList) => void
}

interface ChipData {
    id: string
    type: string
    label: string
    data: any
}

const AIInputEditor = forwardRef<AIInputEditorRef, Props>(({ placeholder, slashCommands, atReferences, onSend, onFileDrop }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null)
    const editorRef = useRef<HTMLDivElement>(null)
    const placeholderRef = useRef<HTMLDivElement>(null)
    const slashMenuRef = useRef<HTMLDivElement>(null)
    const atMenuRef = useRef<HTMLDivElement>(null)
    const chipsRef = useRef<ChipData[]>([])
    const rangeRef = useRef<Range | null>(null)
    const slashVisibleRef = useRef(false)
    const atVisibleRef = useRef(false)
    const selectedIdxRef = useRef(0)
    const filteredRef = useRef<(SlashCommand | AtReference)[]>([])

    // ─── Imperative handle ──────────────────────────────
    useImperativeHandle(ref, (): AIInputEditorRef => ({
        clear: () => {
            if (editorRef.current) {
                editorRef.current.innerHTML = ''
                chipsRef.current = []
                if (placeholderRef.current) placeholderRef.current.style.display = 'block'
                hideAllMenus()
            }
        },
        setValue: (text: string) => {
            if (editorRef.current) {
                editorRef.current.textContent = text
                updatePlaceholder()
            }
        },
        insertToolReference: (toolName: string) => {
            insertChip({ label: toolName, type: 'tool', desc: '', data: { toolName } })
        },
        insertSkillReference: (skillName: string) => {
            insertChip({ label: skillName, type: 'skill', desc: '', data: { skillName } })
        },
        insertDocReference: (docUri: string) => {
            insertChip({ label: docUri, type: 'doc', desc: '', data: { uri: docUri } })
        },
        getText: () => getText(),
        getStructuredRequest: () => getStructuredRequest(),
        isEmpty: () => !getText(),
    }))

    // ─── Core helpers ──────────────────────────────────
    const getText = useCallback((): string => {
        if (!editorRef.current) return ''
        const editor = editorRef.current
        let html = editor.innerHTML
        editor.querySelectorAll('.ai-inline-chip').forEach(chip => {
            const chipId = (chip as HTMLElement).dataset.chipId
            const chipData = chipsRef.current.find(c => c.id === chipId)
            if (chipData) {
                const placeholder = '@[' + chipData.type + ':' + chipData.label + ']'
                html = html.replace((chip as HTMLElement).outerHTML, placeholder)
            }
        })
        const temp = document.createElement('div')
        temp.innerHTML = html
        return temp.textContent?.trim() || ''
    }, [])

    const getStructuredRequest = useCallback((): StructuredRequest => {
        const segments: any[] = []
        const editor = editorRef.current
        if (!editor) return { rawText: '', segments: [], chips: [] }

        const processNode = (node: Node) => {
            if (node.nodeType === Node.TEXT_NODE) {
                const text = node.textContent?.trim() || ''
                if (text) segments.push({ type: 'text', content: text })
            } else if (node.nodeType === Node.ELEMENT_NODE) {
                const el = node as HTMLElement
                if (el.classList.contains('ai-inline-chip')) {
                    const chipId = el.dataset.chipId
                    const chipData = chipsRef.current.find(c => c.id === chipId)
                    if (chipData) {
                        segments.push({ type: 'chip', chipType: chipData.type, label: chipData.label, data: chipData.data })
                    }
                } else {
                    el.childNodes.forEach(child => processNode(child))
                }
            }
        }
        editor.childNodes.forEach(child => processNode(child))
        return {
            rawText: getText(),
            segments,
            chips: chipsRef.current.map(c => ({ type: c.type, label: c.label, data: c.data })),
        }
    }, [getText])

    const updatePlaceholder = useCallback(() => {
        if (placeholderRef.current) {
            placeholderRef.current.style.display = getText() ? 'none' : 'block'
        }
    }, [getText])

    // ─── Trigger detection ──────────────────────────────
    const getTextBeforeCursor = (range: Range): string => {
        const textNode = range.startContainer
        if (textNode.nodeType !== Node.TEXT_NODE) return ''
        const tempRange = document.createRange()
        tempRange.setStart(textNode, 0)
        tempRange.setEnd(range.startContainer, range.startOffset)
        return tempRange.toString()
    }

    const detectSlashTrigger = () => {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount || !sel.isCollapsed) return null
        const range = sel.getRangeAt(0)
        const textBefore = getTextBeforeCursor(range)
        if (!textBefore) return null
        const match = textBefore.match(/(?:^|\s)\/(\w*)$/)
        return match ? { query: match[1] } : null
    }

    const detectAtTrigger = () => {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount || !sel.isCollapsed) return null
        const range = sel.getRangeAt(0)
        const textBefore = getTextBeforeCursor(range)
        if (!textBefore) return null
        const match = textBefore.match(/(^|\s)@([\w:\/.\-]*)$/)
        return match ? { query: match[2] || '' } : null
    }

    // ─── Menu rendering ─────────────────────────────────
    const renderMenu = (menuEl: HTMLDivElement, items: (SlashCommand | AtReference)[], type: 'slash' | 'at', query: string) => {
        if (items.length === 0) { menuEl.style.display = 'none'; return }
        menuEl.innerHTML = ''
        menuEl.style.display = 'block'
        items.forEach((item, idx) => {
            const el = document.createElement('div')
            el.className = 'ai-menu-item' + (idx === selectedIdxRef.current ? ' selected' : '')
            const icon = (item as SlashCommand).icon || (type === 'slash' ? '⚡' : '📎')
            const label = item.label
            const desc = item.desc || ''
            const highlightIdx = query ? label.toLowerCase().indexOf(query.toLowerCase()) : -1
            let labelHtml = label
            if (highlightIdx >= 0) {
                labelHtml = label.substring(0, highlightIdx) + '<strong>' + label.substring(highlightIdx, highlightIdx + query.length) + '</strong>' + label.substring(highlightIdx + query.length)
            }
            el.innerHTML = '<span class="ai-menu-icon">' + icon + '</span><span class="ai-menu-label">' + labelHtml + '</span>' + (desc ? '<span class="ai-menu-desc">' + desc + '</span>' : '')
            el.addEventListener('click', (e) => {
                e.preventDefault()
                selectedIdxRef.current = idx
                selectMenuItem(idx)
            })
            el.addEventListener('mouseenter', () => {
                selectedIdxRef.current = idx
                updateMenuSelection(menuEl)
            })
            menuEl.appendChild(el)
        })
    }

    const updateMenuSelection = (menuEl: HTMLElement) => {
        const items = menuEl.querySelectorAll('.ai-menu-item')
        items.forEach((item, idx) => item.classList.toggle('selected', idx === selectedIdxRef.current))
        const selected = items[selectedIdxRef.current] as HTMLElement
        if (selected) selected.scrollIntoView({ block: 'nearest' })
    }

    const hideAllMenus = useCallback(() => {
        if (slashMenuRef.current) { slashMenuRef.current.style.display = 'none'; slashVisibleRef.current = false }
        if (atMenuRef.current) { atMenuRef.current.style.display = 'none'; atVisibleRef.current = false }
    }, [])

    // ─── Menu selection ─────────────────────────────────
    const selectMenuItem = (index: number) => {
        const item = filteredRef.current[index]
        if (!item) return
        if (slashVisibleRef.current) {
            removeTriggerText('/')
            ;(item as SlashCommand).action?.(getHandle())
        } else if (atVisibleRef.current) {
            removeTriggerText('@')
            insertChip(item as AtReference)
        }
        hideAllMenus()
    }

    // ─── Chip insertion ─────────────────────────────────
    const getChipIcon = (type: string): string => {
        const icons: Record<string, string> = { file: '📄', tool: '🔧', context: '📋', skill: '⚙', reference: '📎', weather: '🌤', location: '📍', doc: '📑', default: '📎' }
        return icons[type] || icons.default
    }

    const insertChip = (ref: AtReference) => {
        const chipId = 'chip_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6)
        const chipData: ChipData = { id: chipId, type: ref.type || 'reference', label: ref.label, data: (ref as any).data || ref }
        chipsRef.current.push(chipData)

        const chipEl = document.createElement('span')
        chipEl.className = 'ai-inline-chip'
        chipEl.contentEditable = 'false'
        chipEl.dataset.chipId = chipId
        chipEl.dataset.chipType = ref.type || 'reference'
        chipEl.innerHTML = '<span class="ai-chip-icon">' + getChipIcon(ref.type) + '</span><span class="ai-chip-label">' + ref.label + '</span><span class="ai-chip-remove" data-chip-id="' + chipId + '">×</span>'

        if (rangeRef.current) {
            const sel = window.getSelection()
            sel?.removeAllRanges()
            sel?.addRange(rangeRef.current)
        }
        const sel = window.getSelection()
        if (sel && sel.rangeCount) {
            const range = sel.getRangeAt(0)
            range.deleteContents()
            range.insertNode(chipEl)
            const space = document.createTextNode(' ')
            chipEl.after(space)
            range.setStartAfter(space)
            range.collapse(true)
            sel.removeAllRanges()
            sel.addRange(range)
        }

        const removeBtn = chipEl.querySelector('.ai-chip-remove')
        removeBtn?.addEventListener('click', (e) => {
            e.stopPropagation()
            chipsRef.current = chipsRef.current.filter(c => c.id !== chipId)
            chipEl.remove()
        })
    }

    // ─── Trigger text removal ───────────────────────────
    const removeTriggerText = (trigger: string) => {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount) return
        const range = sel.getRangeAt(0)
        const textNode = range.startContainer
        if (textNode.nodeType !== Node.TEXT_NODE) return
        const offset = range.startOffset
        const text = textNode.textContent || ''
        let startOffset = offset
        for (let i = offset - 1; i >= 0; i--) {
            const substr = text.substring(i, offset)
            if (substr.startsWith(trigger) && (i === 0 || text[i - 1] === ' ' || text[i - 1] === '\n')) {
                startOffset = i
                break
            }
            if (text[i] === ' ' || text[i] === '\n') break
        }
        range.setStart(textNode, startOffset)
        range.setEnd(textNode, offset)
        range.deleteContents()
    }

    // ─── Save/restore range ─────────────────────────────
    const saveRange = () => {
        const sel = window.getSelection()
        if (sel && sel.rangeCount) rangeRef.current = sel.getRangeAt(0).cloneRange()
    }

    // ─── Handle for slash commands ──────────────────────
    const getHandle = () => ({
        clear: () => { editorRef.current!.innerHTML = ''; chipsRef.current = []; updatePlaceholder(); hideAllMenus() },
        insertToolReference: (toolName: string) => insertChip({ label: toolName, type: 'tool', desc: '', data: { toolName } }),
        insertSkillReference: (skillName: string) => insertChip({ label: skillName, type: 'skill', desc: '', data: { skillName } }),
        insertDocReference: (docUri: string) => insertChip({ label: docUri, type: 'doc', desc: '', data: { uri: docUri } }),
        setValue: (text: string) => { if (editorRef.current) { editorRef.current.textContent = text; updatePlaceholder() } },
    })

    // ─── Event handlers ─────────────────────────────────
    const onInput = () => {
        updatePlaceholder()

        if (!slashVisibleRef.current) {
            const m = detectSlashTrigger()
            if (m) {
                saveRange()
                slashVisibleRef.current = true
                selectedIdxRef.current = 0
                filteredRef.current = slashCommands.filter(cmd =>
                    cmd.label.toLowerCase().includes(m.query.toLowerCase()) ||
                    (cmd.desc && cmd.desc.toLowerCase().includes(m.query.toLowerCase())) ||
                    (cmd.alias && cmd.alias.some(a => a.toLowerCase().includes(m.query.toLowerCase())))
                )
                if (slashMenuRef.current) renderMenu(slashMenuRef.current, filteredRef.current, 'slash', m.query)
                if (atMenuRef.current) atMenuRef.current.style.display = 'none'
                atVisibleRef.current = false
            }
        } else {
            const m = detectSlashTrigger()
            if (m) {
                selectedIdxRef.current = 0
                filteredRef.current = slashCommands.filter(cmd =>
                    cmd.label.toLowerCase().includes(m.query.toLowerCase()) ||
                    (cmd.desc && cmd.desc.toLowerCase().includes(m.query.toLowerCase())) ||
                    (cmd.alias && cmd.alias.some(a => a.toLowerCase().includes(m.query.toLowerCase())))
                )
                if (slashMenuRef.current) renderMenu(slashMenuRef.current, filteredRef.current, 'slash', m.query)
            } else {
                if (slashMenuRef.current) slashMenuRef.current.style.display = 'none'
                slashVisibleRef.current = false
            }
        }

        if (!atVisibleRef.current) {
            const m = detectAtTrigger()
            if (m) {
                saveRange()
                atVisibleRef.current = true
                selectedIdxRef.current = 0
                filteredRef.current = atReferences.filter(ref =>
                    ref.label.toLowerCase().includes(m.query.toLowerCase()) ||
                    (ref.keywords && ref.keywords.some(k => k.toLowerCase().includes(m.query.toLowerCase())))
                )
                if (atMenuRef.current) renderMenu(atMenuRef.current, filteredRef.current, 'at', m.query)
                if (slashMenuRef.current) slashMenuRef.current.style.display = 'none'
                slashVisibleRef.current = false
            }
        } else {
            const m = detectAtTrigger()
            if (m) {
                selectedIdxRef.current = 0
                filteredRef.current = atReferences.filter(ref =>
                    ref.label.toLowerCase().includes(m.query.toLowerCase()) ||
                    (ref.keywords && ref.keywords.some(k => k.toLowerCase().includes(m.query.toLowerCase())))
                )
                if (atMenuRef.current) renderMenu(atMenuRef.current, filteredRef.current, 'at', m.query)
            } else {
                if (atMenuRef.current) atMenuRef.current.style.display = 'none'
                atVisibleRef.current = false
            }
        }
    }

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (slashVisibleRef.current || atVisibleRef.current) {
            const menu = slashVisibleRef.current ? slashMenuRef.current : atMenuRef.current
            if (!menu) return
            const items = menu.querySelectorAll('.ai-menu-item')

            if (e.key === 'ArrowDown') { e.preventDefault(); selectedIdxRef.current = Math.min(selectedIdxRef.current + 1, items.length - 1); updateMenuSelection(menu); return }
            if (e.key === 'ArrowUp') { e.preventDefault(); selectedIdxRef.current = Math.max(selectedIdxRef.current - 1, 0); updateMenuSelection(menu); return }
            if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); selectMenuItem(selectedIdxRef.current); return }
            if (e.key === 'Escape') { e.preventDefault(); hideAllMenus(); return }
        }
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            const text = getText()
            if (text.trim()) onSend(text, getStructuredRequest())
        }
    }

    const onKeyUp = (e: React.KeyboardEvent) => {
        if (e.key === 'Backspace' || e.key === 'Delete') {
            const activeIds = new Set<string>()
            editorRef.current?.querySelectorAll('.ai-inline-chip').forEach(el => activeIds.add((el as HTMLElement).dataset.chipId || ''))
            chipsRef.current = chipsRef.current.filter(c => activeIds.has(c.id))
        }
    }

    const onPaste = (e: React.ClipboardEvent) => {
        e.preventDefault()
        const text = e.clipboardData.getData('text/plain')
        document.execCommand('insertText', false, text)
    }

    const onBlur = () => {
        setTimeout(() => {
            const slashHover = slashMenuRef.current?.matches(':hover')
            const atHover = atMenuRef.current?.matches(':hover')
            if (!slashHover && !atHover) hideAllMenus()
        }, 200)
    }

    const onDragOver = (e: React.DragEvent) => { e.preventDefault(); containerRef.current?.classList.add('dragover') }
    const onDragLeave = () => containerRef.current?.classList.remove('dragover')
    const onDrop = (e: React.DragEvent) => {
        e.preventDefault()
        containerRef.current?.classList.remove('dragover')
        if (onFileDrop && e.dataTransfer.files.length > 0) onFileDrop(e.dataTransfer.files)
    }

    return (
        <div
            ref={containerRef}
            className="ai-editor-container"
            onClick={() => editorRef.current?.focus()}
        >
            <div ref={placeholderRef} className="ai-editor-placeholder">{placeholder}</div>
            <div
                ref={editorRef}
                className="ai-editor-content"
                contentEditable={true}
                role="textbox"
                aria-multiline="true"
                tabIndex={0}
                onInput={onInput}
                onKeyDown={onKeyDown}
                onKeyUp={onKeyUp}
                onBlur={onBlur}
                onPaste={onPaste}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
            />
            <div ref={slashMenuRef} className="ai-slash-menu" style={{ display: 'none' }} />
            <div ref={atMenuRef} className="ai-at-menu" style={{ display: 'none' }} />
        </div>
    )
})

AIInputEditor.displayName = 'AIInputEditor'
export default AIInputEditor
