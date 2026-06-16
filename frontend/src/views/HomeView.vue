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
        </div>
      </div>
    </div>
    <div class="chat-area">
      <ChatPanel />
    </div>
  </div>
</template>

<script setup>
import { Plus } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useWsStore } from '@/stores/wsStore'
import { wsClient } from '@/api/ws'
import ChatPanel from '@/components/ChatPanel.vue'

const chatStore = useChatStore()
const wsStore = useWsStore()

function newSession() {
  const id = chatStore.createSession()
  wsClient.connect(id)
}

function switchSession(id) {
  chatStore.switchSession(id)
  wsClient.connect(id)
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
.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
