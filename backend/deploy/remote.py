"""远程操作统一调度层。

所有"对节点执行命令 / 读写文件"的代码都应该走这里,不要直接用 paramiko。
当前实现:全部走 agent ws RPC,agent 离线直接抛 RemoteOffline。
后端 API 层接到 RemoteOffline 应返回 409 + "agent 离线,无法执行远程操作"。

例外:
- installer.py(首装 agent 阶段,目标机器还没 agent)仍然用 SSH,不经本模块。
- 删 / 卸载节点(disconnect_node)也不经这里,直接走 ws。
"""
from __future__ import annotations

import base64
from typing import Optional

from deploy.agent_rpc import (
    call_sync,
    RemoteError,
    RemoteOffline,
    RemoteTimeout,
    is_online,
)

__all__ = [
    "remote_exec",
    "remote_read",
    "remote_write",
    "remote_list",
    "remote_stat",
    "RemoteError",
    "RemoteOffline",
    "RemoteTimeout",
    "is_online",
]


def _nid(node) -> int:
    """允许传 Node 实例或 int。"""
    return node if isinstance(node, int) else node.id


def remote_exec(node, cmd: str, timeout: float = 30.0) -> dict:
    """执行 shell 命令,返回 {rc:int, stdout:str, stderr:str}。

    超时由 agent 侧也加一层 hard kill(params.timeout),网络层多 5s 容错。
    """
    result = call_sync(
        _nid(node),
        "shell.exec",
        {"cmd": cmd, "timeout": int(timeout)},
        timeout=timeout + 5,
    )
    return {
        "rc": int(result.get("rc", -1)),
        "stdout": str(result.get("stdout", "")),
        "stderr": str(result.get("stderr", "")),
    }


def remote_read(node, path: str, max_size: int = 1024 * 1024) -> bytes:
    """读文件,返回 bytes。超过 max_size(默认 1MB)agent 应报错。"""
    result = call_sync(
        _nid(node),
        "fs.read",
        {"path": path, "max_size": int(max_size)},
        timeout=30,
    )
    content_b64 = result.get("content_b64")
    if content_b64 is None:
        raise RemoteError(f"fs.read 响应缺 content_b64 path={path}")
    try:
        return base64.b64decode(content_b64)
    except Exception as e:  # noqa: BLE001
        raise RemoteError(f"fs.read 解码失败: {e}") from e


def remote_write(node, path: str, content: bytes | str, mode: int = 0o644) -> None:
    """写文件。content 可以是 bytes 或 str(UTF-8 编码)。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    b64 = base64.b64encode(content).decode("ascii")
    call_sync(
        _nid(node),
        "fs.write",
        {"path": path, "content_b64": b64, "mode": mode},
        timeout=30,
    )


def remote_list(node, glob: str) -> list[str]:
    """列出匹配 glob 的路径。如 `/etc/soga/*/`。"""
    result = call_sync(_nid(node), "fs.list", {"glob": glob}, timeout=15)
    items = result.get("items") or []
    return [str(x) for x in items if isinstance(x, str)]


def remote_stat(node, path: str) -> Optional[dict]:
    """文件元信息。不存在返 None,存在返 {size:int, mtime:int, mode:int, is_dir:bool}。"""
    result = call_sync(_nid(node), "fs.stat", {"path": path}, timeout=10)
    if not result.get("exists"):
        return None
    return {
        "size": int(result.get("size", 0)),
        "mtime": int(result.get("mtime", 0)),
        "mode": int(result.get("mode", 0)),
        "is_dir": bool(result.get("is_dir", False)),
    }
