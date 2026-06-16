<template>
  <div class="monitor-panel">
    <div class="monitor-header">
      <span class="title">系统监控大盘</span>
      <el-radio-group v-model="timeRange" size="small" @change="onRangeChange">
        <el-radio-button label="5m">最近5分钟</el-radio-button>
        <el-radio-button label="30m">最近30分钟</el-radio-button>
        <el-radio-button label="1h">最近1小时</el-radio-button>
      </el-radio-group>
    </div>
    <div class="charts-grid">
      <div ref="cpuChart" class="chart-box" />
      <div ref="memChart" class="chart-box" />
      <div ref="diskChart" class="chart-box" />
      <div ref="netChart" class="chart-box" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'

const timeRange = ref('5m')
const cpuChart = ref(null)
const memChart = ref(null)
const diskChart = ref(null)
const netChart = ref(null)

let charts = {}
let sseSource = null
let mockTimer = null

const metrics = {
  times: [],
  cpu: [],
  mem: [],
  disk: [],
  netIn: [],
  netOut: []
}

function pushPoint() {
  const now = new Date().toLocaleTimeString()
  if (metrics.times.length > 60) {
    metrics.times.shift()
    metrics.cpu.shift()
    metrics.mem.shift()
    metrics.disk.shift()
    metrics.netIn.shift()
    metrics.netOut.shift()
  }
  metrics.times.push(now)
  metrics.cpu.push(+(Math.random() * 30 + 20).toFixed(1))
  metrics.mem.push(+(Math.random() * 20 + 40).toFixed(1))
  metrics.disk.push(+(Math.random() * 10 + 50).toFixed(1))
  metrics.netIn.push(+(Math.random() * 500 + 100).toFixed(0))
  metrics.netOut.push(+(Math.random() * 300 + 50).toFixed(0))
}

function baseOption(title, color) {
  return {
    title: { text: title, left: 10, top: 10, textStyle: { fontSize: 14 } },
    grid: { top: 50, left: 50, right: 30, bottom: 30 },
    xAxis: { type: 'category', data: [], boundaryGap: false },
    yAxis: { type: 'value', name: '%', min: 0, max: 100 },
    tooltip: { trigger: 'axis' },
    series: [
      { type: 'line', data: [], smooth: true, showSymbol: false, itemStyle: { color }, areaStyle: { opacity: 0.15 } }
    ]
  }
}

function netOption() {
  return {
    title: { text: '网络 IO', left: 10, top: 10, textStyle: { fontSize: 14 } },
    grid: { top: 50, left: 50, right: 30, bottom: 30 },
    legend: { data: ['接收', '发送'], top: 10, right: 20 },
    xAxis: { type: 'category', data: [], boundaryGap: false },
    yAxis: { type: 'value', name: 'KB/s', min: 0 },
    tooltip: { trigger: 'axis' },
    series: [
      { name: '接收', type: 'line', data: [], smooth: true, showSymbol: false, itemStyle: { color: '#8b5cf6' }, areaStyle: { opacity: 0.1 } },
      { name: '发送', type: 'line', data: [], smooth: true, showSymbol: false, itemStyle: { color: '#06b6d4' }, areaStyle: { opacity: 0.1 } }
    ]
  }
}

function initCharts() {
  charts.cpu = echarts.init(cpuChart.value)
  charts.mem = echarts.init(memChart.value)
  charts.disk = echarts.init(diskChart.value)
  charts.net = echarts.init(netChart.value)

  charts.cpu.setOption(baseOption('CPU 使用率', '#3b82f6'))
  charts.mem.setOption(baseOption('内存 使用率', '#10b981'))
  charts.disk.setOption(baseOption('磁盘 使用率', '#f59e0b'))
  charts.net.setOption(netOption())
}

function refreshAll() {
  const common = { xAxis: { data: metrics.times } }
  charts.cpu.setOption({ ...common, series: [{ data: metrics.cpu }] })
  charts.mem.setOption({ ...common, series: [{ data: metrics.mem }] })
  charts.disk.setOption({ ...common, series: [{ data: metrics.disk }] })
  charts.net.setOption({
    xAxis: { data: metrics.times },
    series: [{ data: metrics.netIn }, { data: metrics.netOut }]
  })
}

function addMockPoint() {
  pushPoint()
  refreshAll()
}

function connectSse() {
  // 阶段1先用 mock 数据，阶段3再接入真实 SSE
  // sseSource = new EventSource('/api/monitor/stream')
  addMockPoint()
  mockTimer = setInterval(addMockPoint, 3000)
}

function onRangeChange() {
  // 时间范围切换，后续可过滤历史数据
  console.log('切换时间范围', timeRange.value)
}

onMounted(() => {
  initCharts()
  connectSse()
  window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()))
})

onUnmounted(() => {
  if (mockTimer) clearInterval(mockTimer)
  if (sseSource) sseSource.close()
  Object.values(charts).forEach(c => c.dispose())
})
</script>

<style scoped>
.monitor-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  padding: 16px;
}
.monitor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title {
  font-size: 16px;
  font-weight: bold;
  color: #1f2937;
}
.charts-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 16px;
}
.chart-box {
  min-height: 200px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
</style>
