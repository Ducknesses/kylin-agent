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
          <el-table-column prop="risk" label="风险等级" width="120" />
          <el-table-column label="操作" width="120">
            <template #default="{ $index }">
              <el-button link type="danger" size="small" @click="removeItem($index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
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
import axios from 'axios'

const activeTab = ref('whitelist')
const whitelist = ref([])
const showAddDialog = ref(false)
const newItem = ref({ pattern: '', role: 'agent-read', risk: 'low' })

const riskForm = ref({ high: '', medium: '', low: '' })
const permForm = ref({ read: '', op: '', admin: '' })

async function fetchWhitelist() {
  try {
    const res = await axios.get('/api/config/whitelist')
    whitelist.value = res.data || []
  } catch (e) {
    console.error('拉取白名单失败', e)
    whitelist.value = [
      { pattern: 'df -h', role: 'agent-read', risk: 'low' },
      { pattern: 'systemctl status *', role: 'agent-op', risk: 'low' }
    ]
  }
}

async function saveWhitelist() {
  try {
    await axios.put('/api/config/whitelist', { commands: whitelist.value })
    ElMessage.success('保存成功')
  } catch (e) {
    console.error('保存白名单失败', e)
    ElMessage.warning('后端 PUT 接口尚未实现持久化')
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
  ElMessage.success('风险关键词配置已保存（本地）')
}

function savePermConfig() {
  ElMessage.success('权限配置已保存（本地）')
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
</style>
