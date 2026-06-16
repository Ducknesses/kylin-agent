<template>
  <div class="tool-card">
    <div class="tool-header">
      <el-icon><Tools /></el-icon>
      <span class="tool-name">工具调用：{{ props.data.tool }}</span>
    </div>
    <div class="tool-section">
      <div class="section-title">参数</div>
      <pre class="code-block">{{ JSON.stringify(props.data.params, null, 2) }}</pre>
    </div>
    <div class="tool-section">
      <div class="section-title">执行结果</div>
      <pre class="code-block result">{{ formatResult(props.data.result) }}</pre>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  data: {
    type: Object,
    required: true
  }
})

function formatResult(result) {
  if (result === undefined || result === null) return '暂无结果'
  if (typeof result === 'object') return JSON.stringify(result, null, 2)
  return String(result)
}
</script>

<style scoped>
.tool-card {
  background-color: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px;
  width: 100%;
  max-width: 600px;
}
.tool-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: bold;
  color: #374151;
  margin-bottom: 10px;
}
.tool-name {
  font-size: 15px;
}
.tool-section {
  margin-bottom: 10px;
}
.section-title {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 4px;
}
.code-block {
  background-color: #1f2937;
  color: #e5e7eb;
  padding: 10px;
  border-radius: 6px;
  font-size: 13px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.code-block.result {
  background-color: #111827;
}
</style>
