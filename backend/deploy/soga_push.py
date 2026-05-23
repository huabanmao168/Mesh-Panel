"""把 DB 里的 soga_routes 渲染成 routes.toml,SSH 覆盖到入口机。

不保留注释、不备份。SoGa 检测到 mtime 变化自动 reload。

调用约定:
  push_instance_routes(instance, node, landing_nodes_by_id) -> None
    instance: SogaInstance
    node: Node (kind=soga)
    landing_nodes_by_id: {id: Node} — 用于解析 route_out.landing_node_id

raises SogaPushError 时上游捕获返回 detail。
"""
import io
import time
import threading
from typing import Dict, List

import paramiko
from ssh.client import _load_pkey
from security.crypto import decrypt


class SogaPushError(Exception):
    pass


# 系统探活规则:captive portal 域名集合,几乎不变,写死在后端
SYSTEM_PROBE_RULES = [
    "domain:cp.cloudflare.com",
    "domain:connectivitycheck.gstatic.com",
    "domain:www.gstatic.com",
    "domain:clients3.google.com",
    "domain:detectportal.firefox.com",
    "domain:captive.apple.com",
    "domain:www.msftconnecttest.com",
]


def _connect(node) -> paramiko.SSHClient:
    last_err = None
    for attempt in range(3):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=node.host,
            port=node.ssh_port,
            username=node.ssh_user,
            timeout=15,
            banner_timeout=30,
            auth_timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )
        if node.auth_type == "password":
            kwargs["password"] = decrypt(node.ssh_password)
        elif node.auth_type == "key":
            kwargs["pkey"] = _load_pkey(decrypt(node.ssh_private_key) or "")
        else:
            raise SogaPushError(f"未知认证方式: {node.auth_type}")
        try:
            client.connect(**kwargs)
            return client
        except paramiko.SSHException as e:
            last_err = e
            try: client.close()
            except Exception: pass
            msg = str(e)
            # banner / 限速类错误才重试
            if "banner" in msg.lower() or "Connection reset" in msg or "EOFError" in msg:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as e:
            try: client.close()
            except Exception: pass
            raise
    # 三次都失败
    raise last_err or SogaPushError("SSH 连接失败: 未知错误")


def render_routes_toml(
    routes: List[dict],
    landing_nodes_by_id: Dict[int, object],
    enable_system_probe: bool = True,
    system_probe_rules: List[str] = None,
) -> str:
    """routes 项格式:
      {
        "rules": ["..."],
        "balance": "ip_hash" | None,
        "is_fallback": bool,
        "outs": [
          { "landing_node_id": int }
        ]
      }

    enable_system_probe=True 时,自动在最顶部注入系统探活路由。
    system_probe_rules=None 则用内置 SYSTEM_PROBE_RULES,否则用传入列表。
    """
    probe_rules = system_probe_rules if system_probe_rules else SYSTEM_PROBE_RULES
    buf = io.StringIO()
    buf.write("enable=true\n\n")

    if enable_system_probe and probe_rules:
        buf.write("[[routes]]\n")
        buf.write("rules=[\n")
        for rule in probe_rules:
            buf.write(f"  {_toml_str(rule)},\n")
        buf.write("]\n")
        buf.write("[[routes.Outs]]\n")
        buf.write('type="direct"\n')
        buf.write("\n")

    # 2) 用户路由 + 兜底
    for r in routes:
        if r.get("is_system"):
            # 老数据残留的系统路由跳过,新逻辑统一靠注入
            continue
        buf.write("[[routes]]\n")
        rules = r.get("rules") or []
        buf.write("rules=[\n")
        for rule in rules:
            buf.write(f"  {_toml_str(rule)},\n")
        buf.write("]\n")
        bal = r.get("balance")
        outs = r.get("outs") or []
        # 只有 >1 个落地池时才写 balance(单出口无意义)
        if bal and len(outs) > 1:
            buf.write(f"balance={_toml_str(bal)}\n")
        for o in outs:
            buf.write("[[routes.Outs]]\n")
            land_id = o.get("landing_node_id")
            land = landing_nodes_by_id.get(land_id) if land_id else None
            if not land:
                raise SogaPushError(f"路由出站缺落地节点(landing_node_id={land_id})")
            cfg = _parse_landing_for_out(land)
            for k, v in cfg.items():
                if isinstance(v, str):
                    buf.write(f"{k}={_toml_str(v)}\n")
                else:
                    buf.write(f"{k}={v}\n")
        buf.write("\n")
    return buf.getvalue()


def _toml_str(s: str) -> str:
    """安全的 TOML 双引号字符串(简化版,转义反斜杠和引号)。"""
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _parse_landing_for_out(land) -> dict:
    """根据落地节点的 ss_config 生成 out 块字段。

    ss_config 是 JSON 字符串(同 SSConfigDrawer 写入的结构),典型:
      {
        "protocol": "shadowsocks" | "socks",
        "listen_port": 8388,
        "method": "aes-256-gcm",
        "password": "...",
        "username": "...",   # socks 才有
      }
    """
    import json
    raw = land.ss_config or "{}"
    try:
        cfg = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        cfg = {}
    proto = (cfg.get("protocol") or "").lower()
    port = cfg.get("listen_port") or cfg.get("port") or 0
    if proto in ("socks", "socks5"):
        out = {
            "type": "socks",
            "server": land.host,
            "port": int(port),
        }
        if cfg.get("username"):
            out["username"] = cfg["username"]
        if cfg.get("password"):
            out["password"] = cfg["password"]
        return out
    # 默认按 shadowsocks (SoGa 用 type="ss" + cipher=)
    return {
        "type": "ss",
        "server": land.host,
        "port": int(port),
        "password": cfg.get("password") or "",
        "cipher": cfg.get("method") or cfg.get("cipher") or "aes-256-gcm",
    }


def push_routes(node, folder_name: str, toml_text: str) -> None:
    """SSH 写 /etc/soga/<folder>/routes.toml — 覆盖,不备份。"""
    _sftp_write(node, f"/etc/soga/{folder_name}/routes.toml", toml_text)


def read_conf(node, folder_name: str) -> str:
    """SSH 读 /etc/soga/<folder>/soga.conf 原文。"""
    try:
        client = _connect(node)
    except Exception as e:
        raise SogaPushError(f"SSH 连接失败: {type(e).__name__}: {e}") from e
    try:
        sftp = client.open_sftp()
        try:
            path = f"/etc/soga/{folder_name}/soga.conf"
            with sftp.file(path, "r") as f:
                data = f.read()
            return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        except FileNotFoundError as e:
            raise SogaPushError(f"配置不存在: {path}") from e
        finally:
            sftp.close()
    finally:
        try: client.close()
        except Exception: pass


def write_conf(node, folder_name: str, text: str) -> None:
    """SSH 写 /etc/soga/<folder>/soga.conf — 覆盖。"""
    _sftp_write(node, f"/etc/soga/{folder_name}/soga.conf", text)


def restart_soga(node, folder_name: str) -> str:
    """SSH 跑 `soga restart <folder>`,返回输出。失败抛 SogaPushError。"""
    try:
        client = _connect(node)
    except Exception as e:
        raise SogaPushError(f"SSH 连接失败: {type(e).__name__}: {e}") from e
    try:
        cmd = f"soga restart {folder_name}"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
        rc = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if rc != 0:
            raise SogaPushError(f"soga restart 失败 (exit {rc}): {err.strip() or out.strip() or '无输出'}")
        return (out + err).strip()
    finally:
        try: client.close()
        except Exception: pass


def _sftp_write(node, path: str, text: str) -> None:
    try:
        client = _connect(node)
    except Exception as e:
        raise SogaPushError(f"SSH 连接失败: {type(e).__name__}: {e}") from e
    try:
        sftp = client.open_sftp()
        try:
            tmp_path = path + ".tmp"
            with sftp.file(tmp_path, "w") as f:
                f.write(text)
            sftp.posix_rename(tmp_path, path)
        finally:
            sftp.close()
    finally:
        try: client.close()
        except Exception: pass
