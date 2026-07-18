<template>
  <div class="tool-card" :class="{ collapsed }">
    <div class="tool-header">
      <el-icon><Tools /></el-icon>
      <span class="tool-name">工具调用：{{ props.data.tool }}</span>
      <button class="toggle-btn" @click="toggleCollapse" :title="collapsed ? '展开' : '收起'">
        <el-icon>
          <ArrowDown v-if="collapsed" />
          <ArrowUp v-else />
        </el-icon>
      </button>
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
import { ref, computed } from 'vue'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'

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

// 判断内容是否"过长"——超过 60 字符或包含换行
const isLongContent = computed(() => {
  const paramsStr = JSON.stringify(props.data.params, null, 2)
  const resultStr = formatResult(props.data.result)
  return paramsStr.length > 60 || resultStr.length > 60 ||
         paramsStr.includes('\n') || resultStr.includes('\n')
})

// 默认：内容短时自动展开，内容长时默认折叠
const collapsed = ref(isLongContent.value)

function toggleCollapse() {
  collapsed.value = !collapsed.value
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
  flex: 1;
}
.toggle-btn {
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  color: #6b7280;
  padding: 2px 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s, color 0.2s;
  flex-shrink: 0;
}
.toggle-btn:hover {
  background-color: #e5e7eb;
  color: #374151;
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

/* ===== 折叠态 ===== */
.tool-card.collapsed .code-block {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tool-card.collapsed .tool-section:last-child {
  margin-bottom: 0;
}
</style>