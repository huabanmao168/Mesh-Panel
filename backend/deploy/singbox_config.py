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
    # 调试用
    "none",
]

DNS_STRATEGIES = ["ipv4_only", "ipv6_only", "prefer_ipv4", "prefer_ipv6"]

DEFAULT_SS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "listen_addr": "0.0.0.0",
    "listen_port": 8388,
    "password": "",
    "method": "2022-blake3-aes-128-gcm",
    "dns_primary": "https://1.1.1.1/dns-query",
    "dns_backup": "https://8.8.8.8/dns-query",
    "dns_strategy": "ipv4_only",
}


def validate_ss_config(cfg: dict) -> tuple[bool, str]:
    """校验 SS 配置字段。返回 (ok, error_message)。"""
    if not isinstance(cfg.get("listen_port"), int):
        return False, "listen_port 必须是整数"
    if not (1 <= cfg["listen_port"] <= 65535):
        return False, "listen_port 必须在 1-65535"
    # listen_addr 允许空（表示监听全部 v4+v6）；非空时简单格式校验
    listen_addr = cfg.get("listen_addr", "")
    if listen_addr and not isinstance(listen_addr, str):
        return False, "listen_addr 必须是字符串"
    method = cfg.get("method", "")
    if method not in SUPPORTED_METHODS:
        return False, f"不支持的加密方法: {method}"
    if method != "none" and not cfg.get("password"):
        return False, "密码不能为空（除非加密方法是 none）"
    strategy = cfg.get("dns_strategy", "ipv4_only")
    if strategy not in DNS_STRATEGIES:
        return False, f"不支持的 dns_strategy: {strategy}"
    if not cfg.get("dns_primary"):
        return False, "主 DNS 不能为空"
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
        "log": {"level": "info", "timestamp": True},
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

    if ss_cfg.get("enabled"):
        # listen 字段：用户填了用用户的，空就用 "::" 监听全部（v4+v6 dual-stack）
        listen = (ss_cfg.get("listen_addr") or "").strip() or "::"
        config["inbounds"].append({
            "type": "shadowsocks",
            "tag": "ss-in",
            "listen": listen,
            "listen_port": int(ss_cfg["listen_port"]),
            "method": ss_cfg["method"],
            "password": ss_cfg.get("password", ""),
        })

    # 1.12+ 要求 outbound dial 显式指定 domain resolver
    config["route"] = {
        "default_domain_resolver": dns_servers[0]["tag"],
    }

    return config
