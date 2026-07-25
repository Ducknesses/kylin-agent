<template>
  <div class="tools-settings">
    <div class="toolbar">
      <el-button type="primary" @click="fetchTools" :loading="loading">刷新列表</el-button>
      <el-button type="success" @click="refreshMCP" :loading="refreshing" :icon="RefreshIcon">
        重新发现 MCP 工具
      </el-button>
      <span class="tool-count">共 {{ tools.length }} 个工具（静态 {{ staticCount }} / 动态 {{ dynamicCount }}）</span>
    </div>

    <div v-if="tools.length === 0 && !loading" class="empty">暂无工具定义</div>

    <el-collapse v-model="expandedTools" v-loading="loading">
      <el-collapse-item v-for="tool in tools" :key="tool.name" :name="tool.name">
        <template #title>
          <div class="collapse-title">
            <span class="tool-name">{{ tool.name }}</span>
            <el-tag :type="riskTag(tool.default_risk)" size="small">{{ tool.default_risk }}</el-tag>
            <el-tag size="small" type="info">{{ tool.audit_policy?.mode || 'full' }}</el-tag>
            <el-tag :type="sourceTag(tool.source)" size="small" effect="plain">{{ sourceLabel(tool.source) }}</el-tag>
            <el-tag v-if="tool.source === 'mcp'" :type="tool.status === 'available' ? 'success' : 'danger'" size="small" effect="dark">
              {{ tool.status === 'available' ? '在线' : '离线' }}
            </el-tag>
            <span v-if="tool.server_id" class="server-hint">{{ tool.server_id }}</span>
          </div>
        </template>

        <div class="tool-detail">
          <div class="detail-section">
            <div class="detail-label">描述</div>
            <div class="detail-value">{{ tool.description }}</div>
          </div>

          <div class="detail-section">
            <div class="detail-label">默认风险等级</div>
            <el-select v-model="tool.default_risk" size="small" style="width:120px" @change="(v) => updateRisk(tool, {default_risk: v})">
              <el-option label="low" value="low" />
              <el-option label="medium" value="medium" />
              <el-option label="high" value="high" />
            </el-select>
          </div>

          <div v-if="tool.action_field && tool.action_risk_overrides" class="detail-section">
            <div class="detail-label">Action 风险覆盖</div>
            <div class="override-list">
              <div v-for="(risk, action) in tool.action_risk_overrides" :key="action" class="override-item">
                <code>{{ action }}</code>
                <el-select :model-value="risk" size="small" style="width:100px" @change="(v) => { tool.action_risk_overrides[action] = v; updateRisk(tool, {action_risk_overrides: {...tool.action_risk_overrides}}) }">
                  <el-option label="low" value="low" />
                  <el-option label="medium" value="medium" />
                  <el-option label="high" value="high" />
                </el-select>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <div class="detail-label">审计策略</div>
            <div class="audit-cfg">
              <div class="audit-row">
                <span class="alabel">模式:</span>
                <el-select :model-value="tool.audit_policy?.mode || 'full'" size="small" style="width:140px" @change="(v) => updateAudit(tool, {mode: v, safe_fields: tool.audit_policy?.safe_fields || []})">
                  <el-option label="whitelist" value="whitelist" />
                  <el-option label="summary" value="summary" />
                  <el-option label="full" value="full" />
                </el-select>
              </div>
              <div v-if="(tool.audit_policy?.mode || 'full') === 'whitelist'" class="audit-row">
                <span class="alabel">安全字段:</span>
                <div class="sf-editor">
                  <el-tag v-for="(f, i) in (tool.audit_policy?.safe_fields || [])" :key="f" closable size="small" @close="removeSF(tool, i)">{{ f }}</el-tag>
                  <el-select v-if="availParams(tool).length" placeholder="添加字段..." size="small" style="width:140px" :model-value="''" @change="(v) => addSF(tool, v)" filterable>
                    <el-option v-for="p in availParams(tool)" :key="p" :label="p" :value="p" />
                  </el-select>
                  <span v-else class="nop">(无可用)</span>
                </div>
              </div>
              <div v-if="(tool.audit_policy?.mode || 'full') === 'summary'" class="audit-row">
                <span class="alabel">构造器:</span>
                <el-select :model-value="tool.audit_policy?.summary_builder || ''" size="small" style="width:180px" @change="(v) => updateAudit(tool, {summary_builder: v || null})">
                  <el-option label="cmd_exec_summary" value="cmd_exec_summary" />
                </el-select>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <div class="detail-label">参数</div>
            <el-table :data="pList(tool)" size="small" border>
              <el-table-column prop="name" label="参数" width="130" />
              <el-table-column prop="type" label="类型" width="70" />
              <el-table-column label="必填" width="55" align="center">
                <template #default="{row}"><el-tag :type="row.required?'danger':'info'" size="small">{{row.required?'是':'否'}}</el-tag></template>
              </el-table-column>
              <el-table-column prop="enum_str" label="可选值" min-width="160" />
              <el-table-column prop="description" label="说明" min-width="100" show-overflow-tooltip />
            </el-table>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import http from '@/api/http'

const tools = ref([])
const loading = ref(false)
const refreshing = ref(false)
const expandedTools = ref('')
const RefreshIcon = Refresh

onMounted(() => fetchTools())

const staticCount = computed(() => tools.value.filter(t => t.source === 'static').length)
const dynamicCount = computed(() => tools.value.filter(t => t.source === 'mcp').length)

async function fetchTools() {
  loading.value = true
  try {
    const res = await http.get('/tools/definitions')
    tools.value = res.data.tools || []
  } catch (e) {
    ElMessage.error('获取失败: ' + (e.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

async function refreshMCP() {
  refreshing.value = true
  try {
    const res = await http.post('/tools/definitions/refresh')
    ElMessage.success(`MCP 工具发现完成: ${res.data.success_count}/${res.data.total_servers} 服务器已连接，共 ${res.data.available_tools?.length || 0} 个工具可用`)
    await fetchTools()
    // 如果有详情，展示工具发现摘要
    const details = res.data.details || []
    const newTools = details.filter(d => d.success && d.tools_count > 0)
    if (newTools.length > 0) {
      ElMessageBox.alert(
        newTools.map(d => `• ${d.server_id}: 发现 ${d.tools_count} 个工具`).join('\n'),
        '工具发现摘要',
        { confirmButtonText: '好的', type: 'info' }
      )
    }
    const failed = details.filter(d => !d.success)
    if (failed.length > 0) {
      ElMessage.warning(`${failed.length} 个服务器连接失败，请检查 MCP 服务器状态`)
    }
  } catch (e) {
    ElMessage.error('MCP 工具发现失败: ' + (e.response?.data?.detail || e.message))
  } finally { refreshing.value = false }
}

function riskTag(r) { return {low:'success',medium:'warning',high:'danger'}[r]||'info' }

function sourceTag(s) { return s === 'static' ? '' : 'success' }
function sourceLabel(s) { return s === 'static' ? '静态定义' : 'MCP 发现' }

function pList(tool) {
  if (!tool.params) return []
  return Object.entries(tool.params).map(([n, d]) => ({ name:n, type:d.type||'string', required:d.required||false, enum_str:d.enum?.join(', ')||'-', description:d.description||'' }))
}

function availParams(tool) {
  if (!tool.params) return []
  const cur = tool.audit_policy?.safe_fields || []
  return Object.keys(tool.params).filter(p => !cur.includes(p))
}

async function updateRisk(tool, data) {
  try {
    const res = await http.put(`/tools/definitions/${tool.name}/risk`, data)
    Object.assign(tool, res.data)
    ElMessage.success(`${tool.name} 风险已更新`)
  } catch (e) {
    ElMessage.error('更新失败: ' + (e.response?.data?.detail || e.message))
    fetchTools()
  }
}

async function updateAudit(tool, data) {
  try {
    const res = await http.put(`/tools/definitions/${tool.name}/audit`, data)
    Object.assign(tool, res.data)
    ElMessage.success(`${tool.name} 审计策略已更新`)
  } catch (e) {
    ElMessage.error('更新失败: ' + (e.response?.data?.detail || e.message))
    fetchTools()
  }
}

function addSF(tool, field) {
  if (!field) return
  const cur = [...(tool.audit_policy?.safe_fields || [])]
  if (cur.includes(field)) return
  cur.push(field)
  updateAudit(tool, {safe_fields: cur})
}

function removeSF(tool, idx) {
  const cur = [...(tool.audit_policy?.safe_fields || [])]
  cur.splice(idx, 1)
  updateAudit(tool, {safe_fields: cur})
}
</script>

<style scoped>
.tools-settings { padding: 0; }
.toolbar { margin-bottom: 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tool-count { color: #6b7280; font-size: 13px; }
.empty { text-align: center; color: #9ca3af; padding: 40px 0; }
.collapse-title { display: flex; align-items: center; gap: 10px; width: 100%; flex-wrap: wrap; }
.tool-name { font-weight: 600; font-size: 14px; font-family: monospace; color: #1f2937; }
.server-hint { font-size: 11px; color: #6b7280; background: #f3f4f6; padding: 1px 8px; border-radius: 4px; }
.tool-detail { padding: 8px 0; }
.detail-section { margin-bottom: 16px; }
.detail-label { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 6px; }
.detail-value { font-size: 13px; color: #6b7280; line-height: 1.5; }
.override-list { display: flex; flex-wrap: wrap; gap: 8px; }
.override-item { display: flex; align-items: center; gap: 6px; background: #f9fafb; padding: 4px 10px; border-radius: 6px; border: 1px solid #e5e7eb; }
.override-item code { font-size: 12px; color: #2563eb; background: #eff6ff; padding: 1px 6px; border-radius: 3px; }
.audit-cfg { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px 14px; }
.audit-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
.audit-row:last-child { margin-bottom: 0; }
.alabel { font-size: 13px; color: #6b7280; min-width: 70px; padding-top: 4px; }
.sf-editor { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.nop { font-size: 12px; color: #9ca3af; }
</style>