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
      <!-- 输入参数：超长默认折叠 -->
      <template v-if="opt.params && Object.keys(opt.params).length > 0">
        <div class="code-section">
          <div class="code-label">
            <span class="label-text">输入参数</span>
            <el-button
              v-if="needsCollapse(paramsStr(opt))"
              link
              size="small"
              @click="toggleExpand(opt.option_id, 'params')"
            >
              {{ isExpanded(opt.option_id, 'params') ? '收起 ▲' : '展开 ▼' }}
            </el-button>
          </div>
          <pre
            class="code-block"
            :class="{
              'code-collapsed': needsCollapse(paramsStr(opt)) && !isExpanded(opt.option_id, 'params'),
              'code-expanded': isExpanded(opt.option_id, 'params')
            }"
          >{{ paramsStr(opt) }}</pre>
        </div>
      </template>
      <div v-if="opt.rollback" class="rollback">回滚：{{ opt.rollback }}</div>

      <el-alert
        v-if="stateOf(opt).message"
        :type="stateOf(opt).ok ? 'success' : 'error'"
        :title="stateOf(opt).message"
        :closable="false"
        show-icon
        class="result-alert"
      />
      <!-- 执行结果：超长默认折叠 -->
      <template v-if="stateOf(opt).summary">
        <div class="code-section">
          <div class="code-label">
            <span class="label-text">执行结果</span>
            <el-button
              v-if="needsCollapse(stateOf(opt).summary)"
              link
              size="small"
              @click="toggleExpand(opt.option_id, 'summary')"
            >
              {{ isExpanded(opt.option_id, 'summary') ? '收起 ▲' : '展开 ▼' }}
            </el-button>
          </div>
          <pre
            class="code-block result"
            :class="{
              'code-collapsed': needsCollapse(stateOf(opt).summary) && !isExpanded(opt.option_id, 'summary'),
              'code-expanded': isExpanded(opt.option_id, 'summary')
            }"
          >{{ stateOf(opt).summary }}</pre>
        </div>
      </template>

      <div class="option-actions">
        <el-button
          size="small"
          :type="buttonType(opt)"
          :loading="stateOf(opt).loading"
          :disabled="isButtonDisabled(opt)"
          @click="execute(opt)"
        >
          {{ buttonText(opt) }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, reactive } from 'vue'
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

// ── 代码块展开/收起状态 ─────────────────────────────────────────
// key: "{option_id}:params" 或 "{option_id}:summary"，value: true=展开
const expandState = reactive({})

function expandKey(optionId, section) {
  return `${optionId}:${section}`
}

function isExpanded(optionId, section) {
  return !!expandState[expandKey(optionId, section)]
}

function toggleExpand(optionId, section) {
  const key = expandKey(optionId, section)
  expandState[key] = !expandState[key]
}

// 行数超过阈值或总长度超过阈值时启用折叠
const COLLAPSE_LINES = 6
const COLLAPSE_CHARS = 300

function needsCollapse(text) {
  if (!text) return false
  const lines = text.split('\n').length
  return lines > COLLAPSE_LINES || text.length > COLLAPSE_CHARS
}

function paramsStr(opt) {
  return JSON.stringify(opt.params, null, 2)
}

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

// ── 从后端拉取选项状态并同步到本地 states ────────────────────────

const fetchedSessions = reactive(new Set())  // 已拉取过状态的 session，避免重复请求

async function fetchStatus() {
  const sid = chatStore.currentSessionId
  if (!sid) return
  if (fetchedSessions.has(sid)) return       // 同一 session 不重复拉取
  fetchedSessions.add(sid)

  try {
    const { data } = await http.get(`/actions/status/${sid}`)
    const remoteOptions = data.options || []
    for (const remote of remoteOptions) {
      // 只处理本卡片中存在的 option_id
      if (!options.value.find(o => o.option_id === remote.option_id)) continue

      // 如果本地已有终态状态（用户刚执行完），不覆盖，避免后台请求把成功消息冲掉
      const local = states[remote.option_id]
      if (local && local.done) continue

      let patch = {}
      if (remote.status === 'executed') {
        patch = { loading: false, done: true, ok: true, message: remote.message || '执行成功', summary: remote.result_summary || '' }
      } else if (remote.status === 'failed') {
        patch = { loading: false, done: true, ok: false, message: remote.message || '执行失败', summary: remote.result_summary || '' }
      } else if (remote.status === 'blocked') {
        patch = { loading: false, done: true, ok: false, message: remote.message || '已被阻断', summary: '' }
      } else if (remote.status === 'rolled_back') {
        patch = { loading: false, done: true, ok: true, message: remote.message || '已回滚', summary: remote.result_summary || '' }
      } else if (remote.status === 'expired') {
        patch = { loading: false, done: true, ok: false, message: remote.message || '已过期', summary: '' }
      } else {
        // pending / confirm_required / executing → 可操作
        patch = { loading: false, done: false, ok: false, message: '', summary: '' }
      }
      setState({ option_id: remote.option_id }, patch)
    }
  } catch (e) {
    console.warn('[FixOptionsCard] 拉取修复状态失败:', e.response?.status, e.response?.data?.detail || e.message)
  }
}

// 当 options 列表就绪时自动拉取状态（支持页面刷新、历史消息加载、异步数据更新）
watch(options, (newOpts) => {
  if (newOpts.length > 0) {
    fetchStatus()
  }
}, { immediate: true })

// ── 按钮禁用逻辑 ───────────────────────────────────────────────

function isButtonDisabled(opt) {
  const s = stateOf(opt)
  // 终态：已执行 / 失败 / 阻断 / 已回滚 / 已过期
  return s.done
}

function buttonText(opt) {
  const s = stateOf(opt)
  if (!s.done) return '执行'
  if (s.ok) return '已执行'
  return '执行失败'
}

function buttonType(opt) {
  const s = stateOf(opt)
  if (!s.done) return 'primary'
  if (s.ok) return 'success'
  return 'danger'
}

// ── 执行逻辑 ───────────────────────────────────────────────────

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
      const isDone = data.status === 'executed' || data.status === 'failed'
      setState(opt, {
        loading: false,
        done: isDone,
        ok: data.status === 'executed',
        message: data.message || (data.status === 'executed' ? '执行完成' : '执行失败'),
        summary: data.result_summary || ''
      })
    }
  } catch (e) {
    setState(opt, { loading: false, done: true, ok: false, message: errMsg(e) })
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
    const isDone = data.status === 'executed' || data.status === 'failed' || data.status === 'rejected'
    setState(opt, {
      loading: false,
      done: isDone,
      ok: data.status === 'executed',
      message: data.message || (approved ? '执行完成' : '已取消'),
      summary: data.result_summary || ''
    })
  } catch (e) {
    setState(opt, { loading: false, done: true, ok: false, message: errMsg(e) })
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
.code-section {
  margin-bottom: 6px;
}
.code-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
  padding: 0 2px;
}
.label-text {
  font-size: 12px;
  color: #6b7280;
  font-weight: 500;
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
  margin: 0 0 2px 0;
}
.code-block.result {
  background-color: #111827;
}
.code-block.code-collapsed {
  max-height: 120px;
  overflow: hidden;
  position: relative;
}
.code-block.code-collapsed::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 40px;
  background: linear-gradient(transparent, #1f2937);
  border-radius: 0 0 6px 6px;
}
.code-block.result.code-collapsed::after {
  background: linear-gradient(transparent, #111827);
}
.code-block.code-expanded {
  max-height: none;
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
