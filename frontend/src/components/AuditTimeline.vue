<template>
  <div class="audit-panel">
    <div class="audit-header">
      <span class="title">审计日志</span>
      <div class="filters">
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button label="timeline">时间轴</el-radio-button>
          <el-radio-button label="table">表格</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          unlink-panels
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          size="small"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          style="width: 260px"
        />
        <el-select v-model="filterLevel" placeholder="风险等级" clearable size="small" style="width: 120px">
          <el-option label="高危" value="high" />
          <el-option label="中危" value="medium" />
          <el-option label="低危" value="low" />
        </el-select>
        <el-button type="primary" size="small" @click="fetchAudit">刷新</el-button>
      </div>
    </div>

    <el-timeline v-if="viewMode === 'timeline'" class="timeline">
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

    <el-table
      v-if="viewMode === 'table'"
      :data="filteredList"
      border
      size="small"
      style="width: 100%"
      row-key="trace_id"
      class="audit-table"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="audit-detail">
            <p><strong>用户输入：</strong>{{ row.user_input || '-' }}</p>
            <p><strong>工具：</strong>{{ row.mcp_tool || '-' }}</p>
            <p><strong>执行命令：</strong><code>{{ row.command || '-' }}</code></p>
            <p><strong>原始输出：</strong><pre class="raw-output">{{ row.raw_output || '-' }}</pre></p>
            <p><strong>最终回复：</strong>{{ row.final_response || '-' }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="timestamp" label="时间" width="170" sortable>
        <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
      </el-table-column>
      <el-table-column prop="user_input" label="用户输入" min-width="180" show-overflow-tooltip />
      <el-table-column prop="intent" label="意图" width="110" show-overflow-tooltip />
      <el-table-column prop="mcp_tool" label="工具" width="130" show-overflow-tooltip />
      <el-table-column prop="command" label="执行命令" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <code v-if="row.command" class="inline-code">{{ row.command }}</code>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="risk_level" label="风险等级" width="100" sortable>
        <template #default="{ row }">
          <el-tag :type="tagType(row.risk_level)" size="small">{{ levelText(row.risk_level) }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="total > pageSize" class="pagination-wrap">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        size="small"
        @current-change="onPageChange"
      />
    </div>

    <div v-if="filteredList.length === 0" class="empty">暂无审计记录</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import http from '../api/http'

const auditList = ref([])
const filterLevel = ref('')
const dateRange = ref(null)
const viewMode = ref('timeline')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const filteredList = computed(() => {
  if (!filterLevel.value) return auditList.value
  return auditList.value.filter(item => item.risk_level === filterLevel.value)
})

// 日期变更时重置分页并重新拉取
watch(dateRange, () => {
  currentPage.value = 1
  fetchAudit()
})

async function fetchAudit() {
  try {
    const params = {
      page: currentPage.value,
      limit: pageSize.value,
    }
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_date = dateRange.value[0]
      params.end_date = dateRange.value[1]
    }
    // 接口约定文档路径: GET /api/audit/logs, 响应: { total, list }
    const data = await http.get('/audit/logs', { params })
    auditList.value = data.list || data || []
    total.value = data.total || 0
  } catch (e) {
    console.error('拉取审计日志失败', e)
    // fallback mock 数据
    auditList.value = [
      { trace_id: 'mock-1', timestamp: new Date().toISOString(), user_input: '查看CPU', intent: 'tool_call', risk_level: 'low', mcp_tool: 'sys_info', command: 'sys_info metric=cpu', raw_output: '{"cpu_percent": 15.2}', final_response: 'CPU 使用率 15%' }
    ]
    total.value = 0
  }
}

function onPageChange(page) {
  currentPage.value = page
  fetchAudit()
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
.audit-table {
  flex: 1;
  overflow-y: auto;
}
.inline-code {
  background-color: #f3f4f6;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  font-family: monospace;
}
.pagination-wrap {
  display: flex;
  justify-content: center;
  padding: 12px 0 0;
}
.empty {
  text-align: center;
  color: #9ca3af;
  padding: 40px 0;
}
</style>
