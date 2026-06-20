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
              <span>{{ item.action || '对话记录' }}</span>
              <el-tag :type="tagType(item.risk_level)" size="small">{{ levelText(item.risk_level) }}</el-tag>
            </div>
          </template>
          <div class="audit-detail">
            <p><strong>用户输入：</strong>{{ item.user_input || '-' }}</p>
            <p><strong>意图：</strong>{{ item.intent || '-' }}</p>
            <p><strong>执行命令：</strong><code>{{ item.command || '-' }}</code></p>
            <p><strong>结果：</strong>{{ item.final_response || '-' }}</p>
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
    const body = res.data
    // 兼容新旧格式：新格式 { records, total }，旧格式是数组
    if (Array.isArray(body)) {
      auditList.value = body
    } else if (body && body.records) {
      auditList.value = body.records
    } else {
      auditList.value = []
    }
  } catch (e) {
    console.error('拉取审计日志失败', e)
    auditList.value = []
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
.empty {
  text-align: center;
  color: #9ca3af;
  padding: 40px 0;
}
</style>
