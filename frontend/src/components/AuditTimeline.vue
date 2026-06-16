<template>
  <div class="audit-panel">
    <div class="audit-header">
      <span class="title">审计日志</span>
      <div class="filters">
        <el-select v-model="filterLevel" placeholder="风险等级" clearable size="small" style="width: 120px">
          <el-option label="高危" value="high" />
          <el-option label="中危" value="medium" />
          <el-option label="低危" value="low" />
        </el-select>
        <el-button type="primary" size="small" @click="fetchAudit">刷新</el-button>
      </div>
    </div>

    <el-timeline class="timeline">
      <el-timeline-item
        v-for="item in filteredList"
        :key="item.trace_id"
        :type="timelineType(item.risk_level)"
        :timestamp="formatTime(item.timestamp)"
        placement="top"
      >
        <el-card shadow="hover" class="audit-card">
          <template #header>
            <div class="card-header">
              <span>{{ item.intent || '对话记录' }}</span>
              <el-tag :type="tagType(item.risk_level)" size="small">{{ levelText(item.risk_level) }}</el-tag>
            </div>
          </template>
          <div class="audit-detail">
            <p><strong>用户输入：</strong>{{ item.user_input || '-' }}</p>
            <p><strong>工具：</strong>{{ item.mcp_tool || '-' }}</p>
            <p><strong>执行命令：</strong><code>{{ item.command || '-' }}</code></p>
            <p><strong>原始输出：</strong><pre class="raw-output">{{ item.raw_output || '-' }}</pre></p>
            <p><strong>最终回复：</strong>{{ item.final_response || '-' }}</p>
          </div>
        </el-card>
      </el-timeline-item>
    </el-timeline>

    <div v-if="filteredList.length === 0" class="empty">暂无审计记录</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const auditList = ref([])
const filterLevel = ref('')

const filteredList = computed(() => {
  if (!filterLevel.value) return auditList.value
  return auditList.value.filter(item => item.risk_level === filterLevel.value)
})

async function fetchAudit() {
  try {
    const res = await axios.get('/api/audit?limit=50')
    auditList.value = res.data || []
  } catch (e) {
    console.error('拉取审计日志失败', e)
    // fallback mock 数据
    auditList.value = [
      { trace_id: 'mock-1', timestamp: new Date().toISOString(), user_input: '查看CPU', intent: 'tool_call', risk_level: 'low', mcp_tool: 'sys_info', command: 'sys_info metric=cpu', raw_output: '{"cpu_percent": 15.2}', final_response: 'CPU 使用率 15%' }
    ]
  }
}

function formatTime(ts) {
  if (!ts) return '-'
  return new Date(ts).toLocaleString()
}

function levelText(level) {
  const map = { high: '高危', medium: '中危', low: '低危' }
  return map[level] || level || '未知'
}

function tagType(level) {
  const map = { high: 'danger', medium: 'warning', low: 'success' }
  return map[level] || 'info'
}

function timelineType(level) {
  const map = { high: 'danger', medium: 'warning', low: 'success' }
  return map[level] || 'primary'
}

onMounted(fetchAudit)
</script>

<style scoped>
.audit-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  padding: 16px;
}
.audit-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.filters {
  display: flex;
  gap: 10px;
}
.title {
  font-size: 16px;
  font-weight: bold;
  color: #1f2937;
}
.timeline {
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}
.audit-card {
  margin-bottom: 8px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.audit-detail p {
  margin: 4px 0;
  color: #4b5563;
}
.raw-output {
  background-color: #f3f4f6;
  padding: 6px 8px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.empty {
  text-align: center;
  color: #9ca3af;
  padding: 40px 0;
}
</style>
