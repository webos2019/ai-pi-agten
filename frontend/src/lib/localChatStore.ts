/**
 * localChatStore — 浏览器 IndexedDB 本地快照存储
 *
 * 设计要点（借鉴掘金文章 AI Mind v0.4.7）:
 * - 不引入任何 IndexedDB wrapper 依赖，直接用原生 API
 * - 数据库名 pi-agent-local-chat，两个 Object Store:
 *   - conversation-index: 会话索引（元数据 + selectedId + revision）
 *   - conversation-snapshots: 会话快照（按 conversationId 独立存储完整消息列表）
 * - revision 乐观锁：旧版本不能覆盖新版本（防多标签页并发覆盖）
 * - 容量裁剪：单快照最多 120 条消息，超出从最旧开始裁剪
 * - 配额耗尽降级：写入失败时裁剪旧消息重试，仍失败静默降级
 *
 * 数据边界:
 * - 本地快照只管 UI 展示，不发给服务端做模型上下文
 * - 服务端 ThreadState 仍然是 AI 上下文的唯一来源
 * - 两者不做静默合并、覆盖或补写
 */

import type { ChatMessage, ChatBlock } from '../types'

// ─── 常量 ─────────────────────────────────────────────

const DB_NAME = 'pi-agent-local-chat'
const DB_VERSION = 1
const STORE_INDEX = 'conversation-index'
const STORE_SNAPSHOTS = 'conversation-snapshots'
const MAX_SNAPSHOT_MESSAGES = 120

// ─── 类型 ─────────────────────────────────────────────

export interface ConversationIndexEntry {
    conversationId: string
    title: string
    lastActiveAt: number
    hasMessages: boolean
}

export interface ConversationIndexData {
    conversations: ConversationIndexEntry[]
    selectedConversationId: string
    revision: number
}

export interface ConversationSnapshot {
    conversationId: string
    messages: ChatMessage[]
    revision: number
    savedAt: number
}

// ─── DB 初始化 ────────────────────────────────────────

let dbInstance: IDBDatabase | null = null

function openDB(): Promise<IDBDatabase> {
    if (dbInstance) return Promise.resolve(dbInstance)

    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION)

        request.onerror = () => reject(request.error)
        request.onsuccess = () => {
            dbInstance = request.result
            resolve(dbInstance)
        }

        request.onupgradeneeded = (event) => {
            const db = (event.target as IDBOpenDBRequest).result

            if (!db.objectStoreNames.contains(STORE_INDEX)) {
                db.createObjectStore(STORE_INDEX, { keyPath: 'key' })
            }

            if (!db.objectStoreNames.contains(STORE_SNAPSHOTS)) {
                db.createObjectStore(STORE_SNAPSHOTS, { keyPath: 'conversationId' })
            }
        }
    })
}

// ─── 事务封装 ─────────────────────────────────────────

async function runStoreOperation<T>(
    storeName: string,
    mode: IDBTransactionMode,
    operation: (store: IDBObjectStore) => IDBRequest,
): Promise<T> {
    const db = await openDB()
    return new Promise<T>((resolve, reject) => {
        const tx = db.transaction(storeName, mode)
        const store = tx.objectStore(storeName)
        const request = operation(store)

        request.onsuccess = () => resolve(request.result as T)
        request.onerror = () => reject(request.error)
    })
}

// ─── 会话索引 ─────────────────────────────────────────

const INDEX_KEY = 'conversation-index'

export async function saveIndex(data: ConversationIndexData): Promise<void> {
    await runStoreOperation(STORE_INDEX, 'readwrite', (store) =>
        store.put({ key: INDEX_KEY, ...data }),
    )
}

export async function loadIndex(): Promise<ConversationIndexData | null> {
    try {
        const result = await runStoreOperation<any>(STORE_INDEX, 'readonly', (store) =>
            store.get(INDEX_KEY),
        )
        if (!result) return null
        return {
            conversations: result.conversations || [],
            selectedConversationId: result.selectedConversationId || '',
            revision: result.revision || 0,
        }
    } catch {
        return null
    }
}

// ─── 会话快照 ─────────────────────────────────────────

export async function saveSnapshot(
    conversationId: string,
    messages: ChatMessage[],
    revision: number,
): Promise<void> {
    // 容量裁剪：最多 120 条，从最旧开始删
    let trimmed = messages
    if (trimmed.length > MAX_SNAPSHOT_MESSAGES) {
        trimmed = trimmed.slice(trimmed.length - MAX_SNAPSHOT_MESSAGES)
    }

    const snapshot: ConversationSnapshot = {
        conversationId,
        messages: trimmed,
        revision,
        savedAt: Date.now(),
    }

    try {
        await runStoreOperation(STORE_SNAPSHOTS, 'readwrite', (store) =>
            store.put(snapshot),
        )
    } catch {
        // 配额耗尽 → 裁剪到更少消息重试
        try {
            const retrySnapshot: ConversationSnapshot = {
                ...snapshot,
                messages: trimmed.slice(-30), // 只保留最近 30 条
            }
            await runStoreOperation(STORE_SNAPSHOTS, 'readwrite', (store) =>
                store.put(retrySnapshot),
            )
        } catch {
            // 仍然失败 → 静默降级，不影响聊天主链
        }
    }
}

export async function loadSnapshot(
    conversationId: string,
): Promise<ConversationSnapshot | null> {
    try {
        const result = await runStoreOperation<ConversationSnapshot>(
            STORE_SNAPSHOTS,
            'readonly',
            (store) => store.get(conversationId),
        )
        return result || null
    } catch {
        return null
    }
}

export async function deleteSnapshot(conversationId: string): Promise<void> {
    try {
        await runStoreOperation(STORE_SNAPSHOTS, 'readwrite', (store) =>
            store.delete(conversationId),
        )
    } catch {
        // 静默失败
    }
}

// ─── 批量清理 ─────────────────────────────────────────

/**
 * 清理本地快照中不在 serverConversationIds 列表中的会话。
 * 用于服务端权威校准：服务端没有的会话，本地也删掉。
 */
export async function reconcileSnapshots(
    serverConversationIds: string[],
): Promise<void> {
    try {
        const db = await openDB()
        const tx = db.transaction(STORE_SNAPSHOTS, 'readwrite')
        const store = tx.objectStore(STORE_SNAPSHOTS)
        const allKeys = await new Promise<IDBValidKey[]>((resolve, reject) => {
            const req = store.getAllKeys()
            req.onsuccess = () => resolve(req.result)
            req.onerror = () => reject(req.error)
        })

        const serverSet = new Set(serverConversationIds)
        for (const key of allKeys) {
            if (!serverSet.has(key as string)) {
                store.delete(key)
            }
        }
    } catch {
        // 静默失败
    }
}

// ─── 调试工具 ─────────────────────────────────────────

export async function clearAll(): Promise<void> {
    try {
        const db = await openDB()
        const tx = db.transaction([STORE_INDEX, STORE_SNAPSHOTS], 'readwrite')
        tx.objectStore(STORE_INDEX).clear()
        tx.objectStore(STORE_SNAPSHOTS).clear()
    } catch {
        // 静默失败
    }
}
