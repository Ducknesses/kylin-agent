<template>
  <div class="app-wrapper">
    <el-container class="main-container">
      <el-aside width="220px" class="side-menu">
        <div class="logo">麒麟运维 Agent</div>
        <el-menu
          :default-active="activeMenu"
          router
          background-color="#1f2937"
          text-color="#d1d5db"
          active-text-color="#60a5fa"
        >
          <el-menu-item index="/">
            <el-icon><ChatDotRound /></el-icon>
            <span>智能对话</span>
          </el-menu-item>
          <el-menu-item index="/monitor">
            <el-icon><TrendCharts /></el-icon>
            <span>系统监控</span>
          </el-menu-item>
          <el-menu-item index="/audit">
            <el-icon><Timer /></el-icon>
            <span>审计日志</span>
          </el-menu-item>
          <el-menu-item index="/config">
            <el-icon><Lock /></el-icon>
            <span>权限配置</span>
          </el-menu-item>
          <el-menu-item index="#" @click="settingsVisible = true">
            <el-icon><Setting /></el-icon>
            <span>连接设置</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
    <ConnectionSettings v-model="settingsVisible" />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Setting, Lock } from '@element-plus/icons-vue'
import ConnectionSettings from '@/components/ConnectionSettings.vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
const settingsVisible = ref(false)
</script>

<style scoped>
.app-wrapper {
  height: 100vh;
  width: 100vw;
}
.main-container {
  height: 100%;
}
.side-menu {
  background-color: #1f2937;
  color: #fff;
}
.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-size: 18px;
  font-weight: bold;
  color: #60a5fa;
  border-bottom: 1px solid #374151;
}
.main-content {
  padding: 0;
  background-color: #f3f4f6;
  overflow: hidden;
}
</style>