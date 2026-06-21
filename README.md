# kylin-agent

赛题 A2：面向麒麟操作系统的安全智能运维 Agent。

## 项目结构

```
.
├── backend/   # Python + FastAPI 后端
└── frontend/  # Vue 3 + Vite 前端
```

## 快速开始

### 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your-key"
python run.py
```

后端默认监听 `http://localhost:8000`。

### 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://localhost:5173`。

## 详细说明

详见 [AGENTS.md](./AGENTS.md)。
