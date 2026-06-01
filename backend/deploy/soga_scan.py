"""通过 agent RPC 进入入口机扫 /etc/soga/*/,发现 soga 实例。

v2.2.8: 完全不再读 routes.toml — routes 数据以 DB 为 source of truth,
扫描只负责发现实例(soga.conf 存在) + 取 soga 版本。

输出格式:
{
  "instances": [{"folder": "ABC-HK"}, ...],
  "soga_version": "v1.2.3" | None
}

所有远程操作通过 deploy.remote 走 agent ws RPC,不再 SSH。
"""
import re
from typing import List, Dict, Any

from deploy import remote

SCAN_TIMEOUT = 20


class ScanError(Exception):
    pass


def scan_soga_instances(node) -> Dict[str, Any]:
    """扫描节点上 /etc/soga/*/ 下所有有 soga.conf 的文件夹,返回实例列表。"""
    # 取 soga 版本(失败不阻断)
    soga_version = None
    try:
        ver_raw = remote.remote_exec(node, "soga --version 2>&1 | head -1", timeout=8)
        stdout = ""
        if isinstance(ver_raw, dict):
            stdout = ver_raw.get("stdout") or ver_raw.get("output") or ""
        elif isinstance(ver_raw, str):
            stdout = ver_raw
        if stdout:
            m = re.search(r"v?\d+\.\d+\.\d+(?:[-.\w]+)?", stdout)
            if m:
                soga_version = m.group(0)
    except remote.RemoteOffline as e:
        raise ScanError(str(e)) from e
    except remote.RemoteError:
        pass

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

    # soga.conf 存在 = 有效实例
    result = []
    for folder in folders:
        conf_path = f"/etc/soga/{folder}/soga.conf"
        try:
            cst = remote.remote_stat(node, conf_path)
        except remote.RemoteError:
            continue
        if cst is None:
            continue
        result.append({"folder": folder})

    return {"instances": result, "soga_version": soga_version}
