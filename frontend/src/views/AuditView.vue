<template>
  <div class="audit-view">
    <AuditTimeline />
  </div>
</template>

<script setup>
import AuditTimeline from '@/components/AuditTimeline.vue'
</script>

<style scoped>
.audit-view {
  height: 100%;
  padding: 12px;
  box-sizing: border-box;
}
</style>
