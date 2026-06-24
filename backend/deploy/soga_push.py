"""把 DB 里的 soga_routes 渲染成 routes.toml,通过 agent RPC 覆盖到入口机。

不保留注释、不备份。写文件后调 `soga restart <folder>` 让 SoGa 重新加载。
所有远程操作走 deploy.remote (agent ws RPC),不再 SSH。

调用约定:
  push_instance_routes(instance, node, landing_nodes_by_id) -> None
    instance: SogaInstance
    node: Node (kind=soga)
    landing_nodes_by_id: {id: Node} — 用于解析 route_out.landing_node_id

raises SogaPushError 时上游捕获返回 detail。
"""
import io
from typing import Dict, List

from deploy import remote


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


def _require_online(node):
    if not remote.is_online(node.id):
        raise SogaPushError("agent 离线,无法推送配置")


def render_routes_toml(
    routes: List[dict],
    landing_nodes_by_id: Dict[int, object],
    enable_system_probe: bool = True,
    system_probe_rules: List[str] = None,
    source_listen: str | None = None,
) -> str:
    """routes 项格式:
      {
        "rules": ["..."],
        "balance": "ip_hash" | None,
        "is_fallback": bool,
        "outs": [
          { "landing_node_id": int, "listen": str }
        ]
      }

    enable_system_probe=True 时,自动在最顶部注入系统探活路由。
    system_probe_rules=None 则用内置 SYSTEM_PROBE_RULES,否则用传入列表。
    """
    probe_rules = system_probe_rules if system_probe_rules else SYSTEM_PROBE_RULES
    source_listen = (source_listen or "").strip()
    buf = io.StringIO()
    buf.write("enable=true\n\n")

    if enable_system_probe and probe_rules:
        buf.write("[[routes]]\n")
        buf.write("rules=[\n")
        for rule in probe_rules:
            buf.write(f"  {_toml_str(rule)},\n")
        buf.write("]\n")
        buf.write("[[routes.Outs]]\n")
        if source_listen:
            buf.write(f"listen={_toml_str(source_listen)}\n")
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
            # SoGa 官方示例里 listen 放在 type 前；优先用手填,为空则回落 soga.conf listen。
            out_listen = (o.get("listen") or source_listen or "").strip()
            if out_listen:
                buf.write(f"listen={_toml_str(out_listen)}\n")
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


def parse_conf_listen(text: str) -> str:
    """从 soga.conf 提取 listen= 的值；没有则返回空字符串。"""
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("listen="):
            val = s.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
            return val
    return ""


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
            # SoGa 要求 username/password 字段必须存在,空也得写
            # 前端字段叫 socks_username/socks_password,兼容老字段 username/password
            "username": cfg.get("socks_username") or cfg.get("username") or "",
            "password": cfg.get("socks_password") or cfg.get("password") or "",
        }
        return out
    # 默认按 shadowsocks (SoGa 用 type="ss" + cipher=)
    return {
        "type": "ss",
        "server": land.host,
        "port": int(port),
        "password": cfg.get("password") or "",
        "cipher": cfg.get("method") or cfg.get("cipher") or "aes-256-gcm",
    }


def read_conf(node, folder_name: str) -> str:
    """读 /etc/soga/<folder>/soga.conf 原文。"""
    _require_online(node)
    path = f"/etc/soga/{folder_name}/soga.conf"
    try:
        data = remote.remote_read(node, path, max_size=512 * 1024)
    except remote.RemoteError as e:
        raise SogaPushError(f"读 {path} 失败: {e}") from e
    return data.decode("utf-8", errors="replace")


def write_conf(node, folder_name: str, text: str) -> None:
    """写 /etc/soga/<folder>/soga.conf — 覆盖。"""
    _require_online(node)
    try:
        remote.remote_write(node, f"/etc/soga/{folder_name}/soga.conf", text, mode=0o644)
    except remote.RemoteError as e:
        raise SogaPushError(f"写 soga.conf 失败: {e}") from e


def _parse_routes_url(text: str) -> str | None:
    """从 conf 文本里提取第一行 routes_url= 的值,没有返回 None。"""
    for ln in text.splitlines():
        s = ln.lstrip()
        if s.lower().startswith("routes_url="):
            return s.split("=", 1)[1].strip()
    return None


def _strip_routes_url(text: str) -> str:
    """从 conf 文本里剥掉所有 routes_url= 行(含前后空行整行)。"""
    lines = text.splitlines()
    keep = [ln for ln in lines if not ln.lstrip().lower().startswith("routes_url=")]
    # 末尾保留单个换行
    return "\n".join(keep).rstrip("\n") + "\n"


def set_routes_url(node, folder_name: str, url: str | None) -> None:
    """切换 soga.conf 的 routes_url 行。

    url=None: 移除该行(切回 file 模式);
    url=str:  移除旧的并追加新行到文件末尾。
    幂等。写完不重启,调用方自己决定重启时机。
    """
    _require_online(node)
    conf = read_conf(node, folder_name)
    stripped = _strip_routes_url(conf)
    if url:
        new_text = stripped + f"routes_url={url}\n"
    else:
        new_text = stripped
    if new_text == conf:
        return
    write_conf(node, folder_name, new_text)


def restart_soga(node, folder_name: str) -> str:
    """跑 `soga restart <folder>`,返回输出。失败抛 SogaPushError。"""
    _require_online(node)
    cmd = f"soga restart {folder_name}"
    try:
        r = remote.remote_exec(node, cmd, timeout=30)
    except remote.RemoteError as e:
        raise SogaPushError(f"执行失败: {e}") from e
    rc = r.get("rc", -1)
    out = r.get("stdout", "")
    err = r.get("stderr", "")
    if rc != 0:
        raise SogaPushError(f"soga restart 失败 (exit {rc}): {err.strip() or out.strip() or '无输出'}")
    return (out + err).strip()
