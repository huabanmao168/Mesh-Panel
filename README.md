# MeshPanel

轻量分布式 Shadowsocks 节点管理面板。一台主控通过 SSH 部署多台海外节点，节点 agent 回连主控上报状态、应用配置，前端可视化管理。

**版本**：v0.0.1

## 特性

- 一键 SSH 部署 sing-box + agent（节点路径 `/opt/meshPanel/`）
- 节点配置走 Web UI（无需手动写 JSON），保存即下发
- Shadowsocks 8 种加密算法，DNS 按协议前缀自动识别（DoH / DoT / UDP / TCP）
- 探针式实时监控：内存采样 2s 推送，15s 过期，CPU / 内存 / 硬盘 / 网络速率 / 上下行累计
- 卡片网格 UI（Element Plus 风格），国旗 + GeoIP 自动回填
- 一键卸载（清干净 sing-box、agent、unit、配置）
- 部署幂等（sha256 比对 + version 检查）
- JWT cookie 鉴权，首次访问设置管理员密码
- WebSocket + HTTP 同端口 8000

## 目录结构

```
mesh-panel/
├── agent/              # Go agent（节点侧）
├── backend/            # FastAPI 主控
│   ├── api/            # REST 路由
│   ├── deploy/         # install.sh / uninstall.sh 生成器
│   ├── models/         # SQLModel 数据模型
│   ├── ssh/            # paramiko SSH 封装
│   └── agent_dist/     # 交叉编译产物（构建时生成，不入库）
├── frontend/           # Vue 3 + Vite + Element Plus
├── docs/               # 设计文档
└── data/               # SQLite（运行时生成，不入库）
```

## 快速开始

### 1. 主控

```bash
# 后端
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 前端 dev
cd frontend
npm install
npm run dev   # :5173 反代 :8000
```

### 2. 编译 agent

```bash
cd agent
make all      # 输出 backend/agent_dist/mesh-agent-{amd64,arm64,armv7}
```

### 3. 添加节点

打开 `http://主控:5173`，首次访问设置管理员密码，然后在右上角 admin 下拉点「添加节点」，填 SSH 信息，点「部署」。

## 技术栈

- **主控后端**：FastAPI + SQLModel + paramiko + WebSocket
- **主控前端**：Vue 3 + Element Plus + Vite
- **节点 agent**：Go（单文件二进制，零依赖）
- **数据面**：sing-box（不锁版本，schema 抗 API 变动）

## License

MIT
