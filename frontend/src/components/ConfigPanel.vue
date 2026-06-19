<template>
  <div class="config-panel">
    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="命令白名单" name="whitelist">
        <div class="toolbar">
          <el-button type="primary" size="small" @click="showAddDialog = true">新增命令</el-button>
          <el-button size="small" @click="fetchWhitelist">刷新</el-button>
        </div>
        <el-table :data="whitelist" border style="width: 100%" size="small">
          <el-table-column prop="pattern" label="命令模板" min-width="200" />
          <el-table-column prop="role" label="适用角色" width="150" />
          <el-table-column prop="risk" label="风险等级" width="120">
            <template #default="{ row }">
              <el-tag :type="row.risk === 'high' ? 'danger' : row.risk === 'medium' ? 'warning' : 'success'" size="small">
                {{ row.risk === 'high' ? '高危' : row.risk === 'medium' ? '中危' : '低危' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ $index }">
              <el-button link type="danger" size="small" @click="removeItem($index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="blocked-section">
          <div class="blocked-title">拦截规则（高危命令黑名单）</div>
          <div class="blocked-tags">
            <el-tag v-for="bp in blockedPatterns" :key="bp" type="danger" size="small" class="blocked-tag">
              {{ bp }}
            </el-tag>
            <span v-if="blockedPatterns.length === 0" class="blocked-empty">暂无拦截规则</span>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="风险关键词" name="risk">
        <el-form :model="riskForm" label-width="120px" size="small">
          <el-form-item label="高危关键词">
            <el-input v-model="riskForm.high" type="textarea" :rows="3" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item label="中危关键词">
            <el-input v-model="riskForm.medium" type="textarea" :rows="3" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item label="低危关键词">
            <el-input v-model="riskForm.low" type="textarea" :rows="3" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveRiskConfig">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="权限配置" name="permission">
        <el-form :model="permForm" label-width="120px" size="small">
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

    <el-dialog v-model="showAddDialog" title="新增白名单命令" width="400px">
      <el-form :model="newItem" label-width="100px" size="small">
        <el-form-item label="命令模板">
          <el-input v-model="newItem.pattern" placeholder="如：df -h" />
        </el-form-item>
        <el-form-item label="适用角色">
          <el-select v-model="newItem.role" placeholder="请选择">
            <el-option label="agent-read" value="agent-read" />
            <el-option label="agent-op" value="agent-op" />
            <el-option label="agent-admin" value="agent-admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级">
          <el-select v-model="newItem.risk" placeholder="请选择">
            <el-option label="低危" value="low" />
            <el-option label="中危" value="medium" />
            <el-option label="高危" value="high" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" size="small" @click="addItem">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'

const activeTab = ref('whitelist')
const whitelist = ref([])
const blockedPatterns = ref([])
const showAddDialog = ref(false)
const newItem = ref({ pattern: '', role: 'agent-read', risk: 'low' })

const riskForm = ref({ high: '', medium: '', low: '' })
const permForm = ref({ read: '', op: '', admin: '' })

async function fetchWhitelist() {
  try {
    // 接口约定文档格式: { code: 200, data: { commands: [...], blocked_patterns: [...] } }
    // http 客户端已自动解包 {code, data}，此处 data 即为内层 data
    const data = await http.get('/config/whitelist')
    whitelist.value = data.commands || data || []
    blockedPatterns.value = data.blocked_patterns || []
  } catch (e) {
    console.error('拉取白名单失败', e)
    whitelist.value = [
      { pattern: 'df -h', role: 'agent-read', risk: 'low' },
      { pattern: 'systemctl status *', role: 'agent-op', risk: 'low' }
    ]
    blockedPatterns.value = ['rm -rf *', 'mkfs.*', '> /etc/*']
  }
}

async function saveWhitelist() {
  try {
    await http.put('/config/whitelist', {
      commands: whitelist.value,
      blocked_patterns: blockedPatterns.value,
    })
    ElMessage.success('白名单配置已保存')
  } catch (e) {
    console.error('保存白名单失败', e)
    ElMessage.warning('保存失败：后端 PUT 接口尚未实现持久化，配置仅本地生效')
  }
}

function addItem() {
  if (!newItem.value.pattern.trim()) return
  whitelist.value.push({ ...newItem.value })
  newItem.value = { pattern: '', role: 'agent-read', risk: 'low' }
  showAddDialog.value = false
  saveWhitelist()
}

function removeItem(index) {
  whitelist.value.splice(index, 1)
  saveWhitelist()
}

function saveRiskConfig() {
  // 后端尚未实现风险关键词配置接口
  ElMessage.info('风险关键词配置仅本地预览，后端接口尚未实现')
}

function savePermConfig() {
  // 后端尚未实现权限配置接口
  ElMessage.info('权限配置仅本地预览，后端接口尚未实现')
}

onMounted(fetchWhitelist)
</script>

<style scoped>
.config-panel {
  height: 100%;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 10px;
}
.blocked-section {
  margin-top: 20px;
  padding: 12px;
  background-color: #fef2f2;
  border-radius: 6px;
  border: 1px solid #fecaca;
}
.blocked-title {
  font-size: 13px;
  font-weight: 600;
  color: #991b1b;
  margin-bottom: 8px;
}
.blocked-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.blocked-tag {
  font-family: monospace;
}
.blocked-empty {
  color: #9ca3af;
  font-size: 12px;
}
</style>
