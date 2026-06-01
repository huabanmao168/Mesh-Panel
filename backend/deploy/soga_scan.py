"""通过 agent RPC 进入入口机扫 /etc/soga/*/routes.toml,解析每个文件返回路由结构。

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
所有远程操作通过 deploy.remote 走 agent ws RPC,不再 SSH。
"""
import re
import tomllib
from typing import List, Dict, Any

from deploy import remote

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


def _parse_soga_version(text: str) -> str | None:
    m = re.search(r"soga\s*程序[:：]\s*v?(\d+\.\d+\.\d+)", text or "")
    if m:
        return m.group(1)
    m = re.search(r"soga[\s:：]+v?(\d+\.\d+\.\d+)", text or "", re.IGNORECASE)
    return m.group(1) if m else None


def get_soga_version(node) -> str | None:
    """跑 `soga version` 解析版本号,失败返 None。"""
    try:
        r = remote.remote_exec(node, "soga version 2>&1", timeout=SCAN_TIMEOUT)
    except remote.RemoteError:
        return None
    return _parse_soga_version((r.get("stdout") or "") + (r.get("stderr") or ""))


def scan_soga_instances(node) -> Dict[str, Any]:
    """主入口:返回 {instances: [...], soga_version: "2.14.0"|None}。

    通过 agent RPC 扫 folder + 读 routes.toml + 取 soga 版本。
    """
    if not remote.is_online(node.id):
        raise ScanError("agent 离线,无法扫描入口机配置")

    # 取 soga 版本
    soga_version = None
    try:
        r = remote.remote_exec(node, "soga version 2>&1", timeout=SCAN_TIMEOUT)
        soga_version = _parse_soga_version((r.get("stdout") or "") + (r.get("stderr") or ""))
    except remote.RemoteError:
        pass  # 取版本失败不阻断扫描

    # 扫文件夹
    try:
        folders_raw = remote.remote_list(node, "/etc/soga/*/")
    except remote.RemoteOffline as e:
        raise ScanError(str(e)) from e
    except remote.RemoteError as e:
        raise ScanError(f"列目录失败: {e}") from e

    folders = []
    for p in folders_raw:
        p = p.rstrip("/")
        folder = p.rsplit("/", 1)[-1]
        if folder:
            folders.append(folder)

    # 判据放宽: soga.conf 存在 = 是一个有效实例 (不再依赖 routes.toml)
    # routes.toml 读不到/不存在 → routes=[], 后续 push 接口会自动重建
    result = []
    for folder in folders:
        conf_path = f"/etc/soga/{folder}/soga.conf"
        try:
            cst = remote.remote_stat(node, conf_path)
        except remote.RemoteError:
            continue
        if cst is None:
            continue  # soga.conf 不存在 → 不是一个实例,跳过

        # 尝试读 routes.toml。
        # 关键: routes=None 表示"无法判定"(文件不存在/读失败/解析失败/超大),
        # 入库时会跳过路由重建,只更新 instance 元数据,避免清空 DB 已有路由。
        # 只有真的读到内容并解析成功(可能是空 list)才填实际值。
        routes = None
        routes_path = f"/etc/soga/{folder}/routes.toml"
        try:
            st = remote.remote_stat(node, routes_path)
        except remote.RemoteError:
            st = None
        if st is not None and st.get("size", 0) <= MAX_ROUTES_FILE_BYTES:
            try:
                raw = remote.remote_read(node, routes_path, max_size=MAX_ROUTES_FILE_BYTES)
                routes = _parse_routes_toml(raw)
            except remote.RemoteError:
                routes = None
            except Exception:
                routes = None
        result.append({"folder": folder, "routes": routes})

    return {"instances": result, "soga_version": soga_version}


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
