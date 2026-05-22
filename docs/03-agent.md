# 第 3 块：agent 回连

## 目标

节点装一个常驻 Go agent，跟主控建立 **WebSocket 长连**，干两件事：

1. **心跳** —— 每 10 秒一个 ping，主控据此判定节点"在线 / 离线"
2. **接收即时指令** —— 主控想让节点 `reload sing-box` 的时候不用再 SSH，发个 `{"cmd":"reload"}` 过去就行（流量上报留给第 5 块）

跑完前端节点列表多一列"agent 状态"，绿点表示在线、灰点表示离线、最后心跳时间。

---

## 架构

```
┌────────────────┐                       ┌────────────────┐
│   主控          │  SSH（装机/推配置）  │   节点          │
│  FastAPI       │ ────────────────────> │  sing-box       │
│  + WS 服务     │                       │  + socks-agent  │
│                │  WS（心跳/指令）      │                 │
│                │ <──────────────────── │                 │
└────────────────┘                       └────────────────┘
```

- **SSH 通道**：管理员操作通道（装机、推配置、改 agent 环境变量）—— 加密的，敏感数据全走这
- **WS 通道（ws://）**：运行时数据通道 —— 只跑心跳 / 流量计数 / "reload" 触发，**永远不过敏感数据**，所以裸 ws:// ok

---

## 关键决策（已敲定）

1. **agent 二进制怎么发**：主控本机交叉编译 amd64 / arm64 / armv7 三个版本，塞进 `backend/agent_dist/`，install.sh 走 SSH `cat > /opt/mesh-panel/socks-agent` 推过去（不联外网）
2. **鉴权**：每节点独立 token（uuid4），生成时存数据库 + 写节点的 `/etc/mesh-panel/agent.env`，agent 连 WS 时 URL query 带 token，主控查表对得上才接
3. **WS 端点**：同 FastAPI 端口，路径 `ws://主控:8000/ws/node?token=xxx`
4. **主控公网地址**：**不写死**，放数据库 `settings` 表的 `agent_endpoint` 字段，前端"全局设置"页填，部署节点和后续推送都读它
5. **协议**：裸 ws://，配置下发继续走 SSH，WS 不过敏感数据

---

## 数据库改动

### 新表 `settings`（第 2 块文档里提过，本块真正建）

| 字段 | 类型 | 说明 |
|---|---|---|
| key | str PK | 设置项名 |
| value | text | 值（JSON 序列化或直接字符串） |
| updated_at | datetime | |

初始项：
- `agent_endpoint` —— 主控的 WS 公网地址，例如 `ws://1.2.3.4:8000` 或 `wss://panel.example.com`，**首次启动为空**

### `nodes` 表加列

| 字段 | 类型 | 说明 |
|---|---|---|
| agent_token | str? | uuid4，部署时生成，**敏感数据，不下发** |
| agent_status | str | `offline` / `online`，默认 offline |
| agent_last_seen | datetime? | 最后一次心跳时间 |
| agent_version | str? | agent 上报的自己版本号 |

---

## API 改动

### 全局设置

- `GET /api/settings` —— 读全部（agent_endpoint 等）
- `PATCH /api/settings` —— 批量改

### 节点（新增）

- `POST /api/nodes/{id}/agent/redeploy-config` —— 仅推 `agent.env`（改了 endpoint 之后批量调用）
- WS `GET /ws/node?token=xxx` —— agent 长连端点

### 节点（修改 deploy 行为）

部署时多干两件事：
1. 如果节点没 `agent_token`，生成一个 uuid4 存库
2. 装机脚本里追加：写 `/etc/mesh-panel/agent.env` + 推 agent 二进制 + 装 `socks-agent.service`

---

## install.sh 追加段

第 2 块的脚本最后加一段（接在 sing-box 启动之后、RESULT 标记之前）：

```bash
# ---------- 8. agent ----------
# 通过环境变量从主控传进来：AGENT_TOKEN / AGENT_ENDPOINT / AGENT_VERSION
mkdir -p /etc/mesh-panel
cat > /etc/mesh-panel/agent.env <<EOF
MESH_AGENT_TOKEN=${AGENT_TOKEN}
MESH_AGENT_ENDPOINT=${AGENT_ENDPOINT}
MESH_AGENT_NODE_ID=${AGENT_NODE_ID}
EOF
chmod 600 /etc/mesh-panel/agent.env

# 二进制由主控通过 base64 stdin 推过来，install.sh 里这段会被 Python 端动态替换
# 占位：实际由 installer.py 在调用前 inject
# (具体实现见 installer.py 的 _send_agent_binary)

cat > /etc/systemd/system/socks-agent.service <<EOF
[Unit]
Description=mesh-panel agent
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/mesh-panel/agent.env
ExecStart=/opt/mesh-panel/socks-agent
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable socks-agent.service >/dev/null 2>&1 || true
systemctl restart socks-agent.service
sleep 1
AGENT_STATUS=$(systemctl is-active socks-agent.service || true)
echo "[8/8] socks-agent status: $AGENT_STATUS"
```

**二进制推送怎么做**：installer.py 用 SFTP 把 `backend/agent_dist/socks-agent-{arch}` 上传到 `/opt/mesh-panel/socks-agent`，比 base64 走 stdin 干净。

---

## Go agent 设计

### 目录

```
/root/mesh-panel/agent/
├── go.mod
├── main.go             入口 + 配置读取
├── ws.go               WebSocket 客户端（带重连）
├── version.go          版本号常量
└── Makefile            交叉编译三个架构
```

### 依赖

只用一个第三方库：`github.com/gorilla/websocket`。其他全标准库。

### 行为

1. 启动时读环境变量 `MESH_AGENT_TOKEN` / `MESH_AGENT_ENDPOINT` / `MESH_AGENT_NODE_ID`，缺一报错退出（systemd 会重启，让用户看 journalctl）
2. 拼接 URL：`{endpoint}/ws/node?token={token}&node_id={id}&version={ver}`
3. **拨号 + 自动重连**：连不上等 5s 重试，连上后开始心跳循环
4. **心跳**：每 10s 发 `{"type":"ping","ts":...}`，主控回 `{"type":"pong"}`
5. **接指令**：另起 goroutine 读 ws，收到 `{"type":"cmd","action":"reload"}` 就跑 `systemctl reload sing-box`
6. **优雅退出**：SIGTERM 时关连接再退

### 心跳消息格式

```json
// agent → 主控
{ "type": "ping", "ts": 1716459000, "uptime": 12345 }

// 主控 → agent
{ "type": "pong" }
{ "type": "cmd", "action": "reload" }
{ "type": "cmd", "action": "restart" }   // 备用
```

第 5 块再加流量上报消息。

---

## 主控 WS 服务端

`backend/ws/agents.py`（新增）：

```python
from fastapi import WebSocket, WebSocketDisconnect, Query
from datetime import datetime

router = APIRouter()

# node_id -> WebSocket 映射，下发指令用
_connections: dict[int, WebSocket] = {}

@router.websocket("/ws/node")
async def node_ws(ws: WebSocket, token: str = Query(...), node_id: int = Query(...)):
    # 1. 鉴权
    node = session.query(Node).filter_by(id=node_id, agent_token=token).first()
    if not node:
        await ws.close(code=4401)
        return
    await ws.accept()
    _connections[node_id] = ws

    # 2. 标记在线
    node.agent_status = "online"
    node.agent_last_seen = datetime.utcnow()
    session.commit()

    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                node.agent_last_seen = datetime.utcnow()
                session.commit()
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        _connections.pop(node_id, None)
        node.agent_status = "offline"
        session.commit()
```

**下发指令**：

```python
async def send_cmd(node_id: int, action: str) -> bool:
    ws = _connections.get(node_id)
    if not ws:
        return False
    await ws.send_json({"type": "cmd", "action": action})
    return True
```

### 离线检测后台任务

`asyncio` 后台任务每 30s 扫一次：如果某节点 `agent_last_seen` 超过 60s 没更新，强制 `agent_status = offline`（兜底，防止 WS 异常断开没触发 finally）。

---

## 前端改动

### 全局设置页（新增）

顶部导航加一个齿轮按钮，点开弹"系统设置"对话框：

```
┌─ 系统设置 ────────────────────────────────┐
│ 主控公网地址 (Agent Endpoint):           │
│   [ws://1.2.3.4:8000          ]          │
│   节点 agent 通过此地址回连主控          │
│                                          │
│        [取消]  [保存]                    │
└──────────────────────────────────────────┘
```

保存后调 `PATCH /api/settings`。

### 节点列表

- "agent 状态"列：绿点 + "在线" / 灰点 + "离线"，旁边显示最后心跳相对时间（"5 秒前"）
- 详情抽屉加一栏 agent 信息

### 首次启动防呆

主页 mount 时调 `GET /api/settings`，如果 `agent_endpoint` 为空，顶部塞一个红条：
```
⚠ 主控公网地址未配置，新部署的节点 agent 无法回连。 [立即配置]
```

### 修改 endpoint 后批量推送

设置页保存按钮旁加一个"保存并推送到所有已部署节点"，调用 `/api/nodes/{id}/agent/redeploy-config` 循环。

---

## 节点目录最终样子

```
/opt/mesh-panel/
├── sing-box           # 第 2 块
├── socks-agent        # 本块新增，Go 二进制
├── version            # sing-box 版本
└── README

/etc/mesh-panel/
└── agent.env          # 本块新增

/etc/sing-box/
└── config.json

/etc/systemd/system/
├── sing-box.service
└── socks-agent.service  # 本块新增
```

---

## Go 交叉编译

`agent/Makefile`：

```makefile
DIST = ../backend/agent_dist

.PHONY: all clean

all: $(DIST)/socks-agent-amd64 $(DIST)/socks-agent-arm64 $(DIST)/socks-agent-armv7

$(DIST)/socks-agent-amd64:
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="-s -w" -o $@ .

$(DIST)/socks-agent-arm64:
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="-s -w" -o $@ .

$(DIST)/socks-agent-armv7:
	GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -ldflags="-s -w" -o $@ .

clean:
	rm -f $(DIST)/socks-agent-*
```

主控构建一次，三个二进制塞进 `backend/agent_dist/`，installer 根据节点 arch 选对应的推上去。

**主控需要装 Go**（开发期）：`apt install golang` 或下载 1.22+。第一次跑 `cd agent && make` 编译，之后改了 agent 源码也是 `make` 一下。

---

## 不在本块做的事

- ❌ 流量上报（第 5 块）
- ❌ 配置真推送（第 4 块）—— 本块的"reload"指令只是触发信号，没有配置内容
- ❌ wss:// + TLS —— 决策已定，配置走 SSH，WS 不过敏感数据
- ❌ agent 自升级 —— 后面再说

---

## 验收标准

1. 主控前端"系统设置"能填 `agent_endpoint`，保存生效
2. 部署一台新节点（第 2 块流程不变），完成后节点列表 agent 状态变绿"在线"
3. SSH 到节点 `systemctl status socks-agent` active，`journalctl -u socks-agent` 看到 "connected" 日志
4. 故意 kill 节点上的 agent 进程，主控 60s 内把它标灰"离线"，systemd 再把它拉起来，又变绿
5. 故意把 `agent.env` 里 token 改错重启 agent，主控 WS 拒接（4401），journalctl 看到 "auth failed"
6. （备用）改 endpoint → 点"批量推送"按钮 → 所有节点 agent.env 更新 + 重启 → 重新连上

---

## 等你确认

方向 ojbk 不？特别这几点：

- **主控本机交叉编译 Go**（你这台机器装 Go 一次性的事）
- **agent token uuid4**，每节点一个，存数据库 + 节点 `/etc/mesh-panel/agent.env`（chmod 600）
- **agent endpoint 全配置在 Web**，前端"系统设置"页填，首次启动红条提示
- **离线检测 60s 超时**（心跳 10s，连续 6 个心跳没到就判离线）

没问题就说"开写第 3 块"，我先装 Go，再撸 agent + WS 服务端 + 设置页。
