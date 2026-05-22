#!/usr/bin/env bash
# MeshPanel 卸载脚本
set -euo pipefail

INSTALL_DIR="/opt/mesh-panel"
SERVICE_NAME="meshpanel"

c_green='\033[32m'; c_yellow='\033[33m'; c_red='\033[31m'; c_blue='\033[34m'; c_reset='\033[0m'
log()  { echo -e "${c_blue}[*]${c_reset} $*"; }
ok()   { echo -e "${c_green}[\xe2\x9c\x93]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[!]${c_reset} $*"; }
die()  { echo -e "${c_red}[x]${c_reset} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行"

warn "即将卸载 MeshPanel:停止服务、删除 ${INSTALL_DIR}、删除 systemd unit"
warn "节点不会自动卸载,如需清理节点请先在面板里挨个点'卸载'"
read -p "确认继续? (yes/N): " ans
[[ "$ans" == "yes" ]] || { log "取消"; exit 0; }

log "停止并禁用服务..."
systemctl stop    ${SERVICE_NAME}.service 2>/dev/null || true
systemctl disable ${SERVICE_NAME}.service 2>/dev/null || true
rm -f /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true

log "删除安装目录..."
rm -rf "$INSTALL_DIR"

ok "MeshPanel 已卸载干净"
