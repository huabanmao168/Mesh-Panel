"""/api/nodes —— 节点 CRUD + SSH 测试 + 部署 + agent。"""
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from models.node import Node, NodeCreate, NodeUpdate, NodeRead
from models.soga import SogaInstance, SogaRoute, SogaRouteOut
from ssh.client import test_connection
from deploy.installer import deploy_node, derive_schema, push_agent_env
from deploy.uninstaller import uninstall_node
from utils.geoip import lookup_country
from api.settings import get_setting


def _cascade_delete_node_soga(session: Session, node_id: int) -> dict:
    """删节点前清干净 soga 关联数据,避免 DB 孤儿。

    清理范围:
    - 该节点作为入口机时: soga_instances + 下属 soga_routes + soga_route_outs
    - 该节点作为落地机时: 其它入口机的 soga_route_outs.landing_node_id == node_id

    用 raw SQL 按正确 FK 顺序删除,绕开 ORM flush 重排导致的 FK 违反。
    """
    from sqlalchemy import text

    # 1. 作为入口机: 先删 outs → routes → instances (FK 顺序)
    insts = session.exec(select(SogaInstance).where(SogaInstance.node_id == node_id)).all()
    inst_ids = [i.id for i in insts]
    inst_cnt = len(inst_ids)
    route_cnt = 0
    out_cnt = 0

    if inst_ids:
        # 删 outs (子查询方式,不需要 IN 绑定 tuple)
        session.exec(text(
            "DELETE FROM soga_route_outs WHERE route_id IN "
            "(SELECT id FROM soga_routes WHERE instance_id IN "
            "(SELECT id FROM soga_instances WHERE node_id = :nid))"
        ).bindparams(nid=node_id))

        # 统计 (近似,删前查)
        routes = session.exec(select(SogaRoute).where(
            SogaRoute.instance_id.in_(inst_ids)  # type: ignore
        )).all()
        route_cnt = len(routes)

        # 删 routes
        session.exec(text(
            "DELETE FROM soga_routes WHERE instance_id IN "
            "(SELECT id FROM soga_instances WHERE node_id = :nid)"
        ).bindparams(nid=node_id))

        # 删 instances
        session.exec(text(
            "DELETE FROM soga_instances WHERE node_id = :nid"
        ).bindparams(nid=node_id))

    # 2. 作为落地机: 清其它入口机引用了它的 outs
    session.exec(text(
        "DELETE FROM soga_route_outs WHERE landing_node_id = :nid"
    ).bindparams(nid=node_id))
    dangling_cnt = 0  # raw SQL rowcount 在 SQLModel 下不可靠,简化

    session.flush()
    return {
        "instances": inst_cnt,
        "routes": route_cnt,
        "outs": out_cnt,
        "dangling_landing_outs": dangling_cnt,
    }
from ws.agents import send_cmd, disconnect_node, get_all_metrics, _connections, _write_locks
import json
import asyncio

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def _ok(data=None):
    return {"ok": True, "data": data}


def _err(msg: str):
    return {"ok": False, "error": msg}


@router.get("")
def list_nodes(session: Session = Depends(get_session)):
    nodes = session.exec(select(Node).order_by(Node.sort_order, Node.id)).all()
    return _ok([NodeRead.from_node(n).model_dump(mode="json") for n in nodes])


@router.put("/order")
def reorder_nodes(payload: dict, session: Session = Depends(get_session)):
    """body: {ids: [3, 1, 2, ...]} — 全量传当前顺序, sort_order 按数组下标 *10 写入。"""
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "ids 必须是整数数组")
    for idx, nid in enumerate(ids):
        node = session.get(Node, nid)
        if node:
            node.sort_order = idx * 10
            session.add(node)
    session.commit()
    return _ok({"updated": len(ids)})


@router.get("/metrics")
def all_metrics():
    """快频轮询端点：返回所有在线节点的最新 metrics（探针式，内存）。"""
    return _ok(get_all_metrics())


@router.post("")
def create_node(payload: NodeCreate, session: Session = Depends(get_session)):
    if payload.auth_type not in ("password", "key"):
        raise HTTPException(400, "auth_type 必须是 password 或 key")
    node = Node(**payload.model_dump())
    # 凭据落库前加密
    from security.crypto import encrypt as _enc
    if node.ssh_password:
        node.ssh_password = _enc(node.ssh_password)
    if node.ssh_private_key:
        node.ssh_private_key = _enc(node.ssh_private_key)
    # 新节点排末尾: sort_order = 当前最大 + 10
    from sqlalchemy import func
    max_so = session.exec(select(func.max(Node.sort_order))).one() or 0
    node.sort_order = (max_so or 0) + 10
    session.add(node)
    session.commit()
    session.refresh(node)
    return _ok(NodeRead.from_node(node).model_dump(mode="json"))


@router.get("/{node_id}")
def get_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    return _ok(NodeRead.from_node(node).model_dump(mode="json"))


@router.patch("/{node_id}")
async def update_node(node_id: int, payload: NodeUpdate, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "auth_type" in changes and changes["auth_type"] not in ("password", "key"):
        raise HTTPException(400, "auth_type 必须是 password 或 key")
    # 凭据字段如果传了,加密后再写
    from security.crypto import encrypt as _enc
    if "ssh_password" in changes and changes["ssh_password"]:
        changes["ssh_password"] = _enc(changes["ssh_password"])
    if "ssh_private_key" in changes and changes["ssh_private_key"]:
        changes["ssh_private_key"] = _enc(changes["ssh_private_key"])
    for k, v in changes.items():
        setattr(node, k, v)
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()
    session.refresh(node)
    # PATCH 仅写 DB,不再下发任何 agent 命令(显式接口: POST /{id}/agent/apply-iface)
    return _ok(NodeRead.from_node(node).model_dump(mode="json"))


@router.post("/{node_id}/agent/apply-iface")
async def apply_iface(node_id: int, session: Session = Depends(get_session)):
    """显式将当前 DB 中的 agent_iface 下发给在线 agent。"""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node.deploy_status != "deployed":
        raise HTTPException(400, "节点尚未部署,无法下发 iface")
    ws = _connections.get(node_id)
    lock = _write_locks.get(node_id)
    if ws is None or lock is None:
        raise HTTPException(400, "agent 当前不在线")
    try:
        async with lock:
            await ws.send_text(json.dumps({
                "type": "set_iface",
                "iface": node.agent_iface or "",
            }))
    except Exception as e:
        raise HTTPException(500, f"下发失败: {e}")
    return _ok({"id": node_id, "iface": node.agent_iface or ""})


@router.delete("/{node_id}")
async def delete_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    await disconnect_node(node_id)
    cascade = _cascade_delete_node_soga(session, node_id)
    session.delete(node)
    session.commit()
    return _ok({"id": node_id, "cascade": cascade})


@router.post("/{node_id}/uninstall")
async def uninstall(
    node_id: int,
    payload: dict = None,
    session: Session = Depends(get_session),
):
    """一键卸载节点。

    body:
      delete_node: bool   卸载后是否同时从面板删除节点记录
      force:       bool   SSH 不通时强制走（仅在 delete_node=true 时有意义，
                          单纯从 DB 删除，不清远端）
    """
    payload = payload or {}
    delete_after = bool(payload.get("delete_node", False))
    force = bool(payload.get("force", False))

    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")

    if force and delete_after:
        # 不连远端，直接踢 WS + 清 soga 关联 + 删 DB
        await disconnect_node(node_id)
        cascade = _cascade_delete_node_soga(session, node_id)
        session.delete(node)
        session.commit()
        return _ok({
            "success": True,
            "forced": True,
            "log": "force mode: 跳过远端清理，仅从面板删除节点",
            "deleted": True,
            "cascade": cascade,
        })

    # 正常路径：跑远端卸载
    # uninstall_node 是同步 paramiko (~60s),必须切线程否则阻塞 event loop
    import asyncio
    result = await asyncio.to_thread(uninstall_node, node)

    # 不管成功失败，先踢 WS
    await disconnect_node(node_id)

    if result.ok:
        if delete_after:
            cascade = _cascade_delete_node_soga(session, node_id)
            session.delete(node)
            session.commit()
            return _ok({
                "success": True,
                "log": result.log,
                "deleted": True,
                "cascade": cascade,
            })
        # 保留记录：重置状态
        node.deploy_status = "uninstalled"
        node.deploy_log = result.log
        node.agent_status = "offline"
        node.agent_last_seen = None
        node.ss_apply_status = "pending"
        node.ss_applied_at = None
        node.ss_apply_error = None
        node.singbox_version = None
        node.arch = None
        node.config_schema = None
        node.deployed_at = None
        node.updated_at = datetime.now(timezone.utc)
        session.add(node)
        session.commit()
        session.refresh(node)
        return _ok({
            "success": True,
            "log": result.log,
            "deleted": False,
            "node": NodeRead.from_node(node).model_dump(mode="json"),
        })

    # 失败：DB 不动
    return _ok({
        "success": False,
        "error": result.error,
        "log": result.log,
        "deleted": False,
    })


@router.post("/{node_id}/deploy")
def deploy(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")

    # 确保 agent_token 存在（用于 WS 鉴权）
    if not node.agent_token:
        node.agent_token = uuid.uuid4().hex

    node.deploy_status = "deploying"
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()

    agent_endpoint = get_setting(session, "agent_endpoint")
    result = deploy_node(node, agent_endpoint)

    node.deploy_log = result.log or (result.error or "")
    node.updated_at = datetime.now(timezone.utc)

    if result.ok:
        node.deploy_status = "deployed"
        node.singbox_version = result.version
        node.arch = result.arch
        node.config_schema = derive_schema(result.version or "")
        node.deployed_at = node.updated_at
        # 国旗自动识别：country 为空才查，手动选过的不覆盖
        if not node.country:
            cc = lookup_country(node.host)
            if cc:
                node.country = cc
    else:
        node.deploy_status = "failed"

    session.add(node)
    session.commit()
    session.refresh(node)

    return _ok({
        "success": result.ok,
        "error": result.error,
        "version": result.version,
        "arch": result.arch,
        "singbox_status": result.singbox_status,
        "agent_status": result.agent_status,
        "agent_endpoint_configured": bool(agent_endpoint),
        "node": NodeRead.from_node(node).model_dump(mode="json"),
    })


@router.post("/{node_id}/geoip")
def refresh_geoip(node_id: int, session: Session = Depends(get_session)):
    """手动重查节点国家码（覆盖现有值）。"""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    cc = lookup_country(node.host)
    if not cc:
        return _err("GeoIP 查询失败，请检查网络或手动填写")
    node.country = cc
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()
    return _ok({"country": cc})


@router.post("/{node_id}/agent/redeploy-config")
def redeploy_agent_config(node_id: int, session: Session = Depends(get_session)):
    """改 endpoint 后单节点推送 agent.env 并重启 agent。"""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    agent_endpoint = get_setting(session, "agent_endpoint")
    if not agent_endpoint:
        raise HTTPException(400, "agent_endpoint 未配置，请先到设置页填写")
    ok, msg = push_agent_env(node, agent_endpoint)
    return _ok({"success": ok, "message": msg})


@router.post("/{node_id}/agent/reload")
async def agent_reload(node_id: int, session: Session = Depends(get_session)):
    """通过 WS 让 agent 立即 reload sing-box。"""
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    ok, msg = await send_cmd(node_id, "reload")
    return _ok({"success": ok, "message": msg})


@router.post("/{node_id}/deploy/reset")
def reset_deploy_status(node_id: int, session: Session = Depends(get_session)):
    """强制重置卡死的部署状态（仅当前状态为 deploying 时允许）。

    用于后端被中断/重启导致 deploy_status 永远停在 deploying，
    前端按钮被锁死无法重新部署的情况。
    """
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node.deploy_status != "deploying":
        raise HTTPException(400, f"当前状态为 {node.deploy_status}，仅 deploying 可重置")

    node.deploy_status = "failed"
    node.deploy_log = (node.deploy_log or "") + "\n[用户强制重置] 部署状态从 deploying 重置为 failed"
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()
    session.refresh(node)
    return _ok({"node": NodeRead.from_node(node).model_dump(mode="json")})


@router.get("/{node_id}/deploy/log")
def get_deploy_log(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    return _ok({
        "deploy_status": node.deploy_status,
        "deploy_log": node.deploy_log or "",
        "deployed_at": node.deployed_at.isoformat() if node.deployed_at else None,
    })


