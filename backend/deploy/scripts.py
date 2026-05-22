"""节点安装脚本 —— 通过 SSH 的 `bash -s` 喂给远端，不在节点落盘。

约定：脚本末尾会打印一行
    ---MESH-PANEL-RESULT--- {"ok":true,"version":"1.10.7","arch":"amd64","singbox_status":"active"}
主控解析这行 JSON 收尾。前面的输出全留在 deploy_log。

路径全部自包含在 /opt/meshPanel/：
  /opt/meshPanel/sing-box       二进制
  /opt/meshPanel/mesh-agent     二进制
  /opt/meshPanel/config.json    sing-box 配置
  /opt/meshPanel/agent.env      agent 环境变量
  /opt/meshPanel/clash.sock     clash_api UDS（运行时）
"""

INSTALL_SH = r"""#!/usr/bin/env bash
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "需要 root"; exit 1; }

# ---------- 1. 架构检测 ----------
case "$(uname -m)" in
  x86_64)  ARCH=amd64 ;;
  aarch64) ARCH=arm64 ;;
  armv7l)  ARCH=armv7 ;;
  *) echo "unsupported arch: $(uname -m)"; exit 1 ;;
esac
echo "[1/10] arch=$ARCH"

# ---------- 2. 解析最新版本 ----------
echo "[2/10] resolving latest sing-box version..."
if command -v curl >/dev/null 2>&1; then
  DL() { curl -fsSL "$1"; }
else
  DL() { wget -q -O - "$1"; }
fi

LATEST_TAG=$(DL "https://api.github.com/repos/SagerNet/sing-box/releases/latest" \
  | grep -oE '"tag_name"[^"]*"v[0-9]+\.[0-9]+\.[0-9]+"' \
  | head -1 \
  | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')

if [ -z "${LATEST_TAG:-}" ]; then
  echo "无法解析 sing-box 最新版本（GitHub API 不通？）"
  exit 1
fi
VER="${LATEST_TAG#v}"
echo "    latest = $VER"

# ---------- 3. 准备目录 ----------
mkdir -p /opt/meshPanel
cd /tmp
echo "[3/10] dirs ready"

# ---------- 4. 下载并解压 sing-box（已是最新版则跳过）----------
SKIP_SB_INSTALL=0
if [ -x /opt/meshPanel/sing-box ]; then
  CUR_VER=$(/opt/meshPanel/sing-box version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
  if [ "${CUR_VER:-}" = "$VER" ]; then
    echo "[4/10] sing-box $CUR_VER already installed, skip download"
    SKIP_SB_INSTALL=1
  else
    echo "[4/10] upgrading sing-box ${CUR_VER:-none} → $VER"
  fi
fi

if [ "$SKIP_SB_INSTALL" = "0" ]; then
  TGZ="sing-box-${VER}-linux-${ARCH}.tar.gz"
  URL="https://github.com/SagerNet/sing-box/releases/download/v${VER}/${TGZ}"
  echo "    downloading $URL"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$TGZ"
  else
    wget -q "$URL" -O "$TGZ"
  fi

  tar -xzf "$TGZ"
  # 升级前先停服务避免 "text file busy"
  if [ "${CUR_VER:-}" != "" ]; then
    systemctl stop sing-box.service 2>/dev/null || true
  fi
  install -m 0755 "sing-box-${VER}-linux-${ARCH}/sing-box" /opt/meshPanel/sing-box
  echo "$VER" > /opt/meshPanel/version
  rm -rf "$TGZ" "sing-box-${VER}-linux-${ARCH}"
  echo "    installed: $(/opt/meshPanel/sing-box version | head -1)"
fi

# ---------- 5. 占位配置（已存在则保留，避免覆盖运行中配置）----------
if [ ! -f /opt/meshPanel/config.json ]; then
  cat > /opt/meshPanel/config.json <<'EOF'
{
  "log": { "level": "info", "timestamp": true },
  "inbounds": [],
  "outbounds": [{ "type": "direct", "tag": "direct" }]
}
EOF
  echo "[5/10] placeholder config created"
else
  echo "[5/10] config exists, keep it"
fi
/opt/meshPanel/sing-box check -c /opt/meshPanel/config.json

# ---------- 6. sing-box systemd（unit 内容变化才重写）----------
SB_UNIT=/etc/systemd/system/sing-box.service
SB_UNIT_NEW=$(mktemp)
cat > "$SB_UNIT_NEW" <<'EOF'
[Unit]
Description=sing-box (managed by MeshPanel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=-/bin/rm -f /opt/meshPanel/clash.sock
ExecStartPre=/opt/meshPanel/sing-box check -c /opt/meshPanel/config.json
ExecStart=/opt/meshPanel/sing-box run -c /opt/meshPanel/config.json
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

SB_UNIT_CHANGED=0
if ! cmp -s "$SB_UNIT_NEW" "$SB_UNIT" 2>/dev/null; then
  mv "$SB_UNIT_NEW" "$SB_UNIT"
  SB_UNIT_CHANGED=1
else
  rm -f "$SB_UNIT_NEW"
fi

if [ "$SB_UNIT_CHANGED" = "1" ] || [ "$SKIP_SB_INSTALL" = "0" ]; then
  systemctl daemon-reload
  systemctl enable sing-box.service >/dev/null 2>&1 || true
  systemctl restart sing-box.service
  echo "[6/10] sing-box restarted"
else
  if ! systemctl is-active --quiet sing-box.service; then
    systemctl start sing-box.service
    echo "[6/10] sing-box started"
  else
    echo "[6/10] sing-box already running, skip restart"
  fi
fi
sleep 1
SB_STATUS=$(systemctl is-active sing-box.service || true)
echo "    sing-box status: $SB_STATUS"

# ---------- 7. agent.env（总是覆盖，token/endpoint 可能变）----------
NEW_ENV=$(mktemp)
cat > "$NEW_ENV" <<EOF
MESH_AGENT_TOKEN=${AGENT_TOKEN}
MESH_AGENT_ENDPOINT=${AGENT_ENDPOINT}
MESH_AGENT_NODE_ID=${AGENT_NODE_ID}
EOF
AGENT_ENV_CHANGED=0
if ! cmp -s "$NEW_ENV" /opt/meshPanel/agent.env 2>/dev/null; then
  mv "$NEW_ENV" /opt/meshPanel/agent.env
  AGENT_ENV_CHANGED=1
else
  rm -f "$NEW_ENV"
fi
chmod 600 /opt/meshPanel/agent.env
echo "[7/10] agent.env $([ "$AGENT_ENV_CHANGED" = "1" ] && echo updated || echo unchanged)"

# ---------- 8. agent systemd ----------
AGENT_BIN_CHANGED="${AGENT_BIN_CHANGED:-0}"  # installer.py 上传 agent 后通过 env 传入

# 清理旧 socks-agent 残留（早期版本叫这个名）
if [ -f /etc/systemd/system/socks-agent.service ] || [ -f /opt/meshPanel/socks-agent ]; then
  systemctl disable --now socks-agent.service 2>/dev/null || true
  rm -f /etc/systemd/system/socks-agent.service /opt/meshPanel/socks-agent
  systemctl daemon-reload 2>/dev/null || true
  echo "[8/10] cleaned legacy socks-agent"
  AGENT_BIN_CHANGED=1
fi

if [ ! -x /opt/meshPanel/mesh-agent ]; then
  echo "[8/10] WARN: /opt/meshPanel/mesh-agent missing, skip agent setup"
  AG_STATUS="missing"
else
  AGENT_UNIT=/etc/systemd/system/mesh-agent.service
  AGENT_UNIT_NEW=$(mktemp)
  cat > "$AGENT_UNIT_NEW" <<'EOF'
[Unit]
Description=MeshPanel agent
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/opt/meshPanel/agent.env
ExecStart=/opt/meshPanel/mesh-agent
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
  AGENT_UNIT_CHANGED=0
  if ! cmp -s "$AGENT_UNIT_NEW" "$AGENT_UNIT" 2>/dev/null; then
    mv "$AGENT_UNIT_NEW" "$AGENT_UNIT"
    AGENT_UNIT_CHANGED=1
  else
    rm -f "$AGENT_UNIT_NEW"
  fi

  if [ "$AGENT_UNIT_CHANGED" = "1" ] || [ "$AGENT_BIN_CHANGED" = "1" ] || [ "$AGENT_ENV_CHANGED" = "1" ]; then
    systemctl daemon-reload
    systemctl enable mesh-agent.service >/dev/null 2>&1 || true
    systemctl restart mesh-agent.service
    echo "    mesh-agent restarted (unit_changed=$AGENT_UNIT_CHANGED bin_changed=$AGENT_BIN_CHANGED env_changed=$AGENT_ENV_CHANGED)"
  else
    if ! systemctl is-active --quiet mesh-agent.service; then
      systemctl start mesh-agent.service
      echo "    mesh-agent started"
    else
      echo "    mesh-agent already running, skip restart"
    fi
  fi
  sleep 1
  AG_STATUS=$(systemctl is-active mesh-agent.service || true)
  echo "[8/10] mesh-agent status: $AG_STATUS"
fi

# ---------- 9. README ----------
cat > /opt/meshPanel/README <<'EOF'
This machine is managed by MeshPanel.
All files self-contained under /opt/meshPanel/:
  sing-box        binary
  mesh-agent      binary
  config.json     sing-box config
  agent.env       agent environment
  clash.sock      clash_api UDS (runtime, recreated by sing-box)
Uninstall (or use the panel's one-click uninstall):
  systemctl disable --now sing-box mesh-agent
  rm -f /etc/systemd/system/sing-box.service /etc/systemd/system/mesh-agent.service
  systemctl daemon-reload
  rm -rf /opt/meshPanel
EOF
echo "[9/10] README written"

# ---------- 10. 结束 ----------
echo "[10/10] done"

# ---------- 结果输出 ----------
echo "---MESH-PANEL-RESULT---"
printf '{"ok":true,"version":"%s","arch":"%s","singbox_status":"%s","agent_status":"%s"}\n' \
  "$VER" "$ARCH" "$SB_STATUS" "$AG_STATUS"
"""
