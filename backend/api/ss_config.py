"""/api/nodes/{id}/ss-config —— SS 配置 CRUD + apply + preview。"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from database import get_session
from models.node import Node
from deploy.singbox_config import (
    DEFAULT_SS_CONFIG,
    SUPPORTED_METHODS,
    SUPPORTED_PROTOCOLS,
    SUPPORTED_SNIFFERS,
    LOG_LEVELS,
    DNS_STRATEGIES,
    render_singbox_config,
    validate_ss_config,
)
from deploy.config_push import push_config
from ws.agents import send_cmd

router = APIRouter(prefix="/api/nodes", tags=["ss-config"])


def _ok(data=None):
    return {"ok": True, "data": data}


def _load_ss(node: Node) -> dict:
    if not node.ss_config:
        return dict(DEFAULT_SS_CONFIG)
    try:
        cfg = json.loads(node.ss_config)
    except json.JSONDecodeError:
        return dict(DEFAULT_SS_CONFIG)
    # 补全缺省字段（兼容老数据）
    merged = dict(DEFAULT_SS_CONFIG)
    merged.update(cfg)
    return merged


@router.get("/ss-config/options")
def list_options():
    """前端下拉数据。"""
    return _ok({
        "protocols": SUPPORTED_PROTOCOLS,
        "methods": SUPPORTED_METHODS,
        "sniffers": SUPPORTED_SNIFFERS,
        "log_levels": LOG_LEVELS,
        "dns_strategies": DNS_STRATEGIES,
        "defaults": DEFAULT_SS_CONFIG,
    })


@router.get("/{node_id}/ss-config")
def get_ss(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    return _ok({
        "config": _load_ss(node),
        "apply_status": node.ss_apply_status,
        "applied_at": node.ss_applied_at.isoformat() if node.ss_applied_at else None,
        "apply_error": node.ss_apply_error,
    })


@router.put("/{node_id}/ss-config")
def put_ss(node_id: int, payload: dict, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    ok, err = validate_ss_config(payload)
    if not ok:
        raise HTTPException(400, err)
    node.ss_config = json.dumps(payload, ensure_ascii=False)
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()
    return _ok({"config": _load_ss(node)})


@router.get("/{node_id}/ss-config/preview")
def preview_ss(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    ss = _load_ss(node)
    schema = node.config_schema or "singbox-1.13"
    return _ok({
        "singbox_config": render_singbox_config(ss, schema),
        "schema": schema,
    })


@router.post("/{node_id}/ss-config/apply")
async def apply_ss(node_id: int, session: Session = Depends(get_session)):
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node.deploy_status != "deployed":
        raise HTTPException(400, "节点尚未部署 sing-box")

    ss = _load_ss(node)
    ok, err = validate_ss_config(ss)
    if not ok:
        raise HTTPException(400, f"配置无效: {err}")

    schema = node.config_schema or "singbox-1.13"
    singbox_json = render_singbox_config(ss, schema)

    # push_config 内部走同步 remote.* (call_sync),必须切线程,否则与主 loop 死锁
    import asyncio
    push = await asyncio.to_thread(push_config, node, singbox_json)
    if not push.ok:
        node.ss_apply_status = "failed"
        node.ss_apply_error = (push.error + "\n" + push.check_output)[:8000]
        node.updated_at = datetime.now(timezone.utc)
        session.add(node)
        session.commit()
        return _ok({
            "success": False,
            "stage": "push",
            "error": push.error,
            "check_output": push.check_output,
        })

    # 触发 reload(agent RPC)。agent 离线直接失败,不再 SSH 兜底。
    reload_ok, reload_msg = await send_cmd(node_id, "reload")
    if not reload_ok:
        reload_msg = f"agent 离线或 reload 失败: {reload_msg or '未知'}"

    if reload_ok:
        node.ss_apply_status = "applied"
        node.ss_applied_at = datetime.now(timezone.utc)
        node.ss_apply_error = None
    else:
        node.ss_apply_status = "failed"
        node.ss_apply_error = f"配置已推送，reload 失败: {reload_msg}"

    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    session.commit()

    return _ok({
        "success": reload_ok,
        "stage": "reload" if not reload_ok else "done",
        "message": reload_msg,
        "applied_at": node.ss_applied_at.isoformat() if node.ss_applied_at else None,
    })
