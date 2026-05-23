"""Agent RPC 通道:后端通过 ws/agents 的长连接给 agent 下发 RPC 请求。

协议(JSON over ws):
  后端 → agent : {"type": "rpc", "id": "<uuid>", "method": "shell.exec", "params": {...}}
  agent → 后端 : {"type": "rpc_resp", "id": "<uuid>", "ok": true,  "result": {...}}
                {"type": "rpc_resp", "id": "<uuid>", "ok": false, "error": "..."}

设计要点:
- 复用 ws/agents.py 里 `_connections[node_id]` 这把全局连接表(不重复维护)。
- 每个 in-flight 请求一个 asyncio.Future,响应到达时 set_result。
- 同步代码(FastAPI 普通路径,paramiko 替代场景全是同步)用 call_sync 桥接:
  通过保存在 ws/agents 里的事件循环把协程丢回去 await。
- 超时:默认 30s,触发时 Future cancel + 清表;agent 后到的响应被忽略。
- agent 离线/掉线时:in-flight 的 Future 全部 set_exception(RemoteOffline)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

log = logging.getLogger(__name__)


class RemoteError(Exception):
    """agent 端报错(method 执行失败)。"""


class RemoteOffline(RemoteError):
    """agent 不在线/掉线。"""


class RemoteTimeout(RemoteError):
    """RPC 超时。"""


# (node_id, request_id) -> Future[dict]
_pending: dict[tuple[int, str], asyncio.Future] = {}

# 主事件循环(FastAPI 启动时由 ws/agents 注册进来),用于跨线程调用
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """ws/agents 启动时调用,把主循环交给 RPC 模块,方便同步代码桥接。"""
    global _main_loop
    _main_loop = loop


def is_online(node_id: int) -> bool:
    """agent 是否在线(查 ws/agents 的连接表)。"""
    from ws.agents import _connections  # 避免循环引用,运行时再 import
    return node_id in _connections


def handle_response(node_id: int, msg: dict) -> None:
    """ws/agents 收到 type=rpc_resp 时调本函数。"""
    rid = msg.get("id")
    if not isinstance(rid, str):
        log.warning("rpc_resp without id from node_id=%s", node_id)
        return
    fut = _pending.pop((node_id, rid), None)
    if fut is None or fut.done():
        log.debug("rpc_resp stale/unknown id=%s node_id=%s", rid, node_id)
        return
    if msg.get("ok") is True:
        fut.set_result(msg.get("result") or {})
    else:
        err = str(msg.get("error") or "agent 未提供错误信息")
        fut.set_exception(RemoteError(err))


def fail_all_for_node(node_id: int, reason: str = "agent 已掉线") -> None:
    """ws/agents 处理掉线时调,把该节点所有 in-flight 失败掉。"""
    keys = [k for k in _pending if k[0] == node_id]
    for k in keys:
        fut = _pending.pop(k, None)
        if fut and not fut.done():
            fut.set_exception(RemoteOffline(reason))


async def call(
    node_id: int,
    method: str,
    params: Optional[dict] = None,
    timeout: float = 30.0,
) -> dict:
    """异步路径调用 RPC。返回 result dict;失败抛 RemoteError 子类。"""
    from ws.agents import _connections, _write_locks

    ws = _connections.get(node_id)
    lock = _write_locks.get(node_id)
    if ws is None or lock is None:
        raise RemoteOffline(f"node_id={node_id} agent 不在线")

    rid = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[(node_id, rid)] = fut

    frame = json.dumps({"type": "rpc", "id": rid, "method": method, "params": params or {}})
    try:
        async with lock:
            await ws.send_text(frame)
    except Exception as e:  # noqa: BLE001
        _pending.pop((node_id, rid), None)
        raise RemoteOffline(f"发送失败: {type(e).__name__}: {e}") from e

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError as e:
        _pending.pop((node_id, rid), None)
        raise RemoteTimeout(f"RPC 超时 ({timeout}s) method={method}") from e


def call_sync(
    node_id: int,
    method: str,
    params: Optional[dict] = None,
    timeout: float = 30.0,
) -> dict:
    """同步代码用的桥接:把协程丢回主事件循环 await。

    用法:任何同步函数(FastAPI def 路由 / paramiko 替代代码)直接调本函数。
    """
    if _main_loop is None:
        raise RemoteError("agent_rpc 未绑定事件循环(服务未就绪)")
    coro = call(node_id, method, params, timeout)
    fut = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    try:
        return fut.result(timeout=timeout + 5)
    except RemoteError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RemoteError(f"{type(e).__name__}: {e}") from e
