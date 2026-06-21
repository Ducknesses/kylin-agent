<template>
  <el-dialog
    v-model="visible"
    title="连接设置"
    width="520px"
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
          配置后端 WebSocket 和 HTTP API 的连接地址。修改后立即生效，并保存到本地存储。
        </div>
        <el-form label-width="140px" size="small">
          <el-form-item label="WebSocket 地址">
            <el-input
              v-model="localWsUrl"
              placeholder="ws://localhost:8000"
            />
          </el-form-item>
          <el-form-item label="HTTP API 地址">
            <el-input
              v-model="localApiUrl"
              placeholder="http://localhost:8000"
            />
          </el-form-item>
          <el-form-item>
            <el-button size="small" type="warning" plain @click="resetDefaults">
              重置为默认值
            </el-button>
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
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useWsStore } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'

const props = defineProps({
  modelValue: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const wsStore = useWsStore()
const activeTab = ref('token')

const visible = ref(props.modelValue)
const localToken = ref(wsStore.token)
const localWsUrl = ref(wsStore.wsBaseUrl)
const localApiUrl = ref(wsStore.apiBaseUrl)

watch(() => props.modelValue, (val) => {
  visible.value = val
  if (val) {
    // 打开时同步最新值
    localToken.value = wsStore.token
    localWsUrl.value = wsStore.wsBaseUrl
    localApiUrl.value = wsStore.apiBaseUrl
  }
})

watch(visible, (val) => {
  emit('update:modelValue', val)
})

function handleSave() {
  // 保存到 store（会自动同步 localStorage）
  wsStore.setToken(localToken.value.trim())
  wsStore.setWsBaseUrl(localWsUrl.value.trim() || 'ws://localhost:8000')
  wsStore.setApiBaseUrl(localApiUrl.value.trim() || 'http://localhost:8000')

  ElMessage.success('配置已保存')

  // 重新连接 WebSocket
  const sessionId = wsStore.activeSessionId
  if (sessionId) {
    wsClient.close(true)
    // 延迟重连，确保旧连接完全关闭
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
  localWsUrl.value = 'ws://localhost:8000'
  localApiUrl.value = 'http://localhost:8000'
  wsStore.resetToDefaults()
  ElMessage.success('已清除所有配置，恢复默认值')

  // 重新连接
  const sessionId = wsStore.activeSessionId
  if (sessionId) {
    wsClient.close(true)
    setTimeout(() => {
      wsClient.connect(sessionId)
    }, 200)
  }

  visible.value = false
}

function resetDefaults() {
  localWsUrl.value = 'ws://localhost:8000'
  localApiUrl.value = 'http://localhost:8000'
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
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>