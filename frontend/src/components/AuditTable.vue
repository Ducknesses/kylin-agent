<template>
  <div class="audit-table-panel">
    <div class="table-toolbar">
      <div class="filters">
        <el-select v-model="filterLevel" placeholder="风险等级" clearable size="small" style="width: 120px">
          <el-option label="高危" value="high" />
          <el-option label="中危" value="medium" />
          <el-option label="低危" value="low" />
        </el-select>
        <el-input
          v-model="filterUser"
          placeholder="按用户输入搜索"
          clearable
          size="small"
          style="width: 200px"
        />
        <el-button type="primary" size="small" @click="fetchData(1)">刷新</el-button>
      </div>
    </div>

    <el-table :data="tableData" border stripe size="small" style="width: 100%" v-loading="loading">
      <el-table-column prop="timestamp" label="时间" width="170">
        <template #default="{ row }">
          {{ formatTime(row.timestamp) }}
        </template>
      </el-table-column>
      <el-table-column prop="risk_level" label="风险" width="80">
        <template #default="{ row }">
          <el-tag :type="tagType(row.risk_level)" size="small">{{ levelText(row.risk_level) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="user_input" label="用户输入" min-width="180" show-overflow-tooltip />
      <el-table-column prop="intent" label="意图" width="120" show-overflow-tooltip />
      <el-table-column prop="mcp_tool" label="MCP工具" width="120" show-overflow-tooltip />
      <el-table-column prop="command" label="执行命令" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <code v-if="row.command" class="cmd-code">{{ row.command }}</code>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="final_response" label="响应" min-width="200" show-overflow-tooltip />
    </el-table>

    <div class="pagination-wrapper">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        size="small"
        @size-change="onSizeChange"
        @current-change="onPageChange"
      />
    </div>

    <el-alert v-if="errorMsg" :title="errorMsg" type="error" show-icon closable @close="errorMsg = ''" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const tableData = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const errorMsg = ref('')

const filterLevel = ref('')
const filterUser = ref('')

const filteredData = computed(() => {
  let list = tableData.value
  if (filterLevel.value) {
    list = list.filter(item => item.risk_level === filterLevel.value)
  }
  if (filterUser.value) {
    const kw = filterUser.value.toLowerCase()
    list = list.filter(item => (item.user_input || '').toLowerCase().includes(kw))
  }
  return list
})

async function fetchData(page) {
  loading.value = true
  errorMsg.value = ''
  const offset = (page - 1) * pageSize.value
  try {
    const res = await axios.get('/api/audit', {
      params: { limit: pageSize.value, offset },
    })
    const body = res.data
    if (body.error) {
      errorMsg.value = `API 错误: ${body.error}`
      tableData.value = []
      total.value = 0
    } else {
      // 兼容新旧格式：新格式 { records, total }，旧格式是数组
      if (Array.isArray(body)) {
        tableData.value = body
        total.value = body.length
      } else {
        tableData.value = body.records || []
        total.value = body.total || 0
      }
    }
  } catch (e) {
    console.error('加载审计表格数据失败', e)
    errorMsg.value = `请求失败: ${e.message || '网络错误'}`
    tableData.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onPageChange(page) {
  currentPage.value = page
  fetchData(page)
}

function onSizeChange(size) {
  pageSize.value = size
  currentPage.value = 1
  fetchData(1)
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

onMounted(() => fetchData(1))
</script>

<style scoped>
.audit-table-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.table-toolbar {
  margin-bottom: 12px;
}
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
}
.pagination-wrapper {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
.cmd-code {
  background: #f3f4f6;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
}
</style>