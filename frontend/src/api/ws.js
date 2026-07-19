import { useChatStore } from '@/stores/chatStore'
import { useWsStore } from '@/stores/wsStore'

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
    this._closeCode = null
    this._closeReason = ''
  }

  // 获取带 token 的 WebSocket URL
  _buildUrl(sessionId) {
    const wsStore = useWsStore()
    const base = wsStore.wsBaseUrl
    let url = `${base}/ws/chat/${sessionId}`
    if (wsStore.token) {
      url += `?token=${encodeURIComponent(wsStore.token)}`
    }
    return url
  }

  // 建立连接
  connect(sessionId) {
    if (this.sessionId === sessionId && this.ws && this.ws.readyState === WebSocket.OPEN) {
      return
    }
    // 防止旧连接的 onclose 异步触发 tryReconnect 连接到新 sessionId
    this.skipReconnect = true
    this.close(false)
    this.sessionId = sessionId
    this._closeCode = null
    this._closeReason = ''
    const url = this._buildUrl(sessionId)
    console.log('WebSocket 连接:', url.replace(/token=[^&]*/, 'token=***'))
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      console.log('WebSocket 已连接')
      this.skipReconnect = false
      const wsStore = useWsStore()
      wsStore.setConnected(true)
      wsStore.setReconnectCount(0)
      wsStore.setActiveSessionId(sessionId)
      this.startHeartbeat()
    }

    this.ws.onmessage = (event) => {
      this.handleMessage(event.data)
    }

    this.ws.onclose = (event) => {
      console.log('WebSocket 已断开', event.code, event.reason)
      this._closeCode = event.code
      this._closeReason = event.reason
      const wsStore = useWsStore()
      wsStore.setConnected(false)
      this.stopHeartbeat()
      // 通知上层连接已断开，便于中断"正在输出"等悬挂状态
      this.emit("close", { code: event.code, reason: event.reason, clean: event.wasClean })

      // 认证失败（4001）不重连，直接标记并通知用户配置 Token
      if (event.code === 4001 || event.reason === 'auth_failed') {
        wsStore.setAuthError(true)
        this.skipReconnect = true
        console.warn('Token 认证失败，请检查 API Token 配置')
        return
      }

      if (this.skipReconnect) {
        this.skipReconnect = false
        return
      }
      this.tryReconnect()
    }

    this.ws.onerror = (err) => {
      console.error('WebSocket 错误', err)
      // onerror 后通常 onclose 也会触发，这里仅做标记
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

  // 发送工具确认
  sendToolConfirm(toolConfirmId, approved = true) {
    const decision = approved === true || approved === 'approve' ? 'approve' : 'reject'
    this.send({ type: 'tool_confirm', tool_confirm_id: toolConfirmId, decision })
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
    const wsStore = useWsStore()

    switch (data.type) {
      case 'status':
        // 不写入消息列表，仅通过 wsStore 驱动顶部转圈加载条
        wsStore.processing = true
        wsStore.processingText = data.content || '处理中...'
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
      case 'fix_options':
        // 一键修复选项卡片；content 置空字符串是为了避免后续 chunk
        // 追加到该消息时出现 undefined 拼接
        chatStore.addMessage(this.sessionId, {
          role: 'assistant',
          type: 'fix_options',
          content: '',
          options: Array.isArray(data.options) ? data.options : [],
          traceId: data.trace_id
        })
        this.emit('fix_options', data)
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
        if (data.title) {
          const chatStore = useChatStore()
          chatStore.updateSessionTitle(this.sessionId, data.title)
        }
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
      case 'pending_confirmation':
        chatStore.addMessage(this.sessionId, {
          role: 'system',
          type: 'pending_confirmation',
          tool: data.tool,
          tool_confirm_id: data.tool_confirm_id,
          params: data.params,
          confirm_id: data.confirm_id,
          reason: data.reason,
          trace_id: data.trace_id,
        })
        this.emit('pending_confirmation', data)
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
      wsStore.setConnectionRefused(true)
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
