import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/api/http'

function generateId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export const useChatStore = defineStore('chat', () => {
  // 会话列表
  const sessions = ref([])
  // 当前会话ID
  const currentSessionId = ref('')
  // 消息记录，按 sessionId 分组
  const messagesMap = ref(new Map())
  // 历史加载状态
  const historyLoaded = ref(new Set())

  const currentMessages = computed(() => {
    if (!currentSessionId.value) return []
    return messagesMap.value.get(currentSessionId.value) || []
  })

  // 创建新会话
  function createSession() {
    const id = generateId()
    currentSessionId.value = id
    sessions.value.unshift({
      id,
      title: `新会话 ${sessions.value.length + 1}`,
      createdAt: Date.now()
    })
    messagesMap.value.set(id, [])
    return id
  }

  // 加载最近会话（刷新后恢复），没有则创建新会话
  async function loadLatestSession() {
    try {
      const { data } = await http.get('/sessions')
      if (data && data.length > 0) {
        const sorted = [...data].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        const latest = sorted[0]
        currentSessionId.value = latest.id
        // 确保会话列表中包含最近会话
        if (!sessions.value.find(s => s.id === latest.id)) {
          sessions.value.unshift({
            id: latest.id,
            title: latest.title || `会话 ${latest.id.slice(-6)}`,
            createdAt: new Date(latest.created_at).getTime() || Date.now()
          })
        }
        await fetchHistory(latest.id)
        return latest.id
      }
    } catch (e) {
      console.warn('[ChatStore] 加载最近会话失败:', e)
    }
    // 没有任何会话时创建新会话
    return createSession()
  }

  // 从后端加载会话历史消息
  async function fetchHistory(sessionId) {
    if (historyLoaded.value.has(sessionId)) return
    try {
      const { data } = await http.get(`/sessions/${sessionId}/messages`)
      if (data.messages && data.messages.length > 0) {
        const msgs = data.messages.map(m => ({
          role: m.role,
          type: m.tool_calls ? 'tool_call' : 'text',
          content: m.content,
          timestamp: m.timestamp,
          tool_calls: m.tool_calls || undefined,
        }))
        messagesMap.value.set(sessionId, msgs)
      }
      historyLoaded.value.add(sessionId)
    } catch (e) {
      // 会话不存在或无消息时静默
      console.debug('[ChatStore] 历史加载: 无已有消息或会话不存在', sessionId)
    }
  }

  // 切换会话
  function switchSession(id) {
    currentSessionId.value = id
  }

  // 添加消息
  function addMessage(sessionId, msg) {
    const list = messagesMap.value.get(sessionId) || []
    list.push(msg)
    messagesMap.value.set(sessionId, list)
  }

  // 追加到当前最后一条 assistant 消息
  function appendToLastAssistant(sessionId, content) {
    const list = messagesMap.value.get(sessionId) || []
    const last = list[list.length - 1]
    if (last && last.role === 'assistant') {
      last.content += content
    } else {
      list.push({ role: 'assistant', type: 'text', content })
    }
    messagesMap.value.set(sessionId, [...list])
  }

  // 替换或添加工具调用消息
  function addOrUpdateToolCall(sessionId, payload) {
    const list = messagesMap.value.get(sessionId) || []
    const key = payload.tool_call_id || `${payload.tool}-${Date.now()}`
    const idx = list.findIndex(m => m.toolCallId === key)
    const msg = {
      role: 'tool',
      type: 'tool_call',
      toolCallId: key,
      tool: payload.tool,
      params: payload.params,
      result: payload.result
    }
    if (idx >= 0) {
      list[idx] = msg
    } else {
      list.push(msg)
    }
    messagesMap.value.set(sessionId, [...list])
  }

  return {
    sessions,
    currentSessionId,
    currentMessages,
    createSession,
    loadLatestSession,
    fetchHistory,
    switchSession,
    addMessage,
    appendToLastAssistant,
    addOrUpdateToolCall
  }
})
