import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

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
    switchSession,
    addMessage,
    appendToLastAssistant,
    addOrUpdateToolCall
  }
})
