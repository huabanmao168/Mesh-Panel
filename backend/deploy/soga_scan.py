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


def parse_soga_version_output(stdout: str) -> str | None:
    """从 soga 版本输出里提取真正的 soga 程序版本。

    soga 管理脚本会输出两行，例如:
      管理脚本: v0.0.6
      soga 程序: v2.16.0
    旧逻辑只取第一行，导致面板显示成管理脚本版本 v0.0.6。
    """
    text = stdout or ""
    # 优先取明确标注的 soga 程序版本，排除管理脚本版本。
    for line in text.splitlines():
        if re.search(r"soga\s*(程序|program|binary|core)", line, re.I):
            m = re.search(r"v?\d+\.\d+\.\d+(?:[-.\w]+)?", line)
            if m:
                return m.group(0)
    # 兼容只有单行真实 binary 版本的安装方式。
    for line in text.splitlines():
        if re.search(r"管理脚本|manager|script", line, re.I):
            continue
        m = re.search(r"v?\d+\.\d+\.\d+(?:[-.\w]+)?", line)
        if m:
            return m.group(0)
    return None


def scan_soga_instances(node) -> Dict[str, Any]:
    """扫描节点上 /etc/soga/*/ 下所有有 soga.conf 的文件夹,返回实例列表。"""
    # 取 soga 程序版本(失败不阻断)
    soga_version = None
    try:
        ver_raw = remote.remote_exec(node, "(soga -v || soga --version) 2>&1", timeout=8)
        stdout = ""
        if isinstance(ver_raw, dict):
            stdout = (ver_raw.get("stdout") or "") + "\n" + (ver_raw.get("stderr") or "")
            if not stdout.strip():
                stdout = ver_raw.get("output") or ""
        elif isinstance(ver_raw, str):
            stdout = ver_raw
        soga_version = parse_soga_version_output(stdout)
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
