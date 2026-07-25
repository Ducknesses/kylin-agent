<template>
  <div class="monitor-panel">
    <div class="monitor-header">
      <span class="title">系统监控大盘</span>
      <div class="header-right">
        <el-tag :type="sourceTagType" size="small">
          {{ sourceTagText }}
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
        v-for="chart in visibleCharts"
        :key="chart.key"
        :ref="el => setChartRef(el, chart.key)"
        :class="['chart-box', { maximized: maximizedChart === chart.key }]"
      >
        <div class="chart-toolbar">
          <span class="chart-source-tag" v-if="!chart.hasHistory">
            <el-tag size="small" type="warning" effect="plain">实时</el-tag>
          </span>
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
import http from '@/api/http'

// ============================================================
// 图表注册表 — 可拓展的图表配置
// 每个图表项可独立定义数据源、历史支持、渲染方式
// ============================================================
const CHART_REGISTRY = Object.freeze([
  {
    key: 'cpu',
    title: 'CPU 使用率',
    color: '#3b82f6',
    dataSource: 'sse_realtime',   // SSE 实时推送 + 本地缓存
    hasHistory: true,              // 支持从 metrics_store 拉取历史数据
    chartType: 'line',
    yAxisName: '%',
    minY: 0, maxY: 100,
    series: [
      { name: 'CPU', field: 'cpu', showSymbol: false, smooth: true }
    ]
  },
  {
    key: 'mem',
    title: '内存 使用率',
    color: '#10b981',
    dataSource: 'sse_realtime',
    hasHistory: true,
    chartType: 'line',
    yAxisName: '%',
    minY: 0, maxY: 100,
    series: [
      { name: '内存', field: 'mem', showSymbol: false, smooth: true }
    ]
  },
  {
    key: 'disk',
    title: '磁盘 使用率',
    color: '#f59e0b',
    dataSource: 'sse_realtime',
    hasHistory: true,
    chartType: 'line',
    yAxisName: '%',
    minY: 0, maxY: 100,
    series: [
      { name: '磁盘', field: 'disk', showSymbol: false, smooth: true }
    ]
  },
  {
    key: 'net',
    title: '网络 IO',
    color: '#8b5cf6',
    dataSource: 'sse_realtime',
    hasHistory: true,
    chartType: 'multi-line',
    yAxisName: 'KB/s',
    minY: 0, maxY: undefined,
    series: [
      { name: '接收', field: 'netIn', showSymbol: false, smooth: true, color: '#8b5cf6' },
      { name: '发送', field: 'netOut', showSymbol: false, smooth: true, color: '#06b6d4' }
    ]
  },
  // ──── 扩展图表：利用现有 MCP 工具实时轮询 ────
  {
    key: 'net_conn',
    title: '网络连接数',
    color: '#ef4444',
    dataSource: 'mcp_poll',        // 轮询 MCP 工具获取实时快照
    mcpTool: 'net_monitor',
    mcpArgs: { metric: 'connections' },
    hasHistory: false,             // 不支持历史数据，降级为显示窗口
    pollIntervalMs: 10000,         // 10s 轮询间隔
    chartType: 'line',
    yAxisName: '个',
    minY: 0, maxY: undefined,
    series: [
      { name: '连接数', field: 'net_conn_count', showSymbol: false, smooth: true }
    ]
  },
  {
    key: 'mcp_qps',
    title: 'MCP 请求统计 (QPS)',
    color: '#8b5cf6',
    dataSource: 'mcp_poll',
    mcpTool: 'mcp_self_monitor',
    mcpArgs: { metric: 'requests' },
    hasHistory: false,
    pollIntervalMs: 10000,
    chartType: 'multi-line',
    yAxisName: '次',
    minY: 0, maxY: undefined,
    series: [
      { name: '成功', field: 'mcp_success', showSymbol: false, smooth: true, color: '#10b981' },
      { name: '错误', field: 'mcp_errors', showSymbol: false, smooth: true, color: '#ef4444' }
    ]
  },
  {
    key: 'mcp_mem',
    title: 'MCP 进程内存',
    color: '#f59e0b',
    dataSource: 'mcp_poll',
    mcpTool: 'mcp_self_monitor',
    mcpArgs: { metric: 'resources' },
    hasHistory: false,
    pollIntervalMs: 10000,
    chartType: 'line',
    yAxisName: 'MB',
    minY: 0, maxY: undefined,
    series: [
      { name: 'RSS', field: 'mcp_mem_rss', showSymbol: false, smooth: true }
    ]
  }
])

const timeRange = ref('5m')
const dataSource = ref('mock')
const mcpConnected = ref(false)
const maximizedChart = ref(null)

// 可见图表列表（默认全显示，后续可扩展用户自定义可见性）
const visibleCharts = computed(() => CHART_REGISTRY)

// 每个图表容器的 DOM 引用
const chartRefs = {}
// echarts 实例
const charts = {}

// 原始数据点（SSE 实时 + MCP轮询共用），保留时间戳对象，最多保留 2 小时
const rawMetrics = []
const MAX_RETAIN_MINUTES = 120
const MAX_RETAIN_POINTS = 2400

// MCP 轮询的独立数据缓存（如 net_conn、mcp_qps 等）
const mcpPollData = {}  // { chartKey: [{time, timestamp, ...fields}] }
const mcpPrevValues = {} // 用于计算 QPS 差值

// 当某个卡片最大化时，让 grid 隐藏其他卡片只显示当前卡片
const gridStyle = computed(() => {
  if (!maximizedChart.value) return {}
  return {
    gridTemplateColumns: '1fr',
    gridTemplateRows: '1fr'
  }
})

// 动态计算 grid 列数：根据可见图表数量自适应
const gridColumns = computed(() => {
  const count = visibleCharts.value.length
  if (count <= 2) return count
  if (count <= 4) return 2
  return 3
})

const sourceTagType = computed(() => {
  if (dataSource.value === 'sse' && mcpConnected.value) return 'success'
  if (dataSource.value === 'sse' && !mcpConnected.value) return 'danger'
  if (dataSource.value === 'polling') return 'warning'
  return 'info'
})

const sourceTagText = computed(() => {
  if (dataSource.value === 'sse' && mcpConnected.value) return 'SSE 实时'
  if (dataSource.value === 'sse' && !mcpConnected.value) return 'MCP 已断开'
  if (dataSource.value === 'polling') return '轮询中'
  return '等待数据'
})

// ============================================================
// 数据写入
// ============================================================

function appendDataPoint(data) {
  if (!mcpConnected.value) {
    console.debug('[SysMonitor] appendDataPoint skipped: mcp not connected')
    return
  }

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
  console.debug('[SysMonitor] appended SSE point, rawMetrics total:', rawMetrics.length,
    'cpu:', point.cpu, 'mem:', point.mem)

  const cutoff = new Date(Date.now() - MAX_RETAIN_MINUTES * 60 * 1000)
  while (rawMetrics.length > MAX_RETAIN_POINTS || rawMetrics[0]?.timestamp < cutoff) {
    rawMetrics.shift()
  }

  refreshAll()
}

// MCP 轮询数据追加
function appendMcpPollPoint(chartKey, fields) {
  if (!mcpPollData[chartKey]) {
    mcpPollData[chartKey] = []
  }
  const ts = new Date()
  const point = {
    time: ts.toLocaleTimeString(),
    timestamp: ts,
    ...fields
  }
  mcpPollData[chartKey].push(point)
  console.debug('[SysMonitor] appended MCP poll point for', chartKey,
    'fields:', fields, 'total:', mcpPollData[chartKey].length)

  // 淘汰旧数据
  const cutoff = new Date(Date.now() - MAX_RETAIN_MINUTES * 60 * 1000)
  const arr = mcpPollData[chartKey]
  while (arr.length > MAX_RETAIN_POINTS || arr[0]?.timestamp < cutoff) {
    arr.shift()
  }

  refreshAll()
}

// ============================================================
// 根据时间范围过滤显示数据
// ============================================================

function getDisplayMetrics(chartConfig) {
  const now = Date.now()
  let ms = 5 * 60 * 1000
  if (timeRange.value === '30m') ms = 30 * 60 * 1000
  if (timeRange.value === '1h') ms = 60 * 60 * 1000
  const cutoff = new Date(now - ms)

  // MCP 轮询数据源使用独立缓存
  if (chartConfig.dataSource === 'mcp_poll') {
    const arr = mcpPollData[chartConfig.key] || []
    return arr.filter(p => p.timestamp >= cutoff)
  }

  // SSE 数据源使用主数据缓存
  return rawMetrics.filter(p => p.timestamp >= cutoff)
}

// ============================================================
// 图表渲染 — 基于注册表动态构建 option
// ============================================================

function buildChartOption(chartConfig) {
  const config = chartConfig
  const base = {
    title: { text: config.title, left: 10, top: 10, textStyle: { fontSize: 14 } },
    grid: { top: 50, left: 50, right: 30, bottom: 30 },
    xAxis: { type: 'category', data: [], boundaryGap: false },
    yAxis: { type: 'value', name: config.yAxisName || '%', min: config.minY ?? 0, max: config.maxY ?? undefined },
    tooltip: { trigger: 'axis' },
    series: config.series.map(s => ({
      name: s.name,
      type: 'line',
      data: [],
      smooth: s.smooth ?? true,
      showSymbol: s.showSymbol ?? false,
      itemStyle: { color: s.color || config.color },
      areaStyle: { opacity: 0.15 }
    }))
  }

  // 多系列图表添加 legend
  if (config.chartType === 'multi-line' && config.series.length > 1) {
    base.legend = {
      data: config.series.map(s => s.name),
      top: 10,
      right: 20
    }
  }

  return base
}

function setChartRef(el, key) {
  if (el) chartRefs[key] = el
}

function initCharts() {
  CHART_REGISTRY.forEach(config => {
    const dom = chartRefs[config.key]?.querySelector('.chart-content')
    if (!dom) {
      console.warn(`[SysMonitor] initCharts: no DOM for ${config.key}`)
      return
    }
    charts[config.key] = echarts.init(dom)
    charts[config.key].setOption(buildChartOption(config))
    console.debug(`[SysMonitor] initCharts: ${config.key} initialized`)
  })
  console.debug('[SysMonitor] All charts initialized, count:', Object.keys(charts).length)
}

function refreshAll() {
  let totalDataPoints = 0
  CHART_REGISTRY.forEach(config => {
    if (!charts[config.key]) return
    const data = getDisplayMetrics(config)
    const times = data.map(p => p.time)

    if (data.length === 0) {
      return
    }
    totalDataPoints += data.length

    const seriesData = config.series.map(s =>
      data.map(p => p[s.field] ?? 0)
    )

    const seriesUpdates = config.series.map((s, i) => ({
      data: seriesData[i]
    }))

    try {
      charts[config.key].setOption({
        xAxis: { data: times },
        series: seriesUpdates
      })
    } catch (e) {
      console.error(`[SysMonitor] setOption failed for ${config.key}:`, e)
    }
  })
  if (totalDataPoints > 0) {
    console.debug('[SysMonitor] refreshAll updated', totalDataPoints, 'data points across all charts')
  }
}

// ============================================================
// SSE 连接
// ============================================================

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
          mcpConnected.value = false
          return
        }
        const wasConnected = mcpConnected.value
        mcpConnected.value = data.mcp_connected === true
        dataSource.value = 'sse'
        if (!wasConnected && mcpConnected.value) {
          console.log('[SSE] MCP 已连接，开始接收实时数据')
        }
        if (data.mcp_connected === true) {
          appendDataPoint(data)
        }
      } catch (e) {
        console.error('[SSE] 数据解析失败:', e)
      }
    }

    sseSource.onerror = () => {
      console.warn('[SSE] 连接断开，降级为轮询')
      mcpConnected.value = false
      sseSource.close()
      sseSource = null
      startPolling()
    }

    sseSource.onopen = () => {
      console.log('[SSE] 连接已建立，等待首条数据...')
      dataSource.value = 'sse'
      stopPolling()
    }
  } catch (e) {
    console.error('[SSE] 创建连接失败:', e)
    startPolling()
  }
}

// ============================================================
// 轮询降级（后端 REST API）
// ============================================================

async function fetchMetrics() {
  try {
    const res = await http.get('/monitor/metrics', { timeout: 5000 })
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

// ============================================================
// MCP 轮询 — 为 mcp_poll 类型图表定时拉取实时数据
// ============================================================

let mcpPollTimers = {} // { chartKey: intervalId }

async function mcpPollSingle(chartConfig) {
  try {
    const res = await http.get('/monitor/mcp/metrics', {
      params: { tool: chartConfig.mcpTool, args: JSON.stringify(chartConfig.mcpArgs) },
      timeout: 8000
    })
    const result = res.data?.result || res.data || {}

    // 根据图表 key 提取数据
    const fields = extractFields(chartConfig, result)
    if (fields) {
      appendMcpPollPoint(chartConfig.key, fields)
    }
  } catch (e) {
    console.warn(`[MCP Poll] ${chartConfig.key} 拉取失败:`, e)
  }
}

function extractFields(config, result) {
  switch (config.key) {
    case 'net_conn': {
      // net_monitor returns { total: N, connections: [...] } at top level
      const total = typeof result?.total === 'number' ? result.total
        : (Array.isArray(result?.connections) ? result.connections.length : 0)
      console.debug('[MCP Poll] net_conn extracted total:', total, 'from:', result)
      return { net_conn_count: total }
    }
    case 'mcp_qps': {
      const requests = result?.requests || result || {}
      const now = Date.now()
      const prev = mcpPrevValues[config.key] || { total: 0, errors: 0, ts: now }
      const totalDelta = (requests.total ?? 0) - prev.total
      const errorsDelta = (requests.errors ?? 0) - prev.errors
      const elapsed = (now - prev.ts) / 1000
      const qps = elapsed > 0 ? Math.round(totalDelta / elapsed) : 0
      const eps = elapsed > 0 ? Math.round(errorsDelta / elapsed) : 0
      mcpPrevValues[config.key] = { total: requests.total ?? 0, errors: requests.errors ?? 0, ts: now }
      console.debug('[MCP Poll] mcp_qps extracted qps:', qps, 'eps:', eps)
      return { mcp_success: qps, mcp_errors: eps }
    }
    case 'mcp_mem': {
      const resources = result?.resources || result || {}
      const rss = resources.memory_rss_mb ?? 0
      console.debug('[MCP Poll] mcp_mem extracted rss:', rss)
      return { mcp_mem_rss: rss }
    }
    default:
      return null
  }
}

function startMcpPolling() {
  CHART_REGISTRY.forEach(config => {
    if (config.dataSource !== 'mcp_poll') return
    // 立即拉取一次
    mcpPollSingle(config)
    // 定时轮询
    const interval = config.pollIntervalMs || 10000
    mcpPollTimers[config.key] = setInterval(() => mcpPollSingle(config), interval)
  })
}

function stopMcpPolling() {
  Object.entries(mcpPollTimers).forEach(([key, id]) => {
    clearInterval(id)
    delete mcpPollTimers[key]
  })
}

// ============================================================
// 历史数据拉取（仅 hasHistory=true 的图表）
// ============================================================

async function fetchHistory(fromMs, toMs) {
  try {
    const fromTs = Math.floor(fromMs / 1000)
    const toTs = Math.floor(toMs / 1000)
    const res = await http.get('/monitor/history', {
      params: { from_ts: fromTs, to_ts: toTs },
      timeout: 8000
    })
    const historyData = res.data?.data || res.data?.result?.data || []
    if (!Array.isArray(historyData) || historyData.length === 0) return

    historyData.forEach(pt => {
      rawMetrics.push({
        time: new Date(pt.ts * 1000).toLocaleTimeString(),
        timestamp: new Date(pt.ts * 1000),
        cpu: pt.cpu_percent ?? 0,
        mem: pt.memory_percent ?? 0,
        disk: pt.disk_percent ?? 0,
        netIn: pt.net_recv_kbps ?? 0,
        netOut: pt.net_sent_kbps ?? 0
      })
    })

    // 去重 + 按时间排序
    const seen = new Set()
    rawMetrics.sort((a, b) => a.timestamp - b.timestamp)
    const deduped = []
    for (const p of rawMetrics) {
      const key = p.timestamp.getTime()
      if (!seen.has(key)) {
        seen.add(key)
        deduped.push(p)
      }
    }
    rawMetrics.length = 0
    rawMetrics.push(...deduped)

    const cutoff = new Date(Date.now() - MAX_RETAIN_MINUTES * 60 * 1000)
    while (rawMetrics.length > MAX_RETAIN_POINTS || rawMetrics[0]?.timestamp < cutoff) {
      rawMetrics.shift()
    }

    refreshAll()
  } catch (e) {
    console.warn('[History] 拉取历史数据失败，使用本地缓存', e)
  }
}

// ============================================================
// 时间范围切换
// ============================================================

function onRangeChange() {
  const now = Date.now()
  let ms = 5 * 60 * 1000
  if (timeRange.value === '30m') ms = 30 * 60 * 1000
  if (timeRange.value === '1h') ms = 60 * 60 * 1000
  const from = now - ms

  // 仅对 hasHistory=true 的图表尝试拉取历史数据
  const historyCharts = CHART_REGISTRY.filter(c => c.hasHistory)
  if (historyCharts.length > 0) {
    const earliest = rawMetrics.length ? rawMetrics[0].timestamp.getTime() : Infinity
    if (earliest > from + 15000) {
      fetchHistory(from, now)
      return
    }
  }

  // hasHistory=false 的图表直接刷新窗口即可（仅过滤本地缓存）
  refreshAll()

  setTimeout(() => Object.values(charts).forEach(c => c && c.resize()), 0)
}

// ============================================================
// 最大化 / 还原
// ============================================================

function toggleMaximize(key) {
  maximizedChart.value = maximizedChart.value === key ? null : key
  setTimeout(() => {
    Object.values(charts).forEach(c => c && c.resize())
  }, 50)
}

// ============================================================
// 生命周期
// ============================================================

function handleWindowResize() {
  Object.values(charts).forEach(c => c && c.resize())
}

onMounted(async () => {
  setTimeout(async () => {
    initCharts()
    const now = Date.now()
    console.debug('[SysMonitor] onMounted: fetching history, then connecting SSE')
    await fetchHistory(now - 5 * 60 * 1000, now)
    connectSse()
    startMcpPolling()
  }, 0)

  window.addEventListener('resize', handleWindowResize)
})

onUnmounted(() => {
  if (sseSource) {
    sseSource.close()
    sseSource = null
  }
  stopPolling()
  stopMcpPolling()
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
  grid-template-rows: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  min-height: 0;
  position: relative;
  overflow-y: auto;
}
.chart-box {
  min-height: 200px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
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
  display: flex;
  align-items: center;
  gap: 6px;
}
.chart-source-tag {
  font-size: 10px;
}
.chart-content {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 100%;
}
</style>