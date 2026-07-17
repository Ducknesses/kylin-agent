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

  // 创建新会话：优先在后端创建，失败时回退到本地临时 ID
  async function createSession() {
    const fallbackTitle = `新会话 ${sessions.value.length + 1}`
    try {
      const { data } = await http.post('/sessions', { title: fallbackTitle })
      const id = data.id
      currentSessionId.value = id
      sessions.value.unshift({
        id,
        title: data.title || fallbackTitle,
        createdAt: new Date(data.created_at).getTime() || Date.now()
      })
      messagesMap.value.set(id, [])
      return id
    } catch (e) {
      console.error('[ChatStore] 创建后端会话失败，回退到本地会话:', e)
      const id = generateId()
      currentSessionId.value = id
      sessions.value.unshift({
        id,
        title: fallbackTitle,
        createdAt: Date.now()
      })
      messagesMap.value.set(id, [])
      return id
    }
  }

  // 加载最近会话（刷新后恢复），没有则创建新会话
  async function loadLatestSession() {
    try {
      const { data } = await http.get('/sessions')
      if (data && data.length > 0) {
        // 后端已按 updated_at 倒序返回，data[0] 即最近使用的会话；
        // 不能再按 created_at 重排，否则会错误恢复到最新创建（通常为空）的会话
        sessions.value = data.map(s => ({
          id: s.id,
          title: s.title || `会话 ${s.id.slice(-6)}`,
          createdAt: new Date(s.created_at).getTime() || Date.now()
        }))
        const latest = data[0]
        currentSessionId.value = latest.id
        await fetchHistory(latest.id)
        return latest.id
      }
    } catch (e) {
      console.warn('[ChatStore] 加载最近会话失败:', e)
    }
    // 没有任何会话时创建新会话
    return await createSession()
  }

  // 从后端加载会话历史消息
  async function fetchHistory(sessionId) {
    if (historyLoaded.value.has(sessionId)) return
    historyLoaded.value.add(sessionId)
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
      } else {
        // 确保空消息列表被初始化，避免 currentMessages 返回 undefined
        if (!messagesMap.value.has(sessionId)) {
          messagesMap.value.set(sessionId, [])
        }
      }
    } catch (e) {
      console.error('[ChatStore] 加载历史消息失败:', sessionId, e)
      // 确保空消息列表被初始化，避免界面卡在加载状态
      if (!messagesMap.value.has(sessionId)) {
        messagesMap.value.set(sessionId, [])
      }
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
