import { useChatStore } from '@/stores/chatStore'
import { useWsStore } from '@/stores/wsStore'

const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000'
const MAX_RECONNECT = 5
const HEARTBEAT_INTERVAL = 30000
const RECONNECT_DELAY = 3000

class WsClient {
  constructor() {
    this.ws = null
    this.sessionId = ''
    this.heartbeatTimer = null
    this.reconnectTimer = null
    this.handlers = new Map()
    this.pendingConfirms = new Map()
    this.skipReconnect = false
  }

  // 建立连接
  connect(sessionId) {
    if (this.sessionId === sessionId && this.ws && this.ws.readyState === WebSocket.OPEN) {
      return
    }
    this.close(false)
    this.sessionId = sessionId
    const url = `${WS_BASE_URL}/ws/chat/${sessionId}`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      console.log('WebSocket 已连接')
      const wsStore = useWsStore()
      wsStore.setConnected(true)
      wsStore.setReconnectCount(0)
      wsStore.setActiveSessionId(sessionId)
      this.startHeartbeat()
    }

    this.ws.onmessage = (event) => {
      this.handleMessage(event.data)
    }

    this.ws.onclose = () => {
      console.log('WebSocket 已断开')
      const wsStore = useWsStore()
      wsStore.setConnected(false)
      this.stopHeartbeat()
      if (this.skipReconnect) {
        this.skipReconnect = false
        return
      }
      this.tryReconnect()
    }

    this.ws.onerror = (err) => {
      console.error('WebSocket 错误', err)
    }
  }

  // 发送聊天消息
  sendChat(content) {
    this.send({ type: 'chat', content })
  }

  // 发送中危确认
  sendConfirm(confirmId, decision = 'approve') {
    this.send({ type: 'confirm', confirm_id: confirmId, decision })
  }

  send(payload) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload))
    } else {
      console.warn('WebSocket 未连接，无法发送消息')
    }
  }

  // 注册消息处理器
  on(type, handler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, [])
    }
    this.handlers.get(type).push(handler)
  }

  // 触发处理器
  emit(type, data) {
    const list = this.handlers.get(type) || []
    list.forEach(h => h(data))
  }

  // 解析消息
  handleMessage(raw) {
    let data
    try {
      data = JSON.parse(raw)
    } catch (e) {
      console.error('解析 WebSocket 消息失败', raw)
      return
    }

    const chatStore = useChatStore()

    switch (data.type) {
      case 'status':
        chatStore.addMessage(this.sessionId, {
          role: 'system',
          type: 'status',
          content: data.content || '处理中...'
        })
        this.emit('status', data)
        break
      case 'chunk':
        chatStore.appendToLastAssistant(this.sessionId, data.content || '')
        this.emit('chunk', data)
        break
      case 'tool_call':
        chatStore.addOrUpdateToolCall(this.sessionId, data)
        this.emit('tool_call', data)
        break
      case 'risk_alert':
        chatStore.addMessage(this.sessionId, {
          role: 'system',
          type: 'risk_alert',
          level: data.level,
          reason: data.reason,
          originalInput: data.original_input
        })
        this.emit('risk_alert', data)
        break
      case 'done':
        this.emit('done', data)
        break
      case 'error':
        chatStore.addMessage(this.sessionId, {
          role: 'assistant',
          type: 'error',
          content: data.message || '发生错误'
        })
        this.emit('error', data)
        break
      case 'pong':
        break
      default:
        console.log('未知消息类型', data)
    }
  }

  startHeartbeat() {
    this.stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' })
    }, HEARTBEAT_INTERVAL)
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  tryReconnect() {
    const wsStore = useWsStore()
    if (wsStore.reconnectCount >= MAX_RECONNECT) {
      console.warn('重连次数已达上限')
      return
    }
    wsStore.setReconnectCount(wsStore.reconnectCount + 1)
    this.reconnectTimer = setTimeout(() => {
      console.log(`第 ${wsStore.reconnectCount} 次重连...`)
      this.connect(this.sessionId)
    }, RECONNECT_DELAY)
  }

  close(stopReconnect = true) {
    this.stopHeartbeat()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (stopReconnect) {
      this.skipReconnect = true
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}

export const wsClient = new WsClient()
