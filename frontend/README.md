# kylin-agent 前端

基于 Vue 3 + Vite + Element Plus + ECharts 的麒麟安全智能运维 Agent 前端。

## 环境要求

- Node.js 20+（推荐 20 LTS）
- npm 10+

> 当前在 WSL Ubuntu 22.04 中已使用本地 Node 二进制（`../.node/bin`）。

## 安装依赖

```bash
cd frontend
npm install
```

## 开发运行

```bash
npm run dev
```

默认访问：http://localhost:5173

后端地址通过环境变量配置：

- `.env.development`：`VITE_API_BASE_URL=http://localhost:8000`
- `.env.development`：`VITE_WS_BASE_URL=ws://localhost:8000`

## 生产构建

```bash
npm run build
```

产物在 `frontend/dist/` 目录，可部署到 Nginx。

## 目录说明

```
frontend/
├── src/
│   ├── api/ws.js              # WebSocket 封装（心跳、重连、消息分发）
│   ├── stores/
│   │   ├── chatStore.js       # 会话与消息状态
│   │   └── wsStore.js         # WebSocket 连接状态
│   ├── components/
│   │   ├── ChatPanel.vue      # 主对话面板
│   │   ├── MsgBubble.vue      # 消息气泡
│   │   ├── ToolCallCard.vue   # 工具调用卡片
│   │   ├── RiskAlert.vue      # 风险拦截弹窗
│   │   ├── SysMonitor.vue     # 系统监控图表
│   │   ├── AuditTimeline.vue  # 审计日志时间轴
│   │   └── ConfigPanel.vue    # 白名单/权限配置
│   ├── views/
│   │   ├── HomeView.vue       # 对话页
│   │   ├── MonitorView.vue    # 监控页
│   │   └── AuditView.vue      # 审计页
│   ├── router/index.js        # 路由配置
│   ├── App.vue
│   └── main.js
├── package.json
└── vite.config.js
```

## 阶段 2 完成项

- [x] Vite + Vue3 + Element Plus 项目能跑通
- [x] WebSocket 连接封装完成（心跳、自动重连、消息分类）
- [x] ChatPanel 完整支持 user / assistant / tool_call / risk_alert 四种消息渲染
- [x] 流式接收正常，`chunk` 消息逐字追加
- [x] RiskAlert 弹窗正常弹出，中危二次确认按钮可用
- [x] SysMonitor 优先接入后端 SSE，失败 fallback 到 mock 数据
- [x] AuditTimeline 拉取后端审计日志
- [x] ConfigPanel 与后端白名单接口对齐
- [x] `npm run build` 通过

## 界面测试方法

### 1. 启动前端

```bash
export PATH="/home/jackb/projects/kylin-agent/.node/bin:$PATH"
cd frontend
npm run dev
```

Vite 已配置 `host: true`，默认可通过以下地址访问：

- http://localhost:5173/
- http://172.30.250.219:5173/（WSL 网络地址，便于 Windows 浏览器访问）

### 2. 无后端测试 UI

在左侧菜单进入 **智能对话**，点击聊天面板右上角的 **“注入测试数据”** 按钮，可一键生成：

- 用户消息
- AI 流式回复（逐字追加）
- 工具调用卡片（`sys_info`）
- 高危风险拦截弹窗（`rm -rf /`）

这样无需启动后端也能验证四种消息类型的渲染效果。

### 3. 测试系统监控

进入 **系统监控** 页面，4 个 ECharts 图表会每 3 秒自动刷新 mock 数据，可验证图表渲染与响应式缩放。

### 4. 与后端联调

先启动后端服务，再刷新前端页面：

```bash
cd backend
source .venv/bin/activate
python run.py
```

此时：

- 前端通过 Vite 代理访问 `http://localhost:8000/api/*`
- WebSocket 直接连接 `ws://localhost:8000/ws/chat/{session_id}`
- 可在对话页发送真实运维问题测试完整流程
