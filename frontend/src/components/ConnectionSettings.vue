<template>
  <el-dialog
    v-model="visible"
    title="连接设置"
    width="560px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <el-tabs v-model="activeTab">
      <!-- Token 认证 -->
      <el-tab-pane label="Token 认证" name="token">
        <div class="section-desc">
          用于 WebSocket 连接的 API Token 认证。留空表示不启用认证。
        </div>
        <el-form label-width="100px" size="small">
          <el-form-item label="API Token">
            <el-input
              v-model="localToken"
              type="password"
              placeholder="请输入 API Token"
              show-password
              clearable
            />
          </el-form-item>
          <el-form-item>
            <div class="token-status">
              <el-tag v-if="localToken" type="success" size="small">已配置 Token</el-tag>
              <el-tag v-else type="info" size="small">未配置 Token</el-tag>
              <span class="hint">（未配置时，后端若不强制认证则可正常连接）</span>
            </div>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 连接地址 -->
      <el-tab-pane label="连接地址" name="connection">
        <div class="section-desc">
          配置后端 WebSocket 和 HTTP API 的连接地址。修改后保存并自动重连。
        </div>

        <el-form label-width="120px" size="small">
          <!-- WebSocket 地址 -->
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

          <!-- HTTP API 地址 -->
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
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleCancel">取消</el-button>
        <el-button type="danger" plain @click="handleClearAll">清除所有配置</el-button>
        <el-button type="primary" @click="handleSave">保存并重连</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useWsStore, getDefaultWsUrl, getDefaultApiUrl, parseUrl, buildUrl } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'

const props = defineProps({
  modelValue: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const wsStore = useWsStore()
const activeTab = ref('token')

const visible = ref(props.modelValue)
const localToken = ref(wsStore.token)

// WebSocket 拆分字段
const wsProtocol = ref('ws://')
const wsHost = ref('localhost')
const wsPort = ref('8000')

// HTTP API 拆分字段
const apiProtocol = ref('http://')
const apiHost = ref('localhost')
const apiPort = ref('8000')

/** 从 store URL 解析出各字段 */
function syncFromStore() {
  const wsParts = parseUrl(wsStore.wsBaseUrl)
  wsProtocol.value = wsParts.protocol
  wsHost.value = wsParts.host
  wsPort.value = wsParts.port

  const apiParts = parseUrl(wsStore.apiBaseUrl)
  apiProtocol.value = apiParts.protocol
  apiHost.value = apiParts.host
  apiPort.value = apiParts.port
}

// 实时预览
const wsPreview = computed(() => buildUrl({ protocol: wsProtocol.value, host: wsHost.value, port: wsPort.value }))
const apiPreview = computed(() => buildUrl({ protocol: apiProtocol.value, host: apiHost.value, port: apiPort.value }))

watch(() => props.modelValue, (val) => {
  visible.value = val
  if (val) {
    localToken.value = wsStore.token
    syncFromStore()
  }
})

watch(visible, (val) => {
  emit('update:modelValue', val)
})

function handleSave() {
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

  visible.value = false
}

function handleCancel() {
  visible.value = false
}

function handleClearAll() {
  localToken.value = ''
  // 重置拆分字段
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

  visible.value = false
}

</script>

<style scoped>
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
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>