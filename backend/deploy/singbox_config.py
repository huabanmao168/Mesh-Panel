"""SS 配置 → sing-box JSON 生成器。

按 config_schema 分支，首版只实现 singbox-1.13（最新）。
其他 schema 走 fallback：用 1.13 模板试一下，节点上 sing-box check 拦得住。
"""
import re
from typing import Any
from urllib.parse import urlparse

# sing-box 支持的 SS 方法（去掉了流密码和不安全的旧算法）
SUPPORTED_METHODS = [
    # 2022 协议族（推荐）
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    # 老 AEAD（兼容性好）
    "aes-128-gcm",
    "aes-256-gcm",
    "chacha20-ietf-poly1305",
    "xchacha20-ietf-poly1305",
]

DNS_STRATEGIES = ["ipv4_only", "ipv6_only", "prefer_ipv4", "prefer_ipv6"]

SUPPORTED_PROTOCOLS = ["shadowsocks", "socks"]

SUPPORTED_SNIFFERS = ["http", "tls", "quic", "stun", "dns", "bittorrent"]

LOG_LEVELS = ["trace", "debug", "info", "warn", "error", "fatal"]

DEFAULT_SS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "protocol": "shadowsocks",     # shadowsocks | socks
    "listen_addr": "0.0.0.0",
    "listen_port": 8388,
    # --- shadowsocks 专属 ---
    "password": "",
    "method": "2022-blake3-aes-128-gcm",
    "udp_enabled": True,           # SS 是否启用 UDP(关掉则 network=tcp)
    # --- socks 专属 ---
    "socks_auth_enabled": True,
    "socks_username": "",
    "socks_password": "",
    # --- 访问控制(协议无关) ---
    "ip_allowlist": "",            # 多行 CIDR,留空 = 不限
    # --- 流量嗅探(可选,只记日志不替换目标) ---
    "sniff_enabled": False,
    "sniff_protocols": ["tls", "http"],
    # --- NTP(SS2022 需要时间同步,误差超 30s 会被服务端拒绝) ---
    "ntp_enabled": False,
    "ntp_server": "time.apple.com",
    # --- 日志: 默认关闭, 防止节点磁盘被写满 ---
    "log_enabled": False,
    "log_level": "warn",
    # --- DNS 共用 ---
    "dns_primary": "https://1.1.1.1/dns-query",
    "dns_backup": "https://8.8.8.8/dns-query",
    "dns_strategy": "ipv4_only",
}


def _parse_ip_allowlist(raw: str) -> list[str]:
    """逐行解析 CIDR 白名单,返回标准化后的列表。空行/注释行跳过。"""
    import ipaddress
    out: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 没带掩码就当 /32 或 /128
        if "/" not in line:
            try:
                ipaddress.ip_address(line)
                line = f"{line}/32" if ":" not in line else f"{line}/128"
            except ValueError:
                raise ValueError(f"无效 IP: {line}")
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError as e:
            raise ValueError(f"无效 CIDR: {line} ({e})")
        out.append(str(net))
    return out


def validate_ss_config(cfg: dict) -> tuple[bool, str]:
    """校验入站配置字段。返回 (ok, error_message)。"""
    protocol = cfg.get("protocol", "shadowsocks")
    if protocol not in SUPPORTED_PROTOCOLS:
        return False, f"不支持的入站协议: {protocol}"

    if not isinstance(cfg.get("listen_port"), int):
        return False, "listen_port 必须是整数"
    if not (1 <= cfg["listen_port"] <= 65535):
        return False, "listen_port 必须在 1-65535"
    listen_addr = cfg.get("listen_addr", "")
    if listen_addr and not isinstance(listen_addr, str):
        return False, "listen_addr 必须是字符串"

    if protocol == "shadowsocks":
        method = cfg.get("method", "")
        if method not in SUPPORTED_METHODS:
            return False, f"不支持的加密方法: {method}"
        if method != "none" and not cfg.get("password"):
            return False, "密码不能为空(除非加密方法是 none)"
    elif protocol == "socks":
        if cfg.get("socks_auth_enabled", True):
            if not cfg.get("socks_username"):
                return False, "SOCKS5 用户名不能为空(启用认证时)"
            if not cfg.get("socks_password"):
                return False, "SOCKS5 密码不能为空(启用认证时)"

    strategy = cfg.get("dns_strategy", "ipv4_only")
    if strategy not in DNS_STRATEGIES:
        return False, f"不支持的 dns_strategy: {strategy}"
    if not cfg.get("dns_primary"):
        return False, "主 DNS 不能为空"

    # IP 白名单校验
    raw = cfg.get("ip_allowlist", "")
    if raw:
        try:
            _parse_ip_allowlist(raw)
        except ValueError as e:
            return False, str(e)

    # sniff 校验
    if cfg.get("sniff_enabled"):
        snf = cfg.get("sniff_protocols") or []
        if not isinstance(snf, list):
            return False, "sniff_protocols 必须是列表"
        for p in snf:
            if p not in SUPPORTED_SNIFFERS:
                return False, f"不支持的嗅探协议: {p}"
        if not snf:
            return False, "启用嗅探时至少要选一个协议"

    # 日志校验
    if cfg.get("log_enabled", True):
        if cfg.get("log_level", "warn") not in LOG_LEVELS:
            return False, f"不支持的日志级别: {cfg.get('log_level')}"

    return True, ""


def _parse_dns_server(tag: str, addr: str) -> dict:
    """把用户输入的 DNS 地址转成 sing-box 1.12+ 新格式。

    支持：
      https://...       → type=https
      tls://host[:port] → type=tls
      quic://host[:port]→ type=quic
      tcp://host[:port] → type=tcp
      udp://host[:port] → type=udp
      纯 IP / host      → type=udp
    """
    addr = addr.strip()
    if not addr:
        raise ValueError("DNS 地址为空")

    server: dict[str, Any] = {"tag": tag}

    if addr.startswith("https://"):
        u = urlparse(addr)
        server["type"] = "https"
        server["server"] = u.hostname or ""
        if u.port:
            server["server_port"] = u.port
        if u.path and u.path != "/":
            server["path"] = u.path
    elif addr.startswith("tls://"):
        u = urlparse(addr)
        server["type"] = "tls"
        server["server"] = u.hostname or ""
        if u.port:
            server["server_port"] = u.port
    elif addr.startswith("quic://"):
        u = urlparse(addr)
        server["type"] = "quic"
        server["server"] = u.hostname or ""
        if u.port:
            server["server_port"] = u.port
    elif addr.startswith("tcp://"):
        u = urlparse(addr)
        server["type"] = "tcp"
        server["server"] = u.hostname or ""
        if u.port:
            server["server_port"] = u.port
    elif addr.startswith("udp://"):
        u = urlparse(addr)
        server["type"] = "udp"
        server["server"] = u.hostname or ""
        if u.port:
            server["server_port"] = u.port
    else:
        # 纯 IP 或 host[:port]
        m = re.match(r"^([^:\s]+)(?::(\d+))?$", addr)
        if not m:
            raise ValueError(f"无法解析 DNS 地址: {addr}")
        server["type"] = "udp"
        server["server"] = m.group(1)
        if m.group(2):
            server["server_port"] = int(m.group(2))

    if not server.get("server"):
        raise ValueError(f"DNS 地址缺少主机名: {addr}")
    return server


def render_singbox_config(ss_cfg: dict, schema: str = "singbox-1.13") -> dict:
    """根据 SS 配置生成 sing-box JSON。

    schema 暂时只影响内部分支，首版统一走 1.13 模板（新 DNS 格式）。
    """
    dns_servers: list[dict] = []
    primary = (ss_cfg.get("dns_primary") or "").strip()
    backup = (ss_cfg.get("dns_backup") or "").strip()
    if primary:
        dns_servers.append(_parse_dns_server("doh-primary", primary))
    if backup:
        dns_servers.append(_parse_dns_server("doh-backup", backup))
    if not dns_servers:
        # 兜底
        dns_servers.append(_parse_dns_server("doh-primary", "https://1.1.1.1/dns-query"))

    config = {
        "log": (
            {"disabled": True}
            if not ss_cfg.get("log_enabled", True)
            else {"level": ss_cfg.get("log_level", "warn"), "timestamp": True}
        ),
        "dns": {
            "servers": dns_servers,
            "strategy": ss_cfg.get("dns_strategy") or "ipv4_only",
        },
        "inbounds": [],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
    }

    # NTP: SS2022 协议需要客户端/服务端时间误差 < 30s, 启用 NTP 让 sing-box 自己校时
    if ss_cfg.get("ntp_enabled"):
        ntp_srv = (ss_cfg.get("ntp_server") or "time.apple.com").strip()
        config["ntp"] = {
            "enabled": True,
            "server": ntp_srv,
            "server_port": 123,
            "interval": "30m",
            "detour": "direct",
        }

    if ss_cfg.get("enabled"):
        # listen 字段:用户填了用用户的,空就用 "::" 监听全部(v4+v6 dual-stack)
        listen = (ss_cfg.get("listen_addr") or "").strip() or "::"
        listen_port = int(ss_cfg["listen_port"])
        protocol = ss_cfg.get("protocol", "shadowsocks")

        if protocol == "shadowsocks":
            inbound = {
                "type": "shadowsocks",
                "tag": "ss-in",
                "listen": listen,
                "listen_port": listen_port,
                "method": ss_cfg["method"],
                "password": ss_cfg.get("password", ""),
            }
            # 关闭 UDP 时显式 network=tcp;开启或留空都让 sing-box 默认双栈
            if not ss_cfg.get("udp_enabled", True):
                inbound["network"] = "tcp"
            config["inbounds"].append(inbound)
        elif protocol == "socks":
            inbound = {
                "type": "socks",
                "tag": "socks-in",
                "listen": listen,
                "listen_port": listen_port,
            }
            if ss_cfg.get("socks_auth_enabled", True):
                inbound["users"] = [{
                    "username": ss_cfg.get("socks_username", ""),
                    "password": ss_cfg.get("socks_password", ""),
                }]
            config["inbounds"].append(inbound)

    # 1.12+ 要求 outbound dial 显式指定 domain resolver
    config["route"] = {
        "default_domain_resolver": dns_servers[0]["tag"],
    }

    if ss_cfg.get("enabled"):
        protocol = ss_cfg.get("protocol", "shadowsocks")
        inbound_tag = "ss-in" if protocol == "shadowsocks" else "socks-in"
        rules: list[dict] = []

        # sniff:1.12+ 推荐用 route action,放最前面让流量先嗅探再继续匹配
        if ss_cfg.get("sniff_enabled"):
            sniffers = ss_cfg.get("sniff_protocols") or ["tls", "http"]
            rules.append({
                "inbound": [inbound_tag],
                "action": "sniff",
                "sniffer": sniffers,
                "timeout": "300ms",
            })

        # IP 白名单
        allowlist = _parse_ip_allowlist(ss_cfg.get("ip_allowlist", ""))
        if allowlist:
            rules.append({
                "inbound": [inbound_tag],
                "source_ip_cidr": allowlist,
                "action": "route",
                "outbound": "direct",
            })
            rules.append({
                "inbound": [inbound_tag],
                "action": "reject",
            })

        if rules:
            config["route"]["rules"] = rules

    return config
