<template>
  <div class="settings-page">
    <el-tabs v-model="activeTab" type="border-card" class="settings-tabs">
      <!-- ============== Tab 1: 连接设置 ============== -->
      <el-tab-pane label="连接设置" name="connection">
        <div class="tab-content">
          <!-- Token 认证 -->
          <el-card shadow="never" class="section-card">
            <template #header>
              <span class="section-title">Token 认证</span>
            </template>
            <div class="section-desc">
              用于 WebSocket 连接的 API Token 认证。留空表示不启用认证。
            </div>
            <el-form label-width="100px" size="default">
              <el-form-item label="API Token">
                <el-input
                  v-model="localToken"
                  type="password"
                  placeholder="请输入 API Token"
                  show-password
                  clearable
                  style="max-width: 400px"
                />
              </el-form-item>
              <el-form-item>
                <div class="token-status">
                  <el-tag v-if="localToken" type="success">已配置 Token</el-tag>
                  <el-tag v-else type="info">未配置 Token</el-tag>
                  <span class="hint">（未配置时，后端若不强制认证则可正常连接）</span>
                </div>
              </el-form-item>
            </el-form>
          </el-card>

          <!-- WebSocket 地址 -->
          <el-card shadow="never" class="section-card">
            <template #header>
              <span class="section-title">WebSocket 地址</span>
            </template>
            <div class="section-desc">
              后端 WebSocket 连接地址，用于实时消息推送。
            </div>
            <el-form label-width="120px" size="default">
              <el-form-item label="WebSocket 地址">
                <div class="url-fields">
                  <el-select v-model="wsProtocol" class="field-protocol">
                    <el-option label="ws://" value="ws://" />
                    <el-option label="wss://" value="wss://" />
                  </el-select>
                  <el-input v-model="wsHost" placeholder="主机地址" class="field-host" />
                  <span class="field-sep">:</span>
                  <el-input v-model="wsPort" placeholder="端口" class="field-port" />
                </div>
                <div class="url-preview">
                  预览：<code>{{ wsPreview }}</code>
                </div>
                <div class="protocol-hint">
                  <el-tag v-if="wsProtocol === 'wss://'" type="warning" size="small">
                    wss:// 需要后端已启用 SSL/TLS（如经过 Nginx HTTPS 代理）
                  </el-tag>
                  <el-tag v-else type="success" size="small">
                    ws:// 适用于后端直连（无 SSL），生产环境建议通过 Nginx 代理使用 wss://
                  </el-tag>
                </div>
              </el-form-item>
            </el-form>
          </el-card>

          <!-- HTTP API 地址 -->
          <el-card shadow="never" class="section-card">
            <template #header>
              <span class="section-title">HTTP API 地址</span>
            </template>
            <div class="section-desc">
              后端 HTTP API 地址，用于所有 REST 接口调用。
            </div>
            <el-form label-width="120px" size="default">
              <el-form-item label="HTTP API 地址">
                <div class="url-fields">
                  <el-select v-model="apiProtocol" class="field-protocol">
                    <el-option label="http://" value="http://" />
                    <el-option label="https://" value="https://" />
                  </el-select>
                  <el-input v-model="apiHost" placeholder="主机地址" class="field-host" />
                  <span class="field-sep">:</span>
                  <el-input v-model="apiPort" placeholder="端口" class="field-port" />
                </div>
                <div class="url-preview">
                  预览：<code>{{ apiPreview }}</code>
                </div>
                <div class="protocol-hint">
                  <el-tag v-if="apiProtocol === 'https://'" type="warning" size="small">
                    https:// 需要后端已启用 SSL/TLS（如经过 Nginx HTTPS 代理）
                  </el-tag>
                  <el-tag v-else type="success" size="small">
                    http:// 适用于后端直连（无 SSL）
                  </el-tag>
                </div>
              </el-form-item>
            </el-form>
          </el-card>

          <!-- 操作按钮 -->
          <div class="form-actions">
            <el-button type="danger" plain @click="handleClearAll">恢复默认</el-button>
            <el-button type="primary" @click="handleSaveConnection">保存并重连</el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- ============== Tab 2: MCP 服务器 ============== -->
      <el-tab-pane label="MCP 服务器" name="mcp">
        <div class="tab-content">
          <!-- 工具栏 -->
          <div class="toolbar">
            <el-button type="primary" @click="openAddMcpDialog">新增服务器</el-button>
            <el-button @click="openImportDialog">导入 JSON</el-button>
            <el-button @click="exportMcpConfig" :disabled="mcpServers.length === 0">导出配置</el-button>
            <el-button @click="fetchMcpServers" :loading="mcpLoading">刷新列表</el-button>
          </div>

          <!-- 服务器表格 -->
          <el-table :data="mcpServers" border stripe style="width: 100%" size="default" v-loading="mcpLoading">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="id" label="ID" min-width="120" />
            <el-table-column prop="url" label="地址" min-width="220" show-overflow-tooltip />
            <el-table-column prop="transport" label="传输方式" width="120" align="center">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.transport || 'sse' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.connected" type="success" size="small">已连接</el-tag>
                <el-tag v-else type="danger" size="small">断开</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="工具数" width="80" align="center">
              <template #default="{ row }">
                <span class="tools-count">{{ row.tools_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="300" align="center" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="testMcpServer(row)">测试</el-button>
                <el-button link type="primary" size="small" @click="refreshMcpServer(row)">刷新</el-button>
                <el-button link type="warning" size="small" @click="openEditMcpDialog(row)">编辑</el-button>
                <el-popconfirm title="确定要删除此服务器吗？" @confirm="deleteMcpServer(row)">
                  <template #reference>
                    <el-button link type="danger" size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="mcpServers.length === 0 && !mcpLoading" class="empty">
            暂无 MCP 服务器，点击"新增服务器"或"导入 JSON"添加
          </div>
        </div>
      </el-tab-pane>

      <!-- ============== Tab 3: 工具配置 ============== -->
      <el-tab-pane label="工具配置" name="tools">
        <div class="tab-content">
          <ToolsSettings />
        </div>
      </el-tab-pane>

      <!-- ============== Tab 4: 权限配置 ============== -->
      <el-tab-pane label="权限配置" name="permission">
        <div class="tab-content">
          <el-tabs v-model="permSubTab" type="card">
            <!-- 命令白名单 -->
            <el-tab-pane label="命令白名单" name="whitelist">
              <div class="toolbar">
                <el-button type="primary" size="small" @click="showAddWhitelistDialog = true">新增命令</el-button>
                <el-button size="small" @click="fetchWhitelist">刷新</el-button>
              </div>
              <el-table :data="whitelist" border style="width: 100%" size="small" v-loading="permLoading">
                <el-table-column prop="pattern" label="命令模板" min-width="200" />
                <el-table-column prop="role" label="适用角色" width="150" />
                <el-table-column prop="risk" label="风险等级" width="120" />
                <el-table-column label="操作" width="120">
                  <template #default="{ $index }">
                    <el-button link type="danger" size="small" @click="removeWhitelistItem($index)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <!-- 风险拦截规则 -->
            <el-tab-pane label="风险拦截规则" name="risk">
              <div class="toolbar">
                <el-button type="primary" size="small" @click="showAddBlockedDialog = true">新增规则</el-button>
                <el-button size="small" @click="fetchWhitelist">刷新</el-button>
              </div>
              <el-table :data="blockedPatterns" border style="width: 100%" size="small" v-loading="permLoading">
                <el-table-column prop="pattern" label="拦截模式" min-width="300" />
                <el-table-column label="操作" width="120">
                  <template #default="{ $index }">
                    <el-button link type="danger" size="small" @click="removeBlockedItem($index)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="blockedPatterns.length === 0" class="empty">暂无拦截规则</div>
            </el-tab-pane>

            <!-- 权限配置 -->
            <el-tab-pane label="权限配置" name="roles">
              <el-form :model="permForm" label-width="120px" size="small" style="max-width: 500px">
                <el-form-item label="agent-read">
                  <el-input v-model="permForm.read" placeholder="允许执行的命令模式，逗号分隔" />
                </el-form-item>
                <el-form-item label="agent-op">
                  <el-input v-model="permForm.op" placeholder="允许执行的命令模式，逗号分隔" />
                </el-form-item>
                <el-form-item label="agent-admin">
                  <el-input v-model="permForm.admin" placeholder="允许执行的命令模式，逗号分隔" />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" @click="savePermConfig">保存</el-button>
                </el-form-item>
              </el-form>
            </el-tab-pane>
          </el-tabs>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ============== 对话框 ============== -->

    <!-- MCP 新增/编辑对话框 -->
    <el-dialog v-model="mcpDialogVisible" :title="mcpEditingId ? '编辑 MCP 服务器' : '新增 MCP 服务器'" width="520px" destroy-on-close>
      <el-form :model="mcpForm" label-width="110px" size="default">
        <el-form-item label="服务器 ID" required>
          <el-input v-model="mcpForm.id" placeholder="唯一标识，如 kylin-main" :disabled="!!mcpEditingId" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="mcpForm.name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item label="地址" required>
          <el-input v-model="mcpForm.url" placeholder="如 http://192.168.56.101:8001" />
        </el-form-item>
        <el-form-item label="传输方式">
          <el-select v-model="mcpForm.transport">
            <el-option label="SSE" value="sse" />
            <el-option label="Streamable HTTP" value="streamable_http" />
            <el-option label="stdio" value="stdio" />
          </el-select>
        </el-form-item>
        <el-form-item label="认证令牌">
          <el-input v-model="mcpForm.auth_token" placeholder="可选，Bearer Token" clearable />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="mcpForm.enabled" />
        </el-form-item>
        <el-form-item label="自动发现工具">
          <el-switch v-model="mcpForm.auto_discover" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mcpDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitMcpForm" :loading="mcpSubmitting">
          {{ mcpEditingId ? '保存更新' : '注册并连接' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 导入 JSON 对话框 -->
    <el-dialog v-model="importDialogVisible" title="导入 MCP 配置" width="600px" destroy-on-close>
      <div class="section-desc">
        请粘贴 MCP 服务器配置的 JSON 数组，每项对应一个服务器。格式示例：
        <pre class="json-example">[
  {
    "id": "kylin-main",
    "name": "麒麟主服务",
    "url": "http://192.168.56.101:8001",
    "transport": "sse",
    "auth_token": "",
    "enabled": true,
    "auto_discover": true
  }
]</pre>
      </div>
      <el-input
        v-model="importJsonText"
        type="textarea"
        :rows="10"
        placeholder="请粘贴 JSON 配置..."
      />
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleImportJson" :loading="importSubmitting">批量导入</el-button>
      </template>
    </el-dialog>

    <!-- 测试连接结果对话框 -->
    <el-dialog v-model="testDialogVisible" title="连通性测试结果" width="560px" destroy-on-close>
      <div v-if="testResult">
        <el-alert :type="testResult.success ? 'success' : 'error'" :title="testResult.success ? '连接成功' : '连接失败'" :closable="false" show-icon>
          <template v-if="testResult.error">
            <p>{{ testResult.error }}</p>
          </template>
        </el-alert>
        <div v-if="testResult.success" class="test-detail">
          <el-descriptions :column="2" border size="small" style="margin-top: 16px">
            <el-descriptions-item label="协议版本">{{ testResult.protocol_version || '—' }}</el-descriptions-item>
            <el-descriptions-item label="工具数量">{{ testResult.tools_count ?? 0 }}</el-descriptions-item>
          </el-descriptions>
          <div v-if="testResult.server_info" class="info-block">
            <div class="info-title">服务器信息</div>
            <pre class="json-block">{{ JSON.stringify(testResult.server_info, null, 2) }}</pre>
          </div>
          <div v-if="testResult.tools && testResult.tools.length > 0" class="info-block">
            <div class="info-title">可用工具 ({{ testResult.tools.length }})</div>
            <el-table :data="testResult.tools" size="small" max-height="200">
              <el-table-column prop="name" label="工具名" min-width="140" />
              <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
            </el-table>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="testDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 新增白名单命令对话框 -->
    <el-dialog v-model="showAddWhitelistDialog" title="新增白名单命令" width="400px">
      <el-form :model="newWhitelistItem" label-width="100px" size="small">
        <el-form-item label="命令模板">
          <el-input v-model="newWhitelistItem.pattern" placeholder="如：df -h" />
        </el-form-item>
        <el-form-item label="适用角色">
          <el-select v-model="newWhitelistItem.role" placeholder="请选择">
            <el-option label="agent-read" value="agent-read" />
            <el-option label="agent-op" value="agent-op" />
            <el-option label="agent-admin" value="agent-admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级">
          <el-select v-model="newWhitelistItem.risk" placeholder="请选择">
            <el-option label="低危" value="low" />
            <el-option label="中危" value="medium" />
            <el-option label="高危" value="high" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAddWhitelistDialog = false">取消</el-button>
        <el-button type="primary" size="small" @click="addWhitelistItem">确定</el-button>
      </template>
    </el-dialog>

    <!-- 新增拦截规则对话框 -->
    <el-dialog v-model="showAddBlockedDialog" title="新增拦截规则" width="400px">
      <el-form :model="newBlockedPattern" label-width="100px" size="small">
        <el-form-item label="拦截模式">
          <el-input v-model="newBlockedPattern.pattern" placeholder="如：rm -rf /" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAddBlockedDialog = false">取消</el-button>
        <el-button type="primary" size="small" @click="addBlockedItem">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api/http'
import { useWsStore, getDefaultWsUrl, getDefaultApiUrl, parseUrl, buildUrl } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'
import ToolsSettings from '@/components/ToolsSettings.vue'

// ==================== 连接设置逻辑 ====================
const wsStore = useWsStore()

const localToken = ref(wsStore.token)

const wsProtocol = ref('ws://')
const wsHost = ref('localhost')
const wsPort = ref('8000')

const apiProtocol = ref('http://')
const apiHost = ref('localhost')
const apiPort = ref('8000')

function syncFromStore() {
  localToken.value = wsStore.token

  const wsParts = parseUrl(wsStore.wsBaseUrl)
  wsProtocol.value = wsParts.protocol
  wsHost.value = wsParts.host
  wsPort.value = wsParts.port

  const apiParts = parseUrl(wsStore.apiBaseUrl)
  apiProtocol.value = apiParts.protocol
  apiHost.value = apiParts.host
  apiPort.value = apiParts.port
}

const wsPreview = computed(() => buildUrl({ protocol: wsProtocol.value, host: wsHost.value, port: wsPort.value }))
const apiPreview = computed(() => buildUrl({ protocol: apiProtocol.value, host: apiHost.value, port: apiPort.value }))

function handleSaveConnection() {
  wsStore.setToken(localToken.value.trim())
  wsStore.setWsBaseUrl(wsPreview.value || getDefaultWsUrl())
  wsStore.setApiBaseUrl(apiPreview.value || getDefaultApiUrl())

  ElMessage.success('配置已保存')

  const sessionId = wsStore.activeSessionId
  if (sessionId) {
    wsClient.close(true)
    setTimeout(() => {
      wsClient.connect(sessionId)
    }, 200)
  }
}

function handleClearAll() {
  localToken.value = ''

  const wsDefault = parseUrl(getDefaultWsUrl())
  wsProtocol.value = wsDefault.protocol
  wsHost.value = wsDefault.host
  wsPort.value = wsDefault.port

  const apiDefault = parseUrl(getDefaultApiUrl())
  apiProtocol.value = apiDefault.protocol
  apiHost.value = apiDefault.host
  apiPort.value = apiDefault.port

  wsStore.resetToDefaults()
  ElMessage.success('已清除所有配置，恢复默认值')

  const sessionId = wsStore.activeSessionId
  if (sessionId) {
    wsClient.close(true)
    setTimeout(() => {
      wsClient.connect(sessionId)
    }, 200)
  }
}

// ==================== MCP 服务器管理逻辑 ====================
const mcpServers = ref([])
const mcpLoading = ref(false)
const mcpSubmitting = ref(false)
const mcpDialogVisible = ref(false)
const mcpEditingId = ref(null)

const mcpForm = ref({
  id: '',
  name: '',
  url: '',
  transport: 'sse',
  auth_token: '',
  enabled: true,
  auto_discover: true,
})

function resetMcpForm() {
  mcpForm.value = { id: '', name: '', url: '', transport: 'sse', auth_token: '', enabled: true, auto_discover: true }
  mcpEditingId.value = null
}

async function fetchMcpServers() {
  mcpLoading.value = true
  try {
    const res = await http.get('/mcp/servers')
    mcpServers.value = Array.isArray(res.data) ? res.data : (res.data.servers || [])
  } catch (e) {
    console.error('获取 MCP 服务器列表失败', e)
    ElMessage.error('获取 MCP 服务器列表失败，请检查后端连接')
  } finally {
    mcpLoading.value = false
  }
}

function openAddMcpDialog() {
  resetMcpForm()
  mcpDialogVisible.value = true
}

function openEditMcpDialog(row) {
  mcpEditingId.value = row.id
  mcpForm.value = {
    id: row.id,
    name: row.name || '',
    url: row.url || '',
    transport: row.transport || 'sse',
    auth_token: row.auth_token || '',
    enabled: row.enabled !== false,
    auto_discover: row.auto_discover !== false,
  }
  mcpDialogVisible.value = true
}

async function submitMcpForm() {
  if (!mcpForm.value.id || !mcpForm.value.name || !mcpForm.value.url) {
    ElMessage.warning('请填写服务器 ID、名称和地址')
    return
  }
  mcpSubmitting.value = true
  try {
    if (mcpEditingId.value) {
      const updates = {}
      for (const [k, v] of Object.entries(mcpForm.value)) {
        if (k === 'id') continue
        updates[k] = v
      }
      await http.put(`/mcp/servers/${mcpEditingId.value}`, updates)
      ElMessage.success('服务器配置已更新')
    } else {
      await http.post('/mcp/servers', mcpForm.value)
      ElMessage.success('服务器注册成功')
    }
    mcpDialogVisible.value = false
    resetMcpForm()
    await fetchMcpServers()
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message || '操作失败'
    ElMessage.error(detail)
  } finally {
    mcpSubmitting.value = false
  }
}

async function deleteMcpServer(row) {
  try {
    await http.delete(`/mcp/servers/${row.id}`)
    ElMessage.success(`已删除服务器 "${row.name}"`)
    await fetchMcpServers()
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message || '删除失败'
    ElMessage.error(detail)
  }
}

async function refreshMcpServer(row) {
  try {
    const res = await http.post(`/mcp/servers/${row.id}/refresh`)
    ElMessage.success(`已刷新，发现 ${res.data.tools_count ?? 0} 个工具`)
    await fetchMcpServers()
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message || '刷新失败'
    ElMessage.error(detail)
  }
}

// 测试连接
const testDialogVisible = ref(false)
const testResult = ref(null)

async function testMcpServer(row) {
  try {
    const res = await http.post('/mcp/servers/test', {
      url: row.url,
      transport: row.transport || 'sse',
      auth_token: row.auth_token || null,
    })
    testResult.value = res.data
    testDialogVisible.value = true
  } catch (e) {
    testResult.value = { success: false, error: e?.response?.data?.detail || e.message || '测试请求失败' }
    testDialogVisible.value = true
  }
}

// 导入/导出
const importDialogVisible = ref(false)
const importJsonText = ref('')
const importSubmitting = ref(false)

function openImportDialog() {
  importJsonText.value = ''
  importDialogVisible.value = true
}

async function handleImportJson() {
  if (!importJsonText.value.trim()) {
    ElMessage.warning('请粘贴 JSON 配置')
    return
  }
  let configs
  try {
    configs = JSON.parse(importJsonText.value)
  } catch {
    ElMessage.error('JSON 格式无效，请检查')
    return
  }
  if (!Array.isArray(configs)) {
    configs = [configs]
  }

  importSubmitting.value = true
  let successCount = 0
  let failCount = 0

  for (const cfg of configs) {
    try {
      await http.post('/mcp/servers', {
        id: cfg.id || '',
        name: cfg.name || cfg.id || '',
        url: cfg.url || '',
        transport: cfg.transport || 'sse',
        auth_token: cfg.auth_token || '',
        enabled: cfg.enabled !== false,
        auto_discover: cfg.auto_discover !== false,
      })
      successCount++
    } catch (e) {
      console.error(`导入 ${cfg.id || cfg.name} 失败:`, e)
      failCount++
    }
  }

  if (successCount > 0 || failCount === 0) {
    ElMessage.success(`成功导入 ${successCount} 个服务器${failCount > 0 ? `，${failCount} 个失败` : ''}`)
  } else {
    ElMessage.error(`全部 ${failCount} 个服务器导入失败`)
  }

  importSubmitting.value = false
  importDialogVisible.value = false
  await fetchMcpServers()
}

function exportMcpConfig() {
  const exportData = mcpServers.value.map(s => ({
    id: s.id,
    name: s.name,
    url: s.url,
    transport: s.transport,
    enabled: s.enabled,
    auto_discover: s.auto_discover,
  }))
  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'mcp-servers-config.json'
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('配置已导出')
}

// ==================== 权限配置逻辑 ====================
const permSubTab = ref('whitelist')
const whitelist = ref([])
const blockedPatterns = ref([])
const permLoading = ref(false)
const showAddWhitelistDialog = ref(false)
const showAddBlockedDialog = ref(false)
const newWhitelistItem = ref({ pattern: '', role: 'agent-read', risk: 'low' })
const newBlockedPattern = ref({ pattern: '' })
const permForm = ref({ read: '', op: '', admin: '' })

async function fetchWhitelist() {
  permLoading.value = true
  try {
    const res = await http.get('/config/whitelist')
    whitelist.value = res.data.commands || []
    blockedPatterns.value = (res.data.blocked_patterns || []).map(p => ({ pattern: p }))
  } catch (e) {
    console.error('拉取白名单失败', e)
    ElMessage.error('拉取白名单配置失败，请检查后端服务')
  } finally {
    permLoading.value = false
  }
}

async function saveWhitelist() {
  try {
    await http.put('/config/whitelist', {
      commands: whitelist.value,
      blocked_patterns: blockedPatterns.value.map(p => p.pattern),
    })
    ElMessage.success('保存成功')
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message || '未知错误'
    ElMessage.error(`保存失败: ${detail}`)
  }
}

function addWhitelistItem() {
  if (!newWhitelistItem.value.pattern.trim()) return
  whitelist.value.push({ ...newWhitelistItem.value })
  newWhitelistItem.value = { pattern: '', role: 'agent-read', risk: 'low' }
  showAddWhitelistDialog.value = false
  saveWhitelist()
}

function removeWhitelistItem(index) {
  whitelist.value.splice(index, 1)
  saveWhitelist()
}

function addBlockedItem() {
  if (!newBlockedPattern.value.pattern.trim()) return
  blockedPatterns.value.push({ pattern: newBlockedPattern.value.pattern })
  newBlockedPattern.value.pattern = ''
  showAddBlockedDialog.value = false
  saveWhitelist()
}

function removeBlockedItem(index) {
  blockedPatterns.value.splice(index, 1)
  saveWhitelist()
}

function savePermConfig() {
  ElMessage.success('权限配置已保存（本地）')
}

// ==================== 初始化 ====================
const activeTab = ref('connection')

// 切换到 MCP tab 或权限 tab 时自动加载数据
watch(activeTab, (tab) => {
  if (tab === 'mcp') {
    fetchMcpServers()
  } else if (tab === 'permission') {
    fetchWhitelist()
  }
})

onMounted(() => {
  syncFromStore()
})
</script>

<style scoped>
.settings-page {
  height: 100%;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.settings-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
  border: none;
  box-shadow: none;
}

.settings-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow-y: auto;
  padding: 0;
}

.settings-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.tab-content {
  padding: 20px;
}

.section-card {
  margin-bottom: 16px;
}

.section-title {
  font-weight: 600;
  font-size: 15px;
}

.section-desc {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 16px;
  line-height: 1.6;
}

.token-status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hint {
  font-size: 12px;
  color: #9ca3af;
}

.url-fields {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  max-width: 500px;
}

.field-protocol {
  width: 100px;
  flex-shrink: 0;
}

.field-host {
  flex: 1;
  min-width: 0;
}

.field-sep {
  font-size: 14px;
  color: #9ca3af;
  flex-shrink: 0;
  padding: 0 2px;
}

.field-port {
  width: 80px;
  flex-shrink: 0;
}

.url-preview {
  margin-top: 4px;
  font-size: 12px;
  color: #9ca3af;
}

.url-preview code {
  color: #2563eb;
  background: #eff6ff;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: monospace;
}

.protocol-hint {
  margin-top: 6px;
  line-height: 1.4;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.empty {
  text-align: center;
  color: #9ca3af;
  padding: 40px 0;
}

.tools-count {
  font-weight: 600;
  color: #2563eb;
}

.json-example {
  background: #f3f4f6;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  margin-top: 8px;
  color: #374151;
}

.test-detail {
  margin-top: 12px;
}

.info-block {
  margin-top: 16px;
}

.info-title {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
}

.json-block {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  max-height: 200px;
}
</style>