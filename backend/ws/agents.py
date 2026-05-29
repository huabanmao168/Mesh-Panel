"""WebSocket: 节点 agent 长连主控。

约束（决策已敲定）：
- 裸 ws://，不过敏感数据（配置走 SSH）
- query 带 token + node_id，主控查数据库鉴权
- 每 10s 心跳；后台扫描任务把超时连接标 offline
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlmodel import Session, select

from database import engine
from models.node import Node
from deploy import agent_rpc

router = APIRouter()
log = logging.getLogger(__name__)

# node_id -> WebSocket，用于主控主动下发指令
_connections: dict[int, WebSocket] = {}
# node_id -> asyncio.Lock，写入串行化
_write_locks: dict[int, asyncio.Lock] = {}
# node_id -> 最近一次 metrics（内存中，不落库；探针式）
_metrics: dict[int, dict] = {}

HEARTBEAT_TIMEOUT = timedelta(seconds=60)
METRICS_FRESH = timedelta(seconds=15)  # 超过这个就视为过期
SWEEP_INTERVAL = 30  # 秒


def _persist_os_pretty(node_id: int, os_pretty: str) -> None:
    """把 agent 上报的 os_pretty 写入 nodes 表(只有变化时才写,避免每次心跳都 UPDATE)。"""
    try:
        with Session(engine) as s:
            n = s.get(Node, node_id)
            if n is not None and n.os_pretty != os_pretty:
                n.os_pretty = os_pretty
                n.updated_at = datetime.now(timezone.utc)
                s.add(n)
                s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("persist os_pretty failed node_id=%s: %s", node_id, e)


def get_metrics(node_id: int) -> Optional[dict]:
    """返回该节点最近一次 metrics（若新鲜），否则 None。"""
    m = _metrics.get(node_id)
    if not m:
        return None
    age = (datetime.now(timezone.utc) - m["_recv_at"]).total_seconds()
    if age > METRICS_FRESH.total_seconds():
        return None
    return {k: v for k, v in m.items() if not k.startswith("_")}


def get_all_metrics() -> dict[int, dict]:
    out = {}
    for nid in list(_metrics.keys()):
        m = get_metrics(nid)
        if m is not None:
            out[nid] = m
    return out


def _mark_status(node_id: int, online: bool, version: Optional[str] = None) -> None:
    """同步函数，开短事务更新节点状态。"""
    with Session(engine) as s:
        node = s.get(Node, node_id)
        if not node:
            return
        node.agent_status = "online" if online else "offline"
        if online:
            node.agent_last_seen = datetime.now(timezone.utc)
            if version:
                node.agent_version = version
        node.updated_at = datetime.now(timezone.utc)
        s.add(node)
        s.commit()


def _touch_last_seen(node_id: int) -> None:
    with Session(engine) as s:
        node = s.get(Node, node_id)
        if not node:
            return
        node.agent_last_seen = datetime.now(timezone.utc)
        s.add(node)
        s.commit()


def _authenticate(node_id: int, token: str) -> bool:
    with Session(engine) as s:
        node = s.get(Node, node_id)
        if not node or not node.agent_token:
            return False
        return node.agent_token == token


def _get_node_iface(node_id: int) -> Optional[str]:
    with Session(engine) as s:
        n = s.get(Node, node_id)
        return n.agent_iface if n else None


@router.websocket("/ws/node")
async def node_ws(
    ws: WebSocket,
    token: str = Query(...),
    node_id: int = Query(...),
    version: str = Query("dev"),
):
    if not _authenticate(node_id, token):
        await ws.close(code=4401)
        log.warning("ws auth failed node_id=%s", node_id)
        return

    # 把主事件循环交给 RPC 模块,只需绑一次
    agent_rpc.bind_loop(asyncio.get_running_loop())

    await ws.accept()
    log.info("ws connected node_id=%s version=%s", node_id, version)

    # 同一节点重连，先关旧连接 + 清 in-flight RPC
    old = _connections.pop(node_id, None)
    if old is not None:
        agent_rpc.fail_all_for_node(node_id, reason="agent 重连")
        try:
            await old.close()
        except Exception:
            pass

    _connections[node_id] = ws
    _write_locks[node_id] = asyncio.Lock()
    _mark_status(node_id, online=True, version=version)

    # 下发当前生效的 iface 配置（若 DB 有指定）
    iface = _get_node_iface(node_id)
    if iface:
        try:
            async with _write_locks[node_id]:
                await ws.send_text(json.dumps({"type": "set_iface", "iface": iface}))
        except Exception as e:  # noqa: BLE001
            log.warning("send set_iface failed node_id=%s: %s", node_id, e)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("bad json from node_id=%s: %s", node_id, raw[:200])
                continue

            mtype = msg.get("type")
            if mtype == "ping":
                _touch_last_seen(node_id)
                async with _write_locks[node_id]:
                    await ws.send_text(json.dumps({"type": "pong"}))
            elif mtype == "metrics":
                _metrics[node_id] = {
                    "iface": msg.get("iface", ""),
                    "rx_bps": int(msg.get("rx_bps", 0)),
                    "tx_bps": int(msg.get("tx_bps", 0)),
                    "rx_total": int(msg.get("rx_total", 0)),
                    "tx_total": int(msg.get("tx_total", 0)),
                    "cpu_pct": float(msg.get("cpu_pct", 0)),
                    "cpu_model": msg.get("cpu_model", ""),
                    "cpu_cores": int(msg.get("cpu_cores", 0)),
                    "mem_used": int(msg.get("mem_used", 0)),
                    "mem_total": int(msg.get("mem_total", 0)),
                    "swap_used": int(msg.get("swap_used", 0)),
                    "swap_total": int(msg.get("swap_total", 0)),
                    "disk_used": int(msg.get("disk_used", 0)),
                    "disk_total": int(msg.get("disk_total", 0)),
                    "tcp_conn": int(msg.get("tcp_conn", 0)),
                    "udp_conn": int(msg.get("udp_conn", 0)),
                    "uptime_sec": int(msg.get("uptime_sec", 0)),
                    "_recv_at": datetime.now(timezone.utc),
                }
                # os_pretty 老 agent 不上报,值为空时不动 DB;新 agent 上报则持久化
                os_pretty = msg.get("os_pretty")
                if os_pretty:
                    _persist_os_pretty(node_id, os_pretty)
            elif mtype == "ack":
                log.info("ack node_id=%s action=%s ok=%s msg=%s",
                         node_id, msg.get("action"), msg.get("ok"), msg.get("message"))
            elif mtype == "rpc_resp":
                agent_rpc.handle_response(node_id, msg)
            else:
                log.info("recv from node_id=%s: %s", node_id, msg)
    except WebSocketDisconnect:
        log.info("ws disconnect node_id=%s", node_id)
    except Exception as e:  # noqa: BLE001
        log.warning("ws error node_id=%s: %s", node_id, e)
    finally:
        is_current = _connections.get(node_id) is ws
        if is_current:
            _connections.pop(node_id, None)
            _write_locks.pop(node_id, None)
        _metrics.pop(node_id, None)
        agent_rpc.fail_all_for_node(node_id, reason="agent ws 断开")
        if is_current:
            _mark_status(node_id, online=False)


async def send_cmd(node_id: int, action: str) -> tuple[bool, str]:
    """主控主动下发指令。返回 (ok, message)。"""
    ws = _connections.get(node_id)
    lock = _write_locks.get(node_id)
    if ws is None or lock is None:
        return False, "agent 不在线"
    try:
        async with lock:
            await ws.send_text(json.dumps({"type": "cmd", "action": action}))
        return True, "已发送"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def disconnect_node(node_id: int) -> bool:
    """主动断开节点 WS（卸载或删除节点时调用）。"""
    ws = _connections.pop(node_id, None)
    _write_locks.pop(node_id, None)
    agent_rpc.fail_all_for_node(node_id, reason="节点被主动断开")
    if ws is None:
        return False
    try:
        await ws.close()
    except Exception:
        pass
    return True


async def sweep_offline_loop():
    """每 30s 扫一次：心跳超 60s 没到的强标 offline（兜底）。"""
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - HEARTBEAT_TIMEOUT
            with Session(engine) as s:
                stale = s.exec(
                    select(Node).where(
                        Node.agent_status == "online",
                        (Node.agent_last_seen == None) | (Node.agent_last_seen < cutoff),  # noqa: E711
                    )
                ).all()
                for n in stale:
                    # 同时把 _connections 里的连接也清掉
                    if n.id in _connections:
                        try:
                            await _connections[n.id].close()
                        except Exception:
                            pass
                        _connections.pop(n.id, None)
                        _write_locks.pop(n.id, None)
                    agent_rpc.fail_all_for_node(n.id, reason="心跳超时")
                    n.agent_status = "offline"
                    n.updated_at = datetime.now(timezone.utc)
                    s.add(n)
                if stale:
                    s.commit()
                    log.info("swept %d offline node(s)", len(stale))
        except Exception as e:  # noqa: BLE001
            log.warning("sweep error: %s", e)
        await asyncio.sleep(SWEEP_INTERVAL)
