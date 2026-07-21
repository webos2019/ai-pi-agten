/**
 * stableSnapshot — 稳定快照投影
 *
 * 设计要点（借鉴掘金文章 AI Mind v0.4.7）:
 *
 * 不是所有消息都能存——流式输出中的半成品、失败请求、pending 的 Agent 步骤
 * 不能也不该被持久化。刷新后恢复出来的，一定是"之前已经完整看到过的内容"。
 *
 * 过滤规则:
 * 1. 只保留 user 和 assistant 消息（system 消息不进入快照）
 * 2. 过滤掉空消息（content 为空且 blocks 也为空的 assistant 消息）
 * 3. agent_step 只保留 status=success/error 的（pending 不存）
 * 4. tool_call 没有对应 tool_result 的不完整对不存
 * 5. 最多 120 条，超出从最旧开始裁剪
 *
 * 提交时机（调用方控制）:
 * - 流式完成后提交
 * - 重新生成完成后提交
 * - 流式中/请求失败/用户中止时不提交，保留上一份成功快照
 */

import type { ChatMessage, ChatBlock } from '../types'

// ─── 可恢复的 block 类型白名单 ─────────────────────────

const RECOVERABLE_BLOCK_TYPES: string[] = [
    'text',
    'reasoning',
    'tool_call',
    'tool_result',
    'resource_start',
    'resource_end',
    'resource_error',
    'agent_step',
    'steer_queued',
    'steer_applied',
    'steer_rejected',
]

const MAX_MESSAGES = 120

// ─── 单条消息过滤 ──────────────────────────────────────

function isRecoverableBlock(block: ChatBlock): boolean {
    // block 类型必须在白名单内
    if (!RECOVERABLE_BLOCK_TYPES.includes(block.type)) {
        return false
    }

    // agent_step: pending 状态不存（只有 success/error 才是稳定的）
    if (block.type === 'agent_step') {
        if (block.status === 'pending') return false
    }

    return true
}

function projectMessage(msg: ChatMessage): ChatMessage | null {
    // 只保留 user 和 assistant 消息
    if (msg.role !== 'user' && msg.role !== 'assistant') {
        return null
    }

    // user 消息：直接保留（去掉 structured 等运行时状态）
    if (msg.role === 'user') {
        return {
            role: 'user',
            content: msg.content,
            files: msg.files,
        }
    }

    // assistant 消息：过滤 blocks
    const rawBlocks = msg.blocks || []
    const recoverableBlocks = rawBlocks.filter(isRecoverableBlock)

    // 如果有 blocks，用过滤后的；如果 content 为空且 blocks 也为空，丢弃
    const hasContent = msg.content && msg.content.trim()
    const hasBlocks = recoverableBlocks.length > 0

    if (!hasContent && !hasBlocks) {
        return null
    }

    return {
        role: 'assistant',
        content: msg.content,
        blocks: hasBlocks ? recoverableBlocks : undefined,
    }
}

// ─── 主入口: projectRecoverableMessages ────────────────

export function projectRecoverableMessages(
    messages: ChatMessage[],
): ChatMessage[] {
    const projected: ChatMessage[] = []

    for (const msg of messages) {
        const result = projectMessage(msg)
        if (result) {
            projected.push(result)
        }
    }

    // 过滤不完整的 tool_call 对：没有对应 tool_result 的 tool_call 不保留
    for (const msg of projected) {
        if (msg.role !== 'assistant' || !msg.blocks) continue

        // 收集所有有 serverId 的 tool_result
        const resultServerIds = new Set<string>()
        for (const block of msg.blocks) {
            if (block.type === 'tool_result' && block.serverId) {
                resultServerIds.add(block.serverId)
            }
        }

        // 过滤掉没有对应 tool_result 的 tool_call（除非没有 serverId 的）
        msg.blocks = msg.blocks.filter(block => {
            if (block.type === 'tool_call' && block.serverId) {
                return resultServerIds.has(block.serverId)
            }
            return true
        })

        // 如果过滤后 blocks 为空，删掉 blocks 字段
        if (msg.blocks.length === 0) {
            msg.blocks = undefined
        }
    }

    // 容量裁剪：最多 120 条，从最旧开始删
    if (projected.length > MAX_MESSAGES) {
        return projected.slice(projected.length - MAX_MESSAGES)
    }

    return projected
}
