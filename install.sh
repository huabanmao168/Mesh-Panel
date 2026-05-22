#!/usr/bin/env bash
# MeshPanel 一键管理脚本(菜单式:安装/更新/卸载/状态)
# 用法:
#   bash <(curl -fsSL https://raw.githubusercontent.com/huabanmao168/Mesh-Panel/main/install.sh)
# 已安装后也可以本地直接跑:
#   bash /opt/mesh-panel/install.sh
set -euo pipefail

REPO="huabanmao168/Mesh-Panel"
INSTALL_DIR="/opt/mesh-panel"
SERVICE_NAME="meshpanel"
DEFAULT_PORT="8000"

c_green='\033[32m'; c_red='\033[31m'; c_yellow='\033[33m'; c_blue='\033[34m'; c_cyan='\033[36m'; c_bold='\033[1m'; c_reset='\033[0m'
log()  { echo -e "${c_blue}[*]${c_reset} $*"; }
ok()   { echo -e "${c_green}[\xe2\x9c\x93]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[!]${c_reset} $*"; }
err()  { echo -e "${c_red}[x]${c_reset} $*" >&2; }
die()  { err "$*"; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行 (sudo bash ...)"

# ---- 检测包管理器 ----
detect_pm() {
  if   command -v apt-get >/dev/null; then PM=apt
  elif command -v dnf     >/dev/null; then PM=dnf
  elif command -v yum     >/dev/null; then PM=yum
  else die "不支持的发行版,仅支持 Debian/Ubuntu/CentOS/RHEL/Fedora"
  fi
}

install_deps() {
  log "安装依赖 (python3 venv git curl tar)..."
  case $PM in
    apt) apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip git curl tar ca-certificates ;;
    dnf|yum) $PM install -y -q python3 python3-pip git curl tar ca-certificates ;;
  esac
  ok "依赖装好"
}

get_latest_tag() {
  curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep -oP '"tag_name":\s*"\K[^"]+' || true
}

current_version() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" describe --tags --always 2>/dev/null || echo "unknown"
  else
    echo "未安装"
  fi
}

service_status() {
  if systemctl is-active --quiet ${SERVICE_NAME}.service 2>/dev/null; then
    echo "running"
  elif systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    echo "stopped"
  else
    echo "absent"
  fi
}

panel_port_in_db() {
  local db="$INSTALL_DIR/data/app.db"
  [[ -f "$db" ]] || { echo "$DEFAULT_PORT"; return; }
  local venv="$INSTALL_DIR/backend/.venv/bin/python"
  [[ -x "$venv" ]] || { echo "$DEFAULT_PORT"; return; }
  "$venv" -c "
import sqlite3
c = sqlite3.connect('$db')
r = c.execute(\"select value from settings where key='panel_port'\").fetchone()
print(r[0] if r else '$DEFAULT_PORT')
" 2>/dev/null || echo "$DEFAULT_PORT"
}

# ---------------- 安装 ----------------
do_install() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    warn "$INSTALL_DIR 已存在,如需更新请选[2],如需重装请先选[3]卸载"
    return
  fi
  detect_pm
  install_deps

  log "查询最新 release..."
  LATEST=$(get_latest_tag)
  [[ -n "$LATEST" ]] || die "拉不到 release tag,检查仓库是否已发版"
  ok "最新版本: $LATEST"

  log "拉取源码到 $INSTALL_DIR..."
  git clone -q --depth 1 --branch "$LATEST" "https://github.com/${REPO}.git" "$INSTALL_DIR"

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

  log "创建 Python venv 并装依赖..."
  cd "$INSTALL_DIR/backend"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  ok "Python 依赖装好"

  write_systemd_unit
  systemctl daemon-reload
  systemctl enable -q ${SERVICE_NAME}.service
  systemctl restart ${SERVICE_NAME}.service
  ok "systemd 已启用并启动"

  sleep 2
  local port; port=$(panel_port_in_db)
  if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    ok "MeshPanel 健康检查通过"
  else
    warn "健康检查失败,看日志: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
  fi

  local pub_ip
  pub_ip=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  echo
  echo "==========================================="
  ok "MeshPanel ${LATEST} 安装完成"
  echo
  echo "  访问面板: ${c_cyan}http://${pub_ip}:${port}${c_reset}"
  echo "  首次访问设置管理员密码"
  echo
  echo "  本地再次运行此菜单: ${c_cyan}bash $INSTALL_DIR/install.sh${c_reset}"
  echo "==========================================="
}

write_systemd_unit() {
  log "写 systemd unit..."
  cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=MeshPanel control panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/backend
ExecStart=${INSTALL_DIR}/backend/.venv/bin/python ${INSTALL_DIR}/backend/run_server.py
Restart=on-failure
RestartSec=3
# 允许绑定 80/443 低端口
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

# ---------------- 更新 ----------------
do_update() {
  [[ -d "$INSTALL_DIR/.git" ]] || { warn "$INSTALL_DIR 不是 MeshPanel 安装目录,先选[1]安装"; return; }
  LATEST=$(get_latest_tag)
  [[ -n "$LATEST" ]] || die "拉不到 release tag"
  CURRENT=$(current_version)
  log "当前: $CURRENT  →  最新: $LATEST"
  if [[ "$CURRENT" == "$LATEST" ]]; then
    ok "已是最新版本"
    return
  fi
  read -p "确认更新到 $LATEST? (y/N): " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { log "取消"; return; }

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
  cd backend && .venv/bin/pip install -q -r requirements.txt

  # 重写 systemd unit(可能新版换了启动命令)
  write_systemd_unit
  systemctl daemon-reload

  log "重启服务..."
  systemctl restart ${SERVICE_NAME}.service
  sleep 2
  if systemctl is-active --quiet ${SERVICE_NAME}; then
    ok "MeshPanel 已更新到 ${LATEST}"
  else
    err "服务起不来,看 journalctl -u ${SERVICE_NAME} -n 30 --no-pager"
  fi
}

# ---------------- 卸载 ----------------
do_uninstall() {
  warn "即将卸载 MeshPanel: 停止服务、删除 ${INSTALL_DIR}、移除 systemd unit"
  warn "节点不会自动卸载,如需清理节点请先在面板里挨个点'卸载'"
  read -p "输入 yes 确认: " ans
  [[ "$ans" == "yes" ]] || { log "取消"; return; }

  log "停止并禁用服务..."
  systemctl stop    ${SERVICE_NAME}.service 2>/dev/null || true
  systemctl disable ${SERVICE_NAME}.service 2>/dev/null || true
  rm -f /etc/systemd/system/${SERVICE_NAME}.service
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true

  log "删除安装目录..."
  rm -rf "$INSTALL_DIR"

  ok "MeshPanel 已卸载干净"
}

# ---------------- 启停/日志 ----------------
do_restart()  { systemctl restart ${SERVICE_NAME}.service && ok "已重启"; }
do_stop()     { systemctl stop    ${SERVICE_NAME}.service && ok "已停止"; }
do_start()    { systemctl start   ${SERVICE_NAME}.service && ok "已启动"; }
do_status()   { systemctl status  ${SERVICE_NAME}.service --no-pager -l | head -20; }
do_logs()     { echo "Ctrl+C 退出"; sleep 1; journalctl -u ${SERVICE_NAME} -f --no-pager; }

# ---------------- 菜单 ----------------
print_header() {
  clear
  local ver st port pubip
  ver=$(current_version)
  st=$(service_status)
  port=$(panel_port_in_db)
  pubip=$(hostname -I 2>/dev/null | awk '{print $1}')
  case "$st" in
    running) st_color="${c_green}运行中${c_reset}";;
    stopped) st_color="${c_yellow}已停止${c_reset}";;
    absent)  st_color="${c_red}未安装${c_reset}";;
  esac
  echo -e "${c_bold}${c_cyan}================ MeshPanel 管理菜单 ================${c_reset}"
  echo -e "  版本: ${c_bold}${ver}${c_reset}   服务: ${st_color}   端口: ${c_bold}${port}${c_reset}"
  [[ -n "$pubip" ]] && echo -e "  访问: ${c_cyan}http://${pubip}:${port}${c_reset}"
  echo -e "${c_cyan}====================================================${c_reset}"
}

menu() {
  while true; do
    print_header
    cat <<EOF

  ${c_bold}1)${c_reset} 安装 MeshPanel
  ${c_bold}2)${c_reset} 更新到最新版
  ${c_bold}3)${c_reset} 卸载 MeshPanel
  ----
  ${c_bold}4)${c_reset} 启动服务
  ${c_bold}5)${c_reset} 停止服务
  ${c_bold}6)${c_reset} 重启服务
  ${c_bold}7)${c_reset} 查看状态
  ${c_bold}8)${c_reset} 查看日志 (实时)
  ----
  ${c_bold}0)${c_reset} 退出

EOF
    read -p "请选择 [0-8]: " choice
    echo
    case "$choice" in
      1) do_install ;;
      2) do_update ;;
      3) do_uninstall ;;
      4) do_start ;;
      5) do_stop ;;
      6) do_restart ;;
      7) do_status ;;
      8) do_logs ;;
      0) exit 0 ;;
      *) warn "无效选择" ;;
    esac
    echo
    read -p "回车返回菜单..." _
  done
}

# ---- 非交互快捷参数 (用于脚本自动化) ----
case "${1:-}" in
  install)   do_install ;;
  update)    do_update ;;
  uninstall) do_uninstall ;;
  start)     do_start ;;
  stop)      do_stop ;;
  restart)   do_restart ;;
  status)    do_status ;;
  logs)      do_logs ;;
  ""|menu)   menu ;;
  *) die "未知命令: $1 (支持: install|update|uninstall|start|stop|restart|status|logs|menu)" ;;
esac
