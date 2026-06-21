<template>
  <div class="audit-view">
    <el-tabs v-model="activeTab" type="border-card" class="audit-tabs">
      <el-tab-pane label="时间轴" name="timeline">
        <AuditTimeline />
      </el-tab-pane>
      <el-tab-pane label="表格" name="table">
        <AuditTable />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import AuditTimeline from '@/components/AuditTimeline.vue'
import AuditTable from '@/components/AuditTable.vue'

const activeTab = ref('timeline')
</script>

<style scoped>
.audit-view {
  height: 100%;
  padding: 12px;
  box-sizing: border-box;
}
.audit-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.audit-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}
.audit-tabs :deep(.el-tab-pane) {
  height: 100%;
}
</style>
