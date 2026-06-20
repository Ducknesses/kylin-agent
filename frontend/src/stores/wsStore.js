import { defineStore } from 'pinia'
import { ref } from 'vue'

const DEFAULT_WS_URL = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000'
const DEFAULT_API_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export const useWsStore = defineStore('ws', () => {
  // 连接状态
  const isConnected = ref(false)
  // 重连次数
  const reconnectCount = ref(0)
  // 当前连接的 sessionId
  const activeSessionId = ref('')
  // Token 认证
  const token = ref(localStorage.getItem('api_token') || '')
  // WebSocket 连接地址（localStorage 优先，环境变量兜底）
  const wsBaseUrl = ref(localStorage.getItem('ws_base_url') || DEFAULT_WS_URL)
  // HTTP API 地址
  const apiBaseUrl = ref(localStorage.getItem('api_base_url') || DEFAULT_API_URL)
  // 是否因认证失败导致断连
  const authError = ref(false)
  // 是否因连接被拒/超时导致断连
  const connectionRefused = ref(false)

  function setConnected(val) {
    isConnected.value = val
    if (val) {
      // 连接成功后清除错误标记
      authError.value = false
      connectionRefused.value = false
    }
  }

  function setReconnectCount(val) {
    reconnectCount.value = val
  }

  function setActiveSessionId(id) {
    activeSessionId.value = id
  }

  function setToken(val) {
    token.value = val
    localStorage.setItem('api_token', val)
  }

  function clearToken() {
    token.value = ''
    localStorage.removeItem('api_token')
  }

  function setWsBaseUrl(val) {
    wsBaseUrl.value = val
    localStorage.setItem('ws_base_url', val)
  }

  function setApiBaseUrl(val) {
    apiBaseUrl.value = val
    localStorage.setItem('api_base_url', val)
  }

  function setAuthError(val) {
    authError.value = val
  }

  function setConnectionRefused(val) {
    connectionRefused.value = val
  }

  /** 重置所有配置到默认值 */
  function resetToDefaults() {
    setWsBaseUrl(DEFAULT_WS_URL)
    setApiBaseUrl(DEFAULT_API_URL)
    clearToken()
  }

  return {
    isConnected,
    reconnectCount,
    activeSessionId,
    token,
    wsBaseUrl,
    apiBaseUrl,
    authError,
    connectionRefused,
    setConnected,
    setReconnectCount,
    setActiveSessionId,
    setToken,
    clearToken,
    setWsBaseUrl,
    setApiBaseUrl,
    setAuthError,
    setConnectionRefused,
    resetToDefaults
  }
})
