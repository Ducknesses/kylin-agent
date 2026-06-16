<template>
  <el-dialog
    v-model="visible"
    title="风险提示"
    width="460px"
    :close-on-click-modal="false"
    align-center
  >
    <div class="risk-content">
      <el-icon :size="48" :color="levelColor" class="risk-icon"><WarningFilled /></el-icon>
      <div class="risk-level">{{ levelText }}风险操作</div>
      <div class="risk-reason">{{ reason }}</div>
      <div v-if="originalInput" class="risk-original">
        原始输入：{{ originalInput }}
      </div>
    </div>
    <template #footer>
      <el-button @click="onCancel">取消</el-button>
      <el-button v-if="level === 'medium'" type="warning" @click="onConfirm">确认执行</el-button>
      <el-button v-else type="primary" @click="onCancel">我知道了</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  level: {
    type: String,
    default: 'high'
  },
  reason: {
    type: String,
    default: ''
  },
  originalInput: {
    type: String,
    default: ''
  },
  confirmId: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])

const visible = ref(props.modelValue)

watch(() => props.modelValue, (val) => {
  visible.value = val
})

watch(visible, (val) => {
  emit('update:modelValue', val)
})

const levelText = computed(() => {
  const map = { high: '高', medium: '中', low: '低' }
  return map[props.level] || props.level
})

const levelColor = computed(() => {
  const map = { high: '#ef4444', medium: '#f59e0b', low: '#10b981' }
  return map[props.level] || '#6b7280'
})

function onConfirm() {
  visible.value = false
  emit('confirm', { confirmId: props.confirmId, decision: 'approve' })
}

function onCancel() {
  visible.value = false
  emit('cancel')
}
</script>

<style scoped>
.risk-content {
  text-align: center;
  padding: 10px 0;
}
.risk-icon {
  margin-bottom: 12px;
}
.risk-level {
  font-size: 18px;
  font-weight: bold;
  color: #1f2937;
  margin-bottom: 10px;
}
.risk-reason {
  font-size: 15px;
  color: #4b5563;
  margin-bottom: 10px;
}
.risk-original {
  font-size: 13px;
  color: #9ca3af;
  word-break: break-all;
}
</style>
