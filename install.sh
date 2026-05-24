#!/usr/bin/env bash
# MeshPanel 一键管理脚本(单二进制版,菜单式:安装/更新/卸载/状态)
# 用法:
#   bash <(curl -fsSL https://raw.githubusercontent.com/huabanmao168/Mesh-Panel/main/install.sh)
# 已安装后输入 `mesh` 也能调出菜单。
set -euo pipefail

REPO="huabanmao168/Mesh-Panel"
INSTALL_DIR="/opt/mesh-panel"
BINARY_PATH="${INSTALL_DIR}/mesh-panel"
SERVICE_NAME="meshpanel"
DEFAULT_PORT="8000"
ASSET_NAME="mesh-panel-linux-amd64"

c_green='\033[32m'; c_red='\033[31m'; c_yellow='\033[33m'; c_blue='\033[34m'; c_cyan='\033[36m'; c_bold='\033[1m'; c_reset='\033[0m'
log()  { echo -e "${c_blue}[*]${c_reset} $*"; }
ok()   { echo -e "${c_green}[\xe2\x9c\x93]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[!]${c_reset} $*"; }
err()  { echo -e "${c_red}[x]${c_reset} $*" >&2; }
die()  { err "$*"; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行 (sudo bash ...)"

# ---- 架构检测 ----
detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) die "暂未提供 arm64 二进制,请使用源码安装或开 issue 申请" ;;
    *) die "不支持的架构: $(uname -m)" ;;
  esac
}

install_deps() {
  # 单二进制只需要 curl,基本所有系统都自带,这里兜底装一下
  if ! command -v curl >/dev/null; then
    log "装 curl..."
    if   command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq curl ca-certificates
    elif command -v dnf >/dev/null; then dnf install -y -q curl ca-certificates
    elif command -v yum >/dev/null; then yum install -y -q curl ca-certificates
    else die "请先手动装 curl"
    fi
  fi
}

ensure_sqlite3() {
  command -v sqlite3 >/dev/null && return 0
  log "装 sqlite3..."
  if   command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq sqlite3
  elif command -v dnf >/dev/null; then dnf install -y -q sqlite
  elif command -v yum >/dev/null; then yum install -y -q sqlite
  else die "请先手动装 sqlite3"
  fi
}

get_latest_tag() {
  curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep -oP '"tag_name":\s*"\K[^"]+' || true
}

current_version() {
  if [[ -x "$BINARY_PATH" ]] && [[ -f "${INSTALL_DIR}/VERSION" ]]; then
    cat "${INSTALL_DIR}/VERSION"
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
  local db="${INSTALL_DIR}/data/app.db"
  [[ -f "$db" ]] || { echo "$DEFAULT_PORT"; return; }
  if command -v sqlite3 >/dev/null; then
    sqlite3 "$db" "select value from settings where key='panel_port'" 2>/dev/null || echo "$DEFAULT_PORT"
  else
    echo "$DEFAULT_PORT"
  fi
}

download_binary() {
  local version="$1"
  local url="https://github.com/${REPO}/releases/download/${version}/${ASSET_NAME}"
  log "下载 ${ASSET_NAME} (${version})..."
  mkdir -p "$INSTALL_DIR"
  # 先下到临时文件再原子替换,避免覆盖中途崩了
  curl -fsSL --retry 3 -o "${BINARY_PATH}.new" "$url" \
    || die "下载失败: $url"
  chmod +x "${BINARY_PATH}.new"
  mv -f "${BINARY_PATH}.new" "$BINARY_PATH"
  echo "$version" > "${INSTALL_DIR}/VERSION"
  ok "二进制就绪: $BINARY_PATH"
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
WorkingDirectory=${INSTALL_DIR}
Environment=MESH_PANEL_HOME=${INSTALL_DIR}
ExecStart=${BINARY_PATH}
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

install_shortcut() {
  # /usr/local/bin/mesh -> 本脚本副本,让用户输入 mesh 即可打开菜单
  cp -f "$0" "${INSTALL_DIR}/install.sh" 2>/dev/null || \
    curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/install.sh" -o "${INSTALL_DIR}/install.sh"
  chmod +x "${INSTALL_DIR}/install.sh"
  cat > /usr/local/bin/mesh <<EOF
#!/usr/bin/env bash
exec bash ${INSTALL_DIR}/install.sh "\$@"
EOF
  chmod +x /usr/local/bin/mesh
  ok "已创建快捷命令: mesh"
}

# ---------------- 安装 ----------------
do_install() {
  if [[ -x "$BINARY_PATH" ]]; then
    warn "$BINARY_PATH 已存在,如需更新请选[2],如需重装请先选[3]卸载"
    return
  fi
  detect_arch
  install_deps

  log "查询最新 release..."
  LATEST=$(get_latest_tag)
  [[ -n "$LATEST" ]] || die "拉不到 release tag,检查仓库是否已发版"
  ok "最新版本: $LATEST"

  download_binary "$LATEST"

  write_systemd_unit
  install_shortcut
  systemctl daemon-reload
  systemctl enable -q ${SERVICE_NAME}.service
  systemctl restart ${SERVICE_NAME}.service
  ok "systemd 已启用并启动"

  sleep 3
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
  echo -e "  访问面板: ${c_cyan}http://${pub_ip}:${port}${c_reset}"
  echo -e "  默认账号: ${c_bold}admin${c_reset} / ${c_bold}admin123456${c_reset}"
  echo -e "  ${c_yellow}登录后请立刻改密!${c_reset}"
  echo
  echo -e "  以后调出菜单只需输入: ${c_cyan}mesh${c_reset}"
  echo "==========================================="
}

# ---------------- 更新 ----------------
do_update() {
  [[ -x "$BINARY_PATH" ]] || { warn "未安装,先选[1]安装"; return; }
  detect_arch
  install_deps

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

  download_binary "$LATEST"
  write_systemd_unit
  install_shortcut
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
  read -p "是否同时删除数据 (data/)? 删了节点+证书+密钥全没 [y/N]: " del_data
  read -p "输入 yes 确认卸载: " ans
  [[ "$ans" == "yes" ]] || { log "取消"; return; }

  log "停止并禁用服务..."
  systemctl stop    ${SERVICE_NAME}.service 2>/dev/null || true
  systemctl disable ${SERVICE_NAME}.service 2>/dev/null || true
  rm -f /etc/systemd/system/${SERVICE_NAME}.service
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true

  rm -f "$BINARY_PATH" "${INSTALL_DIR}/VERSION" "${INSTALL_DIR}/install.sh"
  if [[ "$del_data" =~ ^[Yy]$ ]]; then
    log "删除数据目录..."
    rm -rf "${INSTALL_DIR}/data"
  else
    warn "数据保留在 ${INSTALL_DIR}/data,下次安装会自动复用"
  fi
  rmdir "$INSTALL_DIR" 2>/dev/null || true
  rm -f /usr/local/bin/mesh

  ok "MeshPanel 已卸载"
}

# ---------------- 改端口 ----------------
do_change_port() {
  [[ -x "$BINARY_PATH" ]] || { warn "未安装,先选[1]安装"; return; }
  local db="${INSTALL_DIR}/data/app.db"
  [[ -f "$db" ]] || die "数据库不存在: $db"
  ensure_sqlite3

  local cur new
  cur=$(panel_port_in_db)
  echo -e "当前面板端口: ${c_bold}${cur}${c_reset}"
  read -p "输入新端口 (1-65535,回车取消): " new
  [[ -z "$new" ]] && { log "取消"; return; }
  [[ "$new" =~ ^[0-9]+$ ]] || die "端口必须是数字"
  (( new >= 1 && new <= 65535 )) || die "端口范围 1-65535"
  [[ "$new" == "$cur" ]] && { warn "端口未变"; return; }

  # 检查端口占用 (排除自己)
  if command -v ss >/dev/null && ss -tnlp 2>/dev/null | awk '{print $4}' | grep -qE ":${new}$"; then
    # 看是不是 meshpanel 自己占的(改端口前它还在跑旧端口,所以新端口被占就是别人占的)
    warn "端口 ${new} 已被其它进程占用:"
    ss -tnlp 2>/dev/null | grep -E ":${new}\b" || true
    read -p "仍要使用? (y/N): " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { log "取消"; return; }
  fi

  log "写入数据库..."
  sqlite3 "$db" "INSERT INTO settings(key,value) VALUES('panel_port','${new}') \
    ON CONFLICT(key) DO UPDATE SET value=excluded.value;" \
    || die "sqlite3 写入失败"

  log "重启服务..."
  systemctl restart ${SERVICE_NAME}.service
  sleep 2
  if curl -fsS "http://127.0.0.1:${new}/api/health" >/dev/null 2>&1; then
    ok "端口已改为 ${new},健康检查通过"
    local pubip; pubip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [[ -n "$pubip" ]] && echo -e "  访问: ${c_cyan}http://${pubip}:${new}${c_reset}"
  else
    err "新端口健康检查失败,看 journalctl -u ${SERVICE_NAME} -n 30 --no-pager"
    warn "如需回滚: mesh port ${cur}"
  fi
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
  local ver st port pubip st_color
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
    printf '
  %b1)%b 安装 MeshPanel
  %b2)%b 更新到最新版
  %b3)%b 卸载 MeshPanel
  ----
  %b4)%b 启动服务
  %b5)%b 停止服务
  %b6)%b 重启服务
  %b7)%b 查看状态
  %b8)%b 查看日志 (实时)
  %b9)%b 修改面板端口
  ----
  %b0)%b 退出

' "$c_bold" "$c_reset" "$c_bold" "$c_reset" "$c_bold" "$c_reset" \
  "$c_bold" "$c_reset" "$c_bold" "$c_reset" "$c_bold" "$c_reset" \
  "$c_bold" "$c_reset" "$c_bold" "$c_reset" "$c_bold" "$c_reset" \
  "$c_bold" "$c_reset"
    read -p "请选择 [0-9]: " choice
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
      9) do_change_port ;;
      0) exit 0 ;;
      *) warn "无效选择" ;;
    esac
    echo
    read -p "回车返回菜单..." _
  done
}

# 非交互直接传端口: mesh port 9000
do_change_port_noninteractive() {
  local new="$1"
  [[ -x "$BINARY_PATH" ]] || die "未安装"
  local db="${INSTALL_DIR}/data/app.db"
  [[ -f "$db" ]] || die "数据库不存在: $db"
  [[ "$new" =~ ^[0-9]+$ ]] || die "端口必须是数字"
  (( new >= 1 && new <= 65535 )) || die "端口范围 1-65535"
  ensure_sqlite3
  local cur; cur=$(panel_port_in_db)
  [[ "$new" == "$cur" ]] && { ok "端口未变 (${cur})"; return; }
  sqlite3 "$db" "INSERT INTO settings(key,value) VALUES('panel_port','${new}') \
    ON CONFLICT(key) DO UPDATE SET value=excluded.value;" || die "sqlite3 写入失败"
  systemctl restart ${SERVICE_NAME}.service
  sleep 2
  if curl -fsS "http://127.0.0.1:${new}/api/health" >/dev/null 2>&1; then
    ok "端口 ${cur} → ${new}"
  else
    err "新端口健康检查失败"
    exit 1
  fi
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
  port)      [[ -n "${2:-}" ]] || die "用法: mesh port <端口号>"; do_change_port_noninteractive "$2" ;;
  ""|menu)   menu ;;
  *) die "未知命令: $1 (支持: install|update|uninstall|start|stop|restart|status|logs|port|menu)" ;;
esac