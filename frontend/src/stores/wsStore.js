import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useWsStore = defineStore('ws', () => {
  // 连接状态
  const isConnected = ref(false)
  // 重连次数
  const reconnectCount = ref(0)
  // 当前连接的 sessionId
  const activeSessionId = ref('')

  function setConnected(val) {
    isConnected.value = val
  }

  function setReconnectCount(val) {
    reconnectCount.value = val
  }

  function setActiveSessionId(id) {
    activeSessionId.value = id
  }

  return {
    isConnected,
    reconnectCount,
    activeSessionId,
    setConnected,
    setReconnectCount,
    setActiveSessionId
  }
})
