import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import MonitorView from '@/views/MonitorView.vue'
import AuditView from '@/views/AuditView.vue'
import ConfigPanel from '@/components/ConfigPanel.vue'

const routes = [
  { path: '/', name: 'Home', component: HomeView },
  { path: '/monitor', name: 'Monitor', component: MonitorView },
  { path: '/audit', name: 'Audit', component: AuditView },
  { path: '/config', name: 'Config', component: ConfigPanel }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
