<template>
  <div :class="['msg-row', roleClass]">
    <div class="avatar">
      <el-icon :size="22">
        <UserFilled v-if="props.msg.role === 'user'" />
        <Cpu v-else-if="props.msg.role === 'tool'" />
        <Warning v-else-if="props.msg.type === 'risk_alert'" />
        <ChatLineRound v-else />
      </el-icon>
    </div>
    <div class="bubble">
      <div v-if="props.msg.role === 'user'" class="user-text">{{ props.msg.content }}</div>
      <div v-else-if="props.msg.type === 'risk_alert'" class="risk-body">
        <div class="risk-title">⚠️ 风险拦截：{{ levelText(props.msg.level) }}</div>
        <div class="risk-reason">{{ props.msg.reason }}</div>
      </div>
      <div v-else-if="props.msg.type === 'error'" class="error-body">{{ props.msg.content }}</div>
      <ToolCallCard v-else-if="props.msg.role === 'tool'" :data="props.msg" />
      <div v-else class="assistant-text" v-html="renderMarkdown(props.msg.content)" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import ToolCallCard from './ToolCallCard.vue'

const props = defineProps({
  msg: {
    type: Object,
    required: true
  }
})

const roleClass = computed(() => {
  if (props.msg.role === 'user') return 'user'
  if (props.msg.role === 'tool') return 'tool'
  if (props.msg.type === 'risk_alert') return 'risk'
  return 'assistant'
})

function levelText(level) {
  const map = { high: '高危', medium: '中危', low: '低危' }
  return map[level] || level || '未知'
}

function renderMarkdown(text) {
  return marked.parse(text || '', { breaks: true })
}
</script>

<style scoped>
.msg-row {
  display: flex;
  margin-bottom: 16px;
  gap: 10px;
}
.msg-row.user {
  flex-direction: row-reverse;
}
.msg-row.tool,
.msg-row.risk {
  justify-content: center;
}
.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background-color: #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.user .avatar {
  background-color: #3b82f6;
  color: #fff;
}
.risk .avatar {
  background-color: #ef4444;
  color: #fff;
}
.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 10px;
  background-color: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  line-height: 1.6;
}
.user .bubble {
  background-color: #3b82f6;
  color: #fff;
}
.tool .bubble,
.risk .bubble {
  max-width: 80%;
  background-color: transparent;
  box-shadow: none;
  padding: 0;
}
.risk-body {
  background-color: #fee2e2;
  color: #991b1b;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #fecaca;
}
.risk-title {
  font-weight: bold;
  margin-bottom: 6px;
}
.error-body {
  background-color: #fee2e2;
  color: #991b1b;
  padding: 10px 14px;
  border-radius: 8px;
}
.assistant-text :deep(p) {
  margin: 0 0 8px 0;
}
.assistant-text :deep(pre) {
  background-color: #1f2937;
  color: #e5e7eb;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}
</style>
