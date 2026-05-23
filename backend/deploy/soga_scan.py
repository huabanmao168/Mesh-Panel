"""SSH 进入入口机扫 /etc/soga/*/routes.toml,解析每个文件返回路由结构。

输出格式(给 api/soga.py 入库用):
[
  {
    "folder": "ABC-HK",
    "routes": [
      {
        "rules": ["domain:..."],
        "balance": "ip_hash" | None,
        "is_system": True,         # 仅 outs=[{type:direct}] 且 rules 匹配系统探活域名时
        "is_fallback": False,      # rules == ["*"]
        "outs": [{"type":"socks","server":"1.2.3.4","port":1111,...}]
      },
      ...
    ]
  },
  ...
]

TOML 解析用 Python 3.11+ 内置 tomllib。
"""
import tomllib
import io
from typing import List, Dict, Any

import paramiko
from ssh.client import _load_pkey
from security.crypto import decrypt


SCAN_TIMEOUT = 20
MAX_ROUTES_FILE_BYTES = 100 * 1024   # 单个 routes.toml 不超 100KB

# 系统探活域名集 — 命中任意 4 个以上视作系统路由
SYS_PROBE_DOMAINS = {
    "cp.cloudflare.com",
    "connectivitycheck.gstatic.com",
    "www.gstatic.com",
    "clients3.google.com",
    "detectportal.firefox.com",
    "captive.apple.com",
    "www.msftconnecttest.com",
}


class ScanError(Exception):
    pass


def _connect(node) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=node.host,
        port=node.ssh_port,
        username=node.ssh_user,
        timeout=10,
        allow_agent=False,
        look_for_keys=False,
    )
    if node.auth_type == "password":
        kwargs["password"] = decrypt(node.ssh_password)
    elif node.auth_type == "key":
        kwargs["pkey"] = _load_pkey(decrypt(node.ssh_private_key) or "")
    else:
        raise ScanError(f"未知认证方式: {node.auth_type}")
    client.connect(**kwargs)
    return client


def _exec(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=SCAN_TIMEOUT)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def get_soga_version(node) -> str | None:
    """跑 `soga version` 解析版本号,失败返 None。

    输出示例:
        管理工具: v0.0.3
        soga 程序: v2.14.0
    只取 "soga 程序" 那行的版本。
    """
    import re
    try:
        client = _connect(node)
    except Exception:
        return None
    try:
        rc, out, err = _exec(client, "soga version 2>&1")
        text = (out + err) if (out or err) else ""
        # 优先匹配 "soga 程序: vX.Y.Z"
        m = re.search(r"soga\s*程序[:：]\s*v?(\d+\.\d+\.\d+)", text)
        if m:
            return m.group(1)
        # 兜底:英文 "soga: vX.Y.Z" / "soga vX.Y.Z"
        m = re.search(r"soga[\s:：]+v?(\d+\.\d+\.\d+)", text, re.IGNORECASE)
        return m.group(1) if m else None
    except Exception:
        return None
    finally:
        try: client.close()
        except Exception: pass


def scan_soga_instances(node) -> Dict[str, Any]:
    """主入口:返回 {instances: [...], soga_version: "2.14.0"|None}。

    一条 SSH 连接里跑完所有事:扫 folder + 读 routes.toml + 取 soga 版本。
    """
    try:
        client = _connect(node)
    except Exception as e:
        raise ScanError(f"SSH 连接失败: {type(e).__name__}: {e}") from e

    try:
        # 顺手取一次 soga 版本(同一条连接,不额外开 SSH)
        soga_version = None
        try:
            import re
            _, vout, verr = _exec(client, "soga version 2>&1")
            vtext = (vout or "") + (verr or "")
            m = re.search(r"soga\s*程序[:：]\s*v?(\d+\.\d+\.\d+)", vtext)
            if not m:
                m = re.search(r"soga[\s:：]+v?(\d+\.\d+\.\d+)", vtext, re.IGNORECASE)
            if m:
                soga_version = m.group(1)
        except Exception:
            pass

        rc, out, err = _exec(client, "ls -d /etc/soga/*/ 2>/dev/null")
        if rc != 0 and not out.strip():
            return {"instances": [], "soga_version": soga_version}
        folders = []
        for line in out.strip().split("\n"):
            line = line.strip().rstrip("/")
            if not line:
                continue
            folder = line.rsplit("/", 1)[-1]
            if folder:
                folders.append(folder)

        result = []
        sftp = client.open_sftp()
        try:
            for folder in folders:
                routes_path = f"/etc/soga/{folder}/routes.toml"
                try:
                    st = sftp.stat(routes_path)
                except IOError:
                    continue
                if st.st_size > MAX_ROUTES_FILE_BYTES:
                    continue
                buf = io.BytesIO()
                sftp.getfo(routes_path, buf)
                buf.seek(0)
                try:
                    routes = _parse_routes_toml(buf.read())
                except Exception as e:
                    routes = [{"error": f"parse failed: {e}"}]
                result.append({"folder": folder, "routes": routes})
        finally:
            sftp.close()
        return {"instances": result, "soga_version": soga_version}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _parse_routes_toml(raw: bytes) -> List[Dict[str, Any]]:
    """解析 routes.toml 内容,返回标准化 route 列表。"""
    data = tomllib.loads(raw.decode("utf-8", errors="replace"))
    routes = data.get("routes", [])
    if not isinstance(routes, list):
        return []
    out_list = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        rules = r.get("rules") or []
        if not isinstance(rules, list):
            rules = []
        balance = r.get("balance")
        outs_raw = r.get("Outs") or r.get("outs") or []
        outs = []
        for o in outs_raw:
            if not isinstance(o, dict):
                continue
            outs.append({
                "type": o.get("type"),
                "server": o.get("server"),
                "port": o.get("port"),
                "username": o.get("username"),
                "password": o.get("password"),
                "method": o.get("method"),
            })
        is_fallback = rules == ["*"]
        is_system = _looks_like_system_probe(rules, outs)
        out_list.append({
            "rules": rules,
            "balance": balance,
            "is_system": is_system,
            "is_fallback": is_fallback,
            "outs": outs,
        })
    return out_list


def _looks_like_system_probe(rules, outs) -> bool:
    """规则里命中系统探活域名 4 个以上 + 全 direct 出站 → 视作系统路由。"""
    if not outs or any((o.get("type") or "").lower() != "direct" for o in outs):
        return False
    cnt = 0
    for rule in rules:
        if not isinstance(rule, str):
            continue
        if rule.startswith("domain:"):
            domain = rule.split(":", 1)[1]
            if domain in SYS_PROBE_DOMAINS:
                cnt += 1
    return cnt >= 4
