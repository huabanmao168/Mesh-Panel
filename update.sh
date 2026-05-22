#!/usr/bin/env bash
# MeshPanel 更新脚本:拉最新 tag 的源码 + release 产物,重启服务
set -euo pipefail

REPO="huabanmao168/Mesh-Panel"
INSTALL_DIR="/opt/mesh-panel"
SERVICE_NAME="meshpanel"

c_green='\033[32m'; c_red='\033[31m'; c_blue='\033[34m'; c_reset='\033[0m'
log()  { echo -e "${c_blue}[*]${c_reset} $*"; }
ok()   { echo -e "${c_green}[\xe2\x9c\x93]${c_reset} $*"; }
die()  { echo -e "${c_red}[x]${c_reset} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行"
[[ -d "$INSTALL_DIR/.git" ]] || die "$INSTALL_DIR 不是 MeshPanel 安装目录,先跑 install.sh"

LATEST=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | grep -oP '"tag_name":\s*"\K[^"]+')
[[ -n "$LATEST" ]] || die "拉不到 release tag"
CURRENT=$(git -C "$INSTALL_DIR" describe --tags --always 2>/dev/null || echo "unknown")
log "当前: $CURRENT  →  最新: $LATEST"

if [[ "$CURRENT" == "$LATEST" ]]; then
  ok "已是最新版本,无需更新"
  exit 0
fi

log "拉取新代码..."
cd "$INSTALL_DIR"
git fetch -q --tags
git checkout -q "$LATEST"

BASE_URL="https://github.com/${REPO}/releases/download/${LATEST}"
log "更新前端 dist..."
curl -fsSL -o /tmp/frontend-dist.tar.gz "${BASE_URL}/frontend-dist.tar.gz"
rm -rf frontend/dist
tar -xzf /tmp/frontend-dist.tar.gz -C frontend
rm -f /tmp/frontend-dist.tar.gz

log "更新 agent 二进制..."
for arch in amd64 arm64 armv7; do
  curl -fsSL -o "backend/agent_dist/mesh-agent-${arch}" "${BASE_URL}/mesh-agent-${arch}"
  chmod +x "backend/agent_dist/mesh-agent-${arch}"
done

log "升级 Python 依赖..."
cd backend
.venv/bin/pip install -q -r requirements.txt

log "重启服务..."
systemctl restart ${SERVICE_NAME}.service
sleep 2
systemctl is-active --quiet ${SERVICE_NAME} && ok "MeshPanel 已更新到 ${LATEST}" || die "服务起不来,查 journalctl -u ${SERVICE_NAME}"
