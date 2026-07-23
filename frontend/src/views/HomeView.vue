<template>
  <div class="home-view">
    <div class="session-sidebar">
      <div class="sidebar-header">
        <span>会话列表</span>
        <el-button type="primary" size="small" :icon="Plus" @click="newSession">新建</el-button>
      </div>
      <div class="session-list">
        <div
          v-for="s in chatStore.sessions"
          :key="s.id"
          :class="['session-item', { active: s.id === chatStore.currentSessionId }]"
          @click="switchSession(s.id)"
        >
          <el-icon><ChatLineRound /></el-icon>
          <span class="session-title">{{ s.title }}</span>
          <el-icon class="session-delete" @click.stop="confirmDelete(s)"><Close /></el-icon>
        </div>
      </div>
    </div>
    <div class="chat-area">
      <ChatPanel />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Plus, Close } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useChatStore } from '@/stores/chatStore'
import { useWsStore } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'
import ChatPanel from '@/components/ChatPanel.vue'

const chatStore = useChatStore()
const wsStore = useWsStore()

// 当前连接期间是否已确认过删除操作，确认一次后不再弹出确认框
const deleteConfirmed = ref(false)

async function newSession() {
  const id = await chatStore.createSession()
  // 新会话无历史消息，跳过无效 HTTP 请求
  wsClient.connect(id)
}

async function switchSession(id) {
  chatStore.switchSession(id)
  await chatStore.fetchHistory(id)
  wsClient.connect(id)
}

async function confirmDelete(s) {
  if (!deleteConfirmed.value) {
    try {
      await ElMessageBox.confirm(
        `确认删除会话「${s.title}」？删除后不可恢复。`,
        '删除确认',
        { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
      )
    } catch {
      return // 用户取消
    }
    // 用户确认后，本次连接内不再弹出确认框
    deleteConfirmed.value = true
  }
  try {
    await chatStore.deleteSession(s.id)
  } catch (e) {
    ElMessageBox.alert('删除失败，请稍后重试', '错误', { type: 'error' })
  }
}
</script>

<style scoped>
.home-view {
  display: flex;
  height: 100%;
  gap: 12px;
  padding: 12px;
  box-sizing: border-box;
}
.session-sidebar {
  width: 240px;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sidebar-header {
  height: 56px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e5e7eb;
  font-weight: bold;
  color: #1f2937;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  color: #4b5563;
  margin-bottom: 4px;
}
.session-item:hover,
.session-item.active {
  background-color: #eff6ff;
  color: #2563eb;
}
.session-item {
  position: relative;
}
.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-delete {
  flex-shrink: 0;
  opacity: 0;
  color: #ef4444;
  font-size: 16px;
  transition: opacity 0.15s;
  cursor: pointer;
}
.session-item:hover .session-delete {
  opacity: 1;
}
.session-delete:hover {
  color: #dc2626;
  transform: scale(1.15);
}
.chat-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
