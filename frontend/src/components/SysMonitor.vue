<template>
  <div class="monitor-panel">
    <div class="monitor-header">
      <span class="title">系统监控大盘</span>
      <div class="header-right">
        <el-tag :type="dataSource === 'sse' ? 'success' : 'warning'" size="small">
          {{ dataSource === 'sse' ? 'SSE 实时' : dataSource === 'polling' ? '轮询中' : '模拟数据' }}
        </el-tag>
        <el-radio-group v-model="timeRange" size="small" @change="onRangeChange">
          <el-radio-button label="5m">最近5分钟</el-radio-button>
          <el-radio-button label="30m">最近30分钟</el-radio-button>
          <el-radio-button label="1h">最近1小时</el-radio-button>
        </el-radio-group>
      </div>
    </div>
    <div :class="['charts-grid', { 'has-maximized': maximizedChart }]" :style="gridStyle">
      <div
        v-for="chart in CHART_LIST"
        :key="chart.key"
        :ref="el => setChartRef(el, chart.key)"
        :class="['chart-box', { maximized: maximizedChart === chart.key }]"
      >
        <div class="chart-toolbar">
          <el-button
            link
            size="small"
            :title="maximizedChart === chart.key ? '还原' : '最大化'"
            @click="toggleMaximize(chart.key)"
          >
            <el-icon><Close v-if="maximizedChart === chart.key" /><FullScreen v-else /></el-icon>
          </el-button>
        </div>
        <div class="chart-content" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { FullScreen, Close } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import axios from 'axios'

// 图表配置常量（静态数据，大写命名约定表示常量）
const CHART_LIST = [
  { key: 'cpu', title: 'CPU 使用率', color: '#3b82f6' },
  { key: 'mem', title: '内存 使用率', color: '#10b981' },
  { key: 'disk', title: '磁盘 使用率', color: '#f59e0b' },
  { key: 'net', title: '网络 IO', color: '#8b5cf6' }
]

const timeRange = ref('5m')
const dataSource = ref('mock') // 'sse' | 'polling' | 'mock'
const maximizedChart = ref(null)

// 每个图表容器的 DOM 引用
const chartRefs = {}
// echarts 实例
const charts = {}

// 原始数据点，保留时间戳对象，最多保留 2 小时
const rawMetrics = []
const MAX_RETAIN_MINUTES = 120
const MAX_RETAIN_POINTS = 2400 // 2h * 60s / 3s 约 2400 个点（SSE 3s 一次）

// 当某个卡片最大化时，让 grid 隐藏其他卡片只显示当前卡片
const gridStyle = computed(() => {
  if (!maximizedChart.value) return {}
  return {
    gridTemplateColumns: '1fr',
    gridTemplateRows: '1fr'
  }
})

// ===== 数据写入 =====

function appendDataPoint(data) {
  const ts = data.timestamp ? new Date(data.timestamp) : new Date()
  const point = {
    time: ts.toLocaleTimeString(),
    timestamp: ts,
    cpu: data.cpu_percent ?? 0,
    mem: data.memory_percent ?? 0,
    disk: data.disk_percent ?? 0,
    netIn: data.net_in_kbps ?? 0,
    netOut: data.net_out_kbps ?? 0
  }

  rawMetrics.push(point)

  // 按全局保留策略淘汰旧数据，避免内存无限增长
  const cutoff = new Date(Date.now() - MAX_RETAIN_MINUTES * 60 * 1000)
  while (rawMetrics.length > MAX_RETAIN_POINTS || rawMetrics[0]?.timestamp < cutoff) {
    rawMetrics.shift()
  }

  refreshAll()
}

// 根据时间范围返回要展示的数据子集
function getDisplayMetrics() {
  const now = Date.now()
  let ms = 5 * 60 * 1000
  if (timeRange.value === '30m') ms = 30 * 60 * 1000
  if (timeRange.value === '1h') ms = 60 * 60 * 1000
  const cutoff = new Date(now - ms)
  return rawMetrics.filter(p => p.timestamp >= cutoff)
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

function setChartRef(el, key) {
  if (el) chartRefs[key] = el
}

function initCharts() {
  CHART_LIST.forEach(({ key, title, color }) => {
    const dom = chartRefs[key]?.querySelector('.chart-content')
    if (!dom) return
    charts[key] = echarts.init(dom)
    charts[key].setOption(key === 'net' ? netOption() : baseOption(title, color))
  })
}

function refreshAll() {
  const data = getDisplayMetrics()
  const times = data.map(p => p.time)

  charts.cpu && charts.cpu.setOption({ xAxis: { data: times }, series: [{ data: data.map(p => p.cpu) }] })
  charts.mem && charts.mem.setOption({ xAxis: { data: times }, series: [{ data: data.map(p => p.mem) }] })
  charts.disk && charts.disk.setOption({ xAxis: { data: times }, series: [{ data: data.map(p => p.disk) }] })
  charts.net && charts.net.setOption({
    xAxis: { data: times },
    series: [
      { data: data.map(p => p.netIn) },
      { data: data.map(p => p.netOut) }
    ]
  })
}

// ===== SSE 连接 =====

let sseSource = null
let pollTimer = null

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
      stopPolling()
    }
  } catch (e) {
    console.error('[SSE] 创建连接失败:', e)
    startPolling()
  }
}

// ===== 轮询降级 =====

async function fetchMetrics() {
  try {
    const res = await axios.get('/api/monitor/metrics', { timeout: 5000 })
    const data = res.data
    if (data.cpu) {
      appendDataPoint({
        cpu_percent: data.cpu.percent ?? 0,
        memory_percent: data.memory?.percent ?? 0,
        disk_percent: data.disk?.percent ?? 0,
        net_in_kbps: data.network?.rx_kbps ?? 0,
        net_out_kbps: data.network?.tx_kbps ?? 0,
        timestamp: data.timestamp
      })
    }
    if (dataSource.value !== 'sse') {
      dataSource.value = 'polling'
    }
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
  refreshAll()
  // 切换范围后确保图表尺寸正确
  setTimeout(() => Object.values(charts).forEach(c => c && c.resize()), 0)
}

// ===== 最大化 / 还原 =====

function toggleMaximize(key) {
  maximizedChart.value = maximizedChart.value === key ? null : key
  // DOM 变化后 echarts 需要重新计算尺寸
  setTimeout(() => {
    Object.values(charts).forEach(c => c && c.resize())
  }, 50)
}

// ===== 生命周期 =====

function handleWindowResize() {
  Object.values(charts).forEach(c => c && c.resize())
}

onMounted(() => {
  // 等待 DOM 渲染完成后再初始化 echarts
  setTimeout(() => {
    initCharts()
    // 注入初始 mock 数据点（后端就绪前展示用）
    appendDataPoint({
      cpu_percent: +(Math.random() * 30 + 20).toFixed(1),
      memory_percent: +(Math.random() * 20 + 40).toFixed(1),
      disk_percent: +(Math.random() * 10 + 50).toFixed(1),
      net_in_kbps: +(Math.random() * 500 + 100).toFixed(0),
      net_out_kbps: +(Math.random() * 300 + 50).toFixed(0),
      timestamp: new Date().toISOString()
    })
    connectSse()
  }, 0)

  window.addEventListener('resize', handleWindowResize)
})

onUnmounted(() => {
  if (sseSource) {
    sseSource.close()
    sseSource = null
  }
  stopPolling()
  Object.values(charts).forEach(c => c && c.dispose())
  window.removeEventListener('resize', handleWindowResize)
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
  min-height: 0;
  position: relative;
}
.chart-box {
  min-height: 200px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* 支持原生拖拽缩放 */
  resize: both;
}
.chart-box.maximized {
  grid-column: 1 / -1;
  grid-row: 1 / -1;
  z-index: 10;
  resize: none;
}
.charts-grid.has-maximized .chart-box:not(.maximized) {
  display: none;
}
.chart-toolbar {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 20;
}
.chart-content {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 100%;
}
</style>
