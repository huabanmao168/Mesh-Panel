# MeshPanel

轻量分布式 Shadowsocks 节点管理面板。一台主控通过 SSH 部署多台海外节点，节点 agent 回连主控上报状态、应用配置，前端可视化管理。

**当前版本**：[![Latest Release](https://img.shields.io/github/v/release/huabanmao168/Mesh-Panel)](https://github.com/huabanmao168/Mesh-Panel/releases/latest)

## 特性

- **一键安装** —— 一行 `curl | bash` 部署主控
- 一键 SSH 部署 sing-box + agent 到节点（节点路径 `/opt/meshPanel/`）
- 节点配置走 Web UI（无需手动写 JSON），保存即下发
- Shadowsocks 8 种加密算法，DNS 按协议前缀自动识别（DoH / DoT / UDP / TCP）
- 探针式实时监控：内存采样 2s 推送，15s 过期；CPU / 内存 / 硬盘 / 网络速率 / 上下行累计
- 卡片网格 UI（Element Plus 风格），国旗 + GeoIP 自动回填
- 一键卸载（清干净 sing-box、agent、unit、配置）
- 部署幂等（sha256 比对 + version 检查）+ 旧版本平滑迁移
- JWT cookie 鉴权，首次访问设置管理员密码
- **前后端 + WebSocket 同端口**，一个端口走完所有流量，反代友好

## 一键管理（推荐）

在任意 Debian/Ubuntu/CentOS/RHEL/Fedora 主控机上，以 root 跑:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/huabanmao168/Mesh-Panel/main/install.sh)
```

会弹出菜单:

```
================ MeshPanel 管理菜单 ================
  版本: v1.0.0   服务: 运行中   端口: 8000
  访问: http://你的IP:8000
====================================================

  1) 安装 MeshPanel
  2) 更新到最新版
  3) 卸载 MeshPanel
  ----
  4) 启动服务
  5) 停止服务
  6) 重启服务
  7) 查看状态
  8) 查看日志 (实时)
  ----
  0) 退出
```

首次访问浏览器设置管理员密码即可。

安装完成后,以后调出菜单只需输入:

```bash
mesh
```

也支持直接传参非交互执行:

```bash
mesh update     # 更新
mesh restart    # 重启
mesh logs       # 实时日志
mesh uninstall  # 卸载
```

## 常用命令

```bash
systemctl status   meshpanel
systemctl restart  meshpanel
journalctl -u meshpanel -f
```

## 目录结构

```
mesh-panel/
├── agent/              # Go agent(节点侧)
├── backend/            # FastAPI 主控
│   ├── api/            # REST 路由
│   ├── deploy/         # install.sh / uninstall.sh 生成器(给节点用)
│   ├── models/         # SQLModel 数据模型
│   ├── ssh/            # paramiko SSH 封装
│   ├── ws/             # WebSocket(agent 回连)
│   └── agent_dist/     # agent 交叉编译产物(release 自动下载)
├── frontend/           # Vue 3 + Vite + Element Plus
│   └── dist/           # 构建产物(release 自动下载,生产由后端 serve)
├── docs/               # 设计文档
├── install.sh          # 一键管理脚本(菜单:安装/更新/卸载/启停/日志)
└── data/               # 运行时数据(SQLite + 上传的证书),不入库
```

## 开发模式

如果你想本地开发 / 改源码：

```bash
git clone https://github.com/huabanmao168/Mesh-Panel.git
cd Mesh-Panel

# 后端
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 前端 dev(另一个终端,HMR 热更新)
cd frontend
npm install
npm run dev   # :5173,自动反代 /api /ws 到 :8000
```

开发用 vite 跑 `:5173`，生产由 FastAPI 同端口 serve `frontend/dist/`，两套路径互不冲突。

## 编译 agent

正常情况下 GitHub Actions 自动出三平台二进制随 release 发布。本地手动编译：

```bash
cd agent
make all      # 输出 backend/agent_dist/mesh-agent-{amd64,arm64,armv7}
```

## 技术栈

- **主控后端**：FastAPI + SQLModel + paramiko + WebSocket
- **主控前端**：Vue 3 + Element Plus + Vite
- **节点 agent**：Go(单文件二进制，零依赖)
- **数据面**：sing-box(不锁版本，schema 抗 API 变动)

## 反向代理 / HTTPS

MeshPanel 默认监听 `0.0.0.0:8000`，前后端 + WebSocket 同端口。如需 HTTPS，推荐前面套 caddy 自动证书：

```caddy
panel.example.com {
    reverse_proxy localhost:8000
}
```

或在面板「设置」里直接配监听端口 / 上传证书走 443（开发中）。

## License

MIT
