<template>
  <div class="fix-options-card">
    <div class="card-header">
      <el-icon><MagicStick /></el-icon>
      <span class="card-title">一键修复选项（{{ options.length }}）</span>
    </div>

    <div v-if="options.length === 0" class="empty-options">修复选项数据已失效</div>

    <div v-for="opt in options" :key="opt.option_id" class="option-item">
      <div class="option-head">
        <span class="option-title">{{ opt.title }}</span>
        <el-tag :type="riskTagType(opt.risk_level)" size="small">{{ riskText(opt.risk_level) }}</el-tag>
      </div>
      <div class="option-desc">{{ opt.description }}</div>
      <div class="option-meta">
        <el-tag size="small" type="info">{{ opt.tool }}</el-tag>
        <span v-if="opt.requires_confirm" class="confirm-tip">执行前需二次确认</span>
      </div>
      <pre v-if="opt.params && Object.keys(opt.params).length > 0" class="code-block">{{ JSON.stringify(opt.params, null, 2) }}</pre>
      <div v-if="opt.rollback" class="rollback">回滚：{{ opt.rollback }}</div>

      <el-alert
        v-if="stateOf(opt).message"
        :type="stateOf(opt).ok ? 'success' : 'error'"
        :title="stateOf(opt).message"
        :closable="false"
        show-icon
        class="result-alert"
      />
      <pre v-if="stateOf(opt).summary" class="code-block result">{{ stateOf(opt).summary }}</pre>

      <div class="option-actions">
        <el-button
          size="small"
          type="primary"
          :loading="stateOf(opt).loading"
          :disabled="stateOf(opt).done"
          @click="execute(opt)"
        >
          {{ stateOf(opt).done ? '已执行' : '执行' }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { ElMessageBox } from 'element-plus'
import http from '@/api/http'
import { useChatStore } from '@/stores/chatStore'

const props = defineProps({
  data: {
    type: Object,
    required: true
  }
})

const chatStore = useChatStore()

// 归一化选项列表，过滤掉结构不完整的历史数据
const options = computed(() => {
  const list = Array.isArray(props.data.options) ? props.data.options : []
  return list.filter(o => o && o.option_id && o.title)
})

// 每个选项的执行状态：{ loading, done, ok, message, summary }
const states = reactive({})

function stateOf(opt) {
  return states[opt.option_id] || { loading: false, done: false, ok: false, message: '', summary: '' }
}

function setState(opt, patch) {
  states[opt.option_id] = { ...stateOf(opt), ...patch }
}

function riskTagType(level) {
  const map = { low: 'success', medium: 'warning', high: 'danger' }
  return map[level] || 'info'
}

function riskText(level) {
  const map = { low: '低危', medium: '中危', high: '高危' }
  return map[level] || level || '未知'
}

function errMsg(e) {
  return e.response?.data?.detail || e.message || '请求失败'
}

async function execute(opt) {
  setState(opt, { loading: true, message: '', summary: '' })
  try {
    const { data } = await http.post('/actions/execute', {
      session_id: chatStore.currentSessionId,
      option_id: opt.option_id
    })
    if (data.status === 'confirm_required') {
      setState(opt, { loading: false })
      await confirmAndRun(opt, data)
    } else {
      setState(opt, {
        loading: false,
        done: data.status === 'executed',
        ok: true,
        message: data.message || '执行完成',
        summary: data.result_summary || ''
      })
    }
  } catch (e) {
    setState(opt, { loading: false, ok: false, message: errMsg(e) })
  }
}

// 中危选项二次确认：approve 走 /actions/confirm 执行，取消则发送 reject 收口状态
async function confirmAndRun(opt, executeData) {
  let approved = false
  try {
    await ElMessageBox.confirm(
      `「${opt.title}」为${riskText(opt.risk_level)}操作。${executeData.message || ''}`,
      '二次确认',
      { confirmButtonText: '确认执行', cancelButtonText: '取消', type: 'warning' }
    )
    approved = true
  } catch (_) {
    approved = false
  }

  setState(opt, { loading: true })
  try {
    const { data } = await http.post('/actions/confirm', {
      session_id: chatStore.currentSessionId,
      confirm_id: executeData.confirm_id,
      decision: approved ? 'approve' : 'reject'
    })
    setState(opt, {
      loading: false,
      done: data.status === 'executed',
      ok: data.status === 'executed',
      message: data.message || (approved ? '执行完成' : '已取消'),
      summary: data.result_summary || ''
    })
  } catch (e) {
    setState(opt, { loading: false, ok: false, message: errMsg(e) })
  }
}
</script>

<style scoped>
.fix-options-card {
  background-color: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px;
  width: 100%;
  max-width: 600px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: bold;
  color: #374151;
  margin-bottom: 10px;
}
.card-title {
  font-size: 15px;
}
.option-item {
  border-top: 1px solid #e5e7eb;
  padding: 10px 0;
}
.option-item:first-of-type {
  border-top: none;
  padding-top: 0;
}
.option-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.option-title {
  font-weight: 600;
  color: #1f2937;
}
.option-desc {
  font-size: 13px;
  color: #4b5563;
  margin: 6px 0;
  line-height: 1.5;
}
.option-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.confirm-tip {
  font-size: 12px;
  color: #d97706;
}
.code-block {
  background-color: #1f2937;
  color: #e5e7eb;
  padding: 10px;
  border-radius: 6px;
  font-size: 13px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0 0 6px 0;
}
.code-block.result {
  background-color: #111827;
}
.rollback {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
}
.result-alert {
  margin: 6px 0;
}
.option-actions {
  display: flex;
  justify-content: flex-end;
}
.empty-options {
  font-size: 13px;
  color: #9ca3af;
}
</style>
