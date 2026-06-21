<template>
  <div class="chat-panel">
    <div class="chat-header">
      <span class="title">智能运维助手</span>
      <div class="header-actions">
        <el-tag :type="connectionTagType" size="small">
          {{ connectionTagText }}
        </el-tag>
        <el-button link type="primary" size="small" @click="openSettings">
          <el-icon><Setting /></el-icon>
        </el-button>
        <el-button link type="primary" size="small" @click="injectMockData">
          注入测试数据
        </el-button>
      </div>
    </div>

    <!-- 连接失败提示 -->
    <div v-if="showConnectionBanner" class="connection-banner">
      <el-alert
        :title="connectionBannerTitle"
        :description="connectionBannerDesc"
        type="warning"
        show-icon
        :closable="false"
      >
        <template #default>
          <el-button size="small" type="primary" @click="openSettings">打开连接设置</el-button>
        </template>
      </el-alert>
    </div>

    <div ref="msgListRef" class="message-list">
      <div v-if="chatStore.currentMessages.length === 0 && !showConnectionBanner" class="empty-tip">
        <el-icon :size="40" color="#9ca3af"><ChatLineRound /></el-icon>
        <p>请输入运维问题，例如：查看CPU使用率</p>
      </div>
      <div v-else-if="chatStore.currentMessages.length === 0 && showConnectionBanner" class="empty-tip">
        <el-icon :size="40" color="#f59e0b"><WarningFilled /></el-icon>
        <p class="tip-warning">无法连接到后端服务器</p>
        <p class="tip-sub">请检查连接地址和 Token 配置</p>
      </div>
      <MsgBubble
        v-for="(msg, index) in chatStore.currentMessages"
        :key="index"
        :msg="msg"
      />
    </div>

    <div class="input-area">
      <el-input
        v-model="inputText"
        type="textarea"
        :rows="3"
        placeholder="请输入您的问题..."
        resize="none"
        @keydown.enter.prevent="handleEnter"
      />
      <el-button
        type="primary"
        :disabled="!canSend"
        :loading="isStreaming"
        @click="sendMessage"
      >
        发送
      </el-button>
    </div>

    <RiskAlert
      v-model="riskDialogVisible"
      :level="currentRisk.level"
      :reason="currentRisk.reason"
      :original-input="currentRisk.originalInput"
      :confirm-id="currentRisk.confirmId"
      @confirm="handleRiskConfirm"
      @cancel="handleRiskCancel"
    />

    <ConnectionSettings v-model="settingsVisible" />
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Setting, WarningFilled } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useWsStore } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'
import MsgBubble from './MsgBubble.vue'
import RiskAlert from './RiskAlert.vue'
import ConnectionSettings from './ConnectionSettings.vue'

const chatStore = useChatStore()
const wsStore = useWsStore()

const inputText = ref('')
const isStreaming = ref(false)
const msgListRef = ref(null)
const riskDialogVisible = ref(false)
const settingsVisible = ref(false)
const currentRisk = ref({ level: 'high', reason: '', originalInput: '', confirmId: '' })

const canSend = computed(() => {
  return inputText.value.trim().length > 0 && wsStore.isConnected && !isStreaming.value
})

// 连接标签
const connectionTagType = computed(() => {
  if (wsStore.isConnected) return 'success'
  if (wsStore.authError || wsStore.connectionRefused) return 'danger'
  return 'warning'
})

const connectionTagText = computed(() => {
  if (wsStore.isConnected) return '已连接'
  if (wsStore.authError) return '认证失败'
  if (wsStore.connectionRefused) return '连接失败'
  return '未连接'
})

// 连接失败横幅
const showConnectionBanner = computed(() => {
  return !wsStore.isConnected && (wsStore.authError || wsStore.connectionRefused ||
    (!wsStore.isConnected && wsStore.reconnectCount >= 5))
})

const connectionBannerTitle = computed(() => {
  if (wsStore.authError) return 'Token 认证失败'
  return '无法连接到后端服务器'
})

const connectionBannerDesc = computed(() => {
  if (wsStore.authError) return 'API Token 不正确或未配置，请联系管理员获取正确的 Token。'
  return '请检查后端服务是否已启动，以及连接地址是否正确。当前地址: ' + wsStore.wsBaseUrl
})

function openSettings() {
  settingsVisible.value = true
}

// 初始化会话与连接
onMounted(() => {
  const sessionId = chatStore.createSession()
  wsClient.connect(sessionId)
  wsClient.on('risk_alert', onRiskAlert)
  wsClient.on('done', () => { isStreaming.value = false })
  wsClient.on('error', () => { isStreaming.value = false })
})

onUnmounted(() => {
  wsClient.close()
})

function handleEnter(e) {
  if (!e.shiftKey) {
    sendMessage()
  } else {
    inputText.value += '\n'
  }
}

function sendMessage() {
  const text = inputText.value.trim()
  if (!text || !wsStore.isConnected) return

  chatStore.addMessage(chatStore.currentSessionId, {
    role: 'user',
    type: 'text',
    content: text
  })

  // 预置一条空的 assistant 消息，用于流式追加
  chatStore.addMessage(chatStore.currentSessionId, {
    role: 'assistant',
    type: 'text',
    content: ''
  })

  isStreaming.value = true
  wsClient.sendChat(text)
  inputText.value = ''
  scrollToBottom()
}

function onRiskAlert(data) {
  isStreaming.value = false
  currentRisk.value = {
    level: data.level || 'high',
    reason: data.reason || '检测到风险操作',
    originalInput: data.original_input || '',
    confirmId: data.confirm_id || ''
  }
  riskDialogVisible.value = true
}

function handleRiskConfirm({ confirmId }) {
  wsClient.sendConfirm(confirmId, 'approve')
}

function handleRiskCancel() {
  riskDialogVisible.value = false
}

// 注入测试数据，用于无后端时测试 UI
function injectMockData() {
  const sid = chatStore.currentSessionId
  if (!sid) return

  chatStore.addMessage(sid, {
    role: 'user',
    type: 'text',
    content: '帮我查看一下系统状态，并删除所有日志'
  })

  chatStore.addMessage(sid, {
    role: 'assistant',
    type: 'text',
    content: ''
  })

  const chunks = ['正在检查系统状态...\n', 'CPU 使用率 15%，内存 42%。\n', '发现日志目录较大。']
  let i = 0
  const timer = setInterval(() => {
    chatStore.appendToLastAssistant(sid, chunks[i])
    i++
    if (i >= chunks.length) {
      clearInterval(timer)
      // 模拟工具调用
      chatStore.addOrUpdateToolCall(sid, {
        tool: 'sys_info',
        tool_call_id: 'mock-1',
        params: { metric: 'cpu' },
        result: { usage: '15%', cores: 8 }
      })
      // 模拟高危拦截
      chatStore.addMessage(sid, {
        role: 'system',
        type: 'risk_alert',
        level: 'high',
        reason: '检测到危险命令：rm -rf /',
        originalInput: 'rm -rf /'
      })
      currentRisk.value = {
        level: 'high',
        reason: '检测到危险命令：rm -rf /',
        originalInput: 'rm -rf /',
        confirmId: 'mock-confirm-1'
      }
      riskDialogVisible.value = true
    }
  }, 400)
}

// 消息变化时自动滚动
watch(() => chatStore.currentMessages.length, () => {
  nextTick(scrollToBottom)
})

watch(() => chatStore.currentMessages, () => {
  nextTick(scrollToBottom)
}, { deep: true })

function scrollToBottom() {
  if (msgListRef.value) {
    msgListRef.value.scrollTop = msgListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.chat-header {
  height: 56px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e5e7eb;
}
.title {
  font-size: 16px;
  font-weight: bold;
  color: #1f2937;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background-color: #f9fafb;
}
.empty-tip {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
}
.input-area {
  padding: 16px 20px;
  border-top: 1px solid #e5e7eb;
  display: flex;
  gap: 12px;
  align-items: flex-end;
  background-color: #fff;
}
.input-area :deep(.el-textarea__inner) {
  resize: none;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.connection-banner {
  padding: 8px 16px;
  background-color: #fffbeb;
  border-bottom: 1px solid #fde68a;
}
.tip-warning {
  color: #f59e0b;
  font-weight: 500;
}
.tip-sub {
  color: #9ca3af;
  font-size: 13px;
  margin-top: 4px;
}
</style>
