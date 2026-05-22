#!/usr/bin/env bash
# MeshPanel 一键安装脚本
# 用法: bash <(curl -fsSL https://raw.githubusercontent.com/huabanmao168/Mesh-Panel/main/install.sh)
set -euo pipefail

REPO="huabanmao168/Mesh-Panel"
INSTALL_DIR="/opt/mesh-panel"
SERVICE_NAME="meshpanel"
DEFAULT_PORT="8000"

c_green='\033[32m'; c_red='\033[31m'; c_yellow='\033[33m'; c_blue='\033[34m'; c_reset='\033[0m'
log()  { echo -e "${c_blue}[*]${c_reset} $*"; }
ok()   { echo -e "${c_green}[\xe2\x9c\x93]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[!]${c_reset} $*"; }
die()  { echo -e "${c_red}[x]${c_reset} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行 (sudo bash ...)"

# ---- 检测包管理器 ----
if   command -v apt-get >/dev/null; then PM=apt
elif command -v dnf     >/dev/null; then PM=dnf
elif command -v yum     >/dev/null; then PM=yum
else die "不支持的发行版,仅支持 Debian/Ubuntu/CentOS/RHEL/Fedora"
fi
log "包管理器: $PM"

# ---- 装依赖 ----
log "安装依赖 (python3 venv git curl tar)..."
case $PM in
  apt) apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip git curl tar ca-certificates ;;
  dnf|yum) $PM install -y -q python3 python3-pip git curl tar ca-certificates ;;
esac
ok "依赖装好"

# ---- 取最新 release tag ----
log "查询最新 release..."
LATEST=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | grep -oP '"tag_name":\s*"\K[^"]+' || true)
[[ -n "$LATEST" ]] || die "拉不到 release tag,检查仓库是否已发版"
ok "最新版本: $LATEST"

# ---- 已安装? ----
if [[ -d "$INSTALL_DIR/.git" ]]; then
  warn "$INSTALL_DIR 已存在,如需更新请用 update.sh,如需重装先卸载: bash uninstall.sh"
  exit 0
fi

# ---- clone 仓库 ----
log "拉取源码到 $INSTALL_DIR..."
git clone -q --depth 1 --branch "$LATEST" "https://github.com/${REPO}.git" "$INSTALL_DIR"

# ---- 下载 release 产物 ----
BASE_URL="https://github.com/${REPO}/releases/download/${LATEST}"
log "下载前端 dist..."
curl -fsSL -o /tmp/frontend-dist.tar.gz "${BASE_URL}/frontend-dist.tar.gz"
tar -xzf /tmp/frontend-dist.tar.gz -C "$INSTALL_DIR/frontend"
rm -f /tmp/frontend-dist.tar.gz

log "下载 agent 二进制 (amd64/arm64/armv7)..."
mkdir -p "$INSTALL_DIR/backend/agent_dist"
for arch in amd64 arm64 armv7; do
  curl -fsSL -o "$INSTALL_DIR/backend/agent_dist/mesh-agent-${arch}" "${BASE_URL}/mesh-agent-${arch}"
  chmod +x "$INSTALL_DIR/backend/agent_dist/mesh-agent-${arch}"
done
ok "release 产物就绪"

# ---- 后端 venv + 依赖 ----
log "创建 Python venv 并装依赖..."
cd "$INSTALL_DIR/backend"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
ok "Python 依赖装好"

# ---- 写 systemd unit ----
log "写 systemd unit..."
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=MeshPanel control panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/backend
ExecStart=${INSTALL_DIR}/backend/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${DEFAULT_PORT}
Restart=on-failure
RestartSec=3
# 允许绑定 80/443 低端口(无需 root 用户的 systemd 写法,但我们直接用 root 跑也可以)
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q ${SERVICE_NAME}.service
systemctl restart ${SERVICE_NAME}.service
ok "systemd 已启用并启动"

# ---- 验证 ----
sleep 2
if curl -fsS "http://127.0.0.1:${DEFAULT_PORT}/api/health" >/dev/null; then
  ok "MeshPanel 健康检查通过"
else
  warn "健康检查失败,看日志: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

# ---- 输出访问信息 ----
PUB_IP=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo
echo "==========================================="
ok "MeshPanel ${LATEST} 安装完成"
echo
echo "  访问面板: http://${PUB_IP}:${DEFAULT_PORT}"
echo "  首次访问设置管理员密码"
echo
echo "  常用命令:"
echo "    systemctl status   ${SERVICE_NAME}"
echo "    systemctl restart  ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo
echo "  更新: bash <(curl -fsSL https://raw.githubusercontent.com/${REPO}/main/update.sh)"
echo "  卸载: bash <(curl -fsSL https://raw.githubusercontent.com/${REPO}/main/uninstall.sh)"
echo "==========================================="
