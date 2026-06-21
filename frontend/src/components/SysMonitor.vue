<template>
  <div class="monitor-panel">
    <div class="monitor-header">
      <span class="title">系统监控大盘</span>
      <div class="header-right">
        <el-tag :type="dataSource === 'sse' ? 'success' : 'warning'" size="small">
          {{ dataSource === 'sse' ? 'SSE 实时' : '轮询中' }}
        </el-tag>
        <el-radio-group v-model="timeRange" size="small" @change="onRangeChange">
          <el-radio-button label="5m">最近5分钟</el-radio-button>
          <el-radio-button label="30m">最近30分钟</el-radio-button>
          <el-radio-button label="1h">最近1小时</el-radio-button>
        </el-radio-group>
      </div>
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
import axios from 'axios'

const timeRange = ref('5m')
const dataSource = ref('mock') // 'sse' | 'polling' | 'mock'
const cpuChart = ref(null)
const memChart = ref(null)
const diskChart = ref(null)
const netChart = ref(null)

let charts = {}
let sseSource = null
let pollTimer = null

const metrics = {
  times: [],
  cpu: [],
  mem: [],
  disk: [],
  netIn: [],
  netOut: []
}

// ===== 数据写入 =====

function appendDataPoint(data) {
  const now = data.timestamp
    ? new Date(data.timestamp).toLocaleTimeString()
    : new Date().toLocaleTimeString()

  // 保持最多 60 个数据点
  const maxLen = 60
  if (metrics.times.length >= maxLen) {
    metrics.times.shift()
    metrics.cpu.shift()
    metrics.mem.shift()
    metrics.disk.shift()
    metrics.netIn.shift()
    metrics.netOut.shift()
  }

  metrics.times.push(now)
  metrics.cpu.push(data.cpu_percent ?? 0)
  metrics.mem.push(data.memory_percent ?? 0)
  metrics.disk.push(data.disk_percent ?? 0)
  metrics.netIn.push(data.net_in_kbps ?? 0)
  metrics.netOut.push(data.net_out_kbps ?? 0)

  refreshAll()
}

// ===== 图表 =====

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
  charts.cpu && charts.cpu.setOption({ ...common, series: [{ data: metrics.cpu }] })
  charts.mem && charts.mem.setOption({ ...common, series: [{ data: metrics.mem }] })
  charts.disk && charts.disk.setOption({ ...common, series: [{ data: metrics.disk }] })
  charts.net && charts.net.setOption({
    xAxis: { data: metrics.times },
    series: [{ data: metrics.netIn }, { data: metrics.netOut }]
  })
}

// ===== SSE 连接 =====

function connectSse() {
  try {
    sseSource = new EventSource('/api/monitor/stream')

    sseSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.error) {
          console.error('[SSE] 服务端错误:', data.error)
          return
        }
        dataSource.value = 'sse'
        appendDataPoint(data)
      } catch (e) {
        console.error('[SSE] 数据解析失败:', e)
      }
    }

    sseSource.onerror = () => {
      console.warn('[SSE] 连接断开，降级为轮询')
      sseSource.close()
      sseSource = null
      startPolling()
    }

    sseSource.onopen = () => {
      console.log('[SSE] 连接已建立')
      dataSource.value = 'sse'
      // SSE 建立后停止轮询（如果之前有）
      stopPolling()
    }
  } catch (e) {
    console.error('[SSE] 创建连接失败:', e)
    // SSE 不可用时直接走轮询
    startPolling()
  }
}

// ===== 轮询降级 =====

async function fetchMetrics() {
  try {
    const res = await axios.get('/api/monitor/metrics', { timeout: 5000 })
    const data = res.data
    if (data.cpu) {
      // REST 快照格式（嵌套结构）
      appendDataPoint({
        cpu_percent: data.cpu.percent ?? 0,
        memory_percent: data.memory?.percent ?? 0,
        disk_percent: data.disk?.percent ?? 0,
        net_in_kbps: data.network?.rx_kbps ?? 0,
        net_out_kbps: data.network?.tx_kbps ?? 0,
        timestamp: data.timestamp
      })
    }
    dataSource.value = 'polling'
  } catch (e) {
    console.error('[Poll] 拉取监控指标失败:', e)
  }
}

function startPolling() {
  if (pollTimer) return
  dataSource.value = 'polling'
  fetchMetrics()
  pollTimer = setInterval(fetchMetrics, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ===== 时间范围切换 =====

function onRangeChange() {
  console.log('切换时间范围', timeRange.value)
}

// ===== 生命周期 =====

onMounted(() => {
  initCharts()
  // 注入初始 mock 数据点（后端就绪前）
  appendDataPoint({
    cpu_percent: +(Math.random() * 30 + 20).toFixed(1),
    memory_percent: +(Math.random() * 20 + 40).toFixed(1),
    disk_percent: +(Math.random() * 10 + 50).toFixed(1),
    net_in_kbps: +(Math.random() * 500 + 100).toFixed(0),
    net_out_kbps: +(Math.random() * 300 + 50).toFixed(0),
    timestamp: new Date().toISOString()
  })
  // 优先尝试 SSE，不可用时自动降级
  connectSse()

  window.addEventListener('resize', () => Object.values(charts).forEach(c => c && c.resize()))
})

onUnmounted(() => {
  if (sseSource) {
    sseSource.close()
    sseSource = null
  }
  stopPolling()
  Object.values(charts).forEach(c => c && c.dispose())
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
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
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