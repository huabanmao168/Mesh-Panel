"""SoGa 入口机配置 API。

阶段 4 实装:
- POST /api/soga/{node_id}/scan       SSH 扫 /etc/soga/*/routes.toml,解析入库
- GET  /api/soga/{node_id}/instances  列出已扫描的实例(从 DB 取)
- GET  /api/soga/instances/{id}/routes 拉某个实例的完整路由树
"""
import logging
import threading
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy import text, func

from database import get_session
from models.node import Node
from models.soga import SogaInstance, SogaRoute, SogaRouteOut
from deploy.soga_scan import scan_soga_instances, ScanError
from deploy.soga_push import SYSTEM_PROBE_RULES as SYSTEM_PROBE_DEFAULTS


log = logging.getLogger(__name__)


# 每个 node_id 一把锁: 同节点的 scan 串行执行,防止并发 scan
# 互相踩导致 FK 约束失败 / refresh instance not found 等竞态。
_scan_locks: dict[int, threading.Lock] = {}
_scan_locks_guard = threading.Lock()


def _get_scan_lock(node_id: int) -> threading.Lock:
    with _scan_locks_guard:
        lock = _scan_locks.get(node_id)
        if lock is None:
            lock = threading.Lock()
            _scan_locks[node_id] = lock
        return lock

router = APIRouter(prefix="/api/soga", tags=["soga"])


def _require_soga_node(session: Session, node_id: int) -> Node:
    node = session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node.kind != "soga":
        raise HTTPException(400, "仅入口机节点支持 SoGa 操作")
    return node


def _node_probe_rules(node) -> Optional[list]:
    """解析 node.soga_system_probe_rules JSON 字符串,返回 list 或 None(用默认)。"""
    import json
    raw = getattr(node, "soga_system_probe_rules", None)
    if not raw:
        return None
    try:
        v = json.loads(raw)
        if isinstance(v, list) and v:
            return [str(x) for x in v if x]
    except Exception:
        pass
    return None


def _now():
    return datetime.now(timezone.utc)


@router.post("/{node_id}/scan")
def scan_instances(node_id: int, session: Session = Depends(get_session)):
    """SSH 进入入口机扫 /etc/soga/*/routes.toml,解析入库。

    幂等:已存在的 folder 复用同一 instance,只刷新路由树。
    DB 里有但节点上消失的 folder → 标记 enabled=false (不删,保留历史)。

    并发保护: 同一 node 的 scan 串行执行,避免 SQLite FK + ORM autoflush 竞态。
    """
    lock = _get_scan_lock(node_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该节点正在扫描中,请等当前扫描完成")
    try:
        with session.no_autoflush:
            return _do_scan_inner(node_id, session)
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        lock.release()


def _do_scan_inner(node_id: int, session: Session):
    node = _require_soga_node(session, node_id)

    try:
        scan_result = scan_soga_instances(node)
        scanned = scan_result["instances"]
        scanned_soga_version = scan_result.get("soga_version")
    except ScanError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("soga scan 失败 node_id=%s", node_id)
        raise HTTPException(500, f"扫描失败: {type(e).__name__}: {e}")

    seen_folders = {s["folder"] for s in scanned}

    # 入库:逐个 folder upsert
    existing = {i.folder_name: i for i in session.exec(
        select(SogaInstance).where(SogaInstance.node_id == node_id)
    ).all()}

    result = []
    for s in scanned:
        folder = s["folder"]
        inst = existing.get(folder)
        if inst is None:
            # 新实例排末尾
            from sqlalchemy import func as _f
            max_so = session.exec(
                select(_f.max(SogaInstance.sort_order)).where(SogaInstance.node_id == node_id)
            ).one() or 0
            inst = SogaInstance(
                node_id=node_id, folder_name=folder, enabled=True,
                sort_order=(max_so or 0) + 10,
            )
            session.add(inst)
            session.commit()
            session.refresh(inst)
        else:
            inst.enabled = True
            inst.updated_at = _now()
            session.add(inst)

        # v2.2.8: 扫描不再碰路由表 — DB 是 routes 的唯一 source of truth
        # 用户只能通过「保存路由」UI 改 soga_routes/soga_route_outs
        session.commit()

        # 取当前 DB 路由数(展示用)
        route_count = session.exec(
            select(func.count()).select_from(SogaRoute).where(SogaRoute.instance_id == inst.id)
        ).one()

        result.append({
            "id": inst.id,
            "folder_name": folder,
            "display_name": inst.display_name,
            "route_count": route_count,
            "enabled": True,
        })

    # 节点上消失的 folder → 标记 disabled
    for folder, inst in existing.items():
        if folder not in seen_folders and inst.enabled:
            inst.enabled = False
            inst.updated_at = _now()
            session.add(inst)

    # 记录本次扫描时间 + soga 版本(扫描时同一条 SSH 里取的)
    node.soga_last_scanned_at = _now()
    if scanned_soga_version:
        node.soga_version = scanned_soga_version
    session.add(node)
    session.commit()

    # 自动接管 routes_url:扫完后遍历每个 enabled instance,
    # 远端 conf 的 routes_url 与期望值不一致时纠正 + 重启 soga。
    # 错误只记日志,不阻断扫描返回。
    reconciled = _auto_reconcile_routes_url(session, node)

    return {
        "ok": True,
        "instances": result,
        "last_scanned_at": node.soga_last_scanned_at.isoformat(),
        "reconciled": reconciled,
    }


def _auto_reconcile_routes_url(session: Session, node) -> dict:
    """扫描收尾:确保所有 enabled instance 的 soga.conf 含正确的 routes_url。

    幂等: 远端值已对齐则跳过。差异才写 conf + 重启 soga。
    panel_public_url 没配 → 整体跳过(不抛错,等用户去 settings 配)。
    """
    from deploy.soga_push import (
        read_conf, write_conf, _strip_routes_url, _parse_routes_url,
        restart_soga, SogaPushError,
    )
    public_url = _panel_public_url(session)
    if not public_url:
        return {"skipped": "panel_public_url 未配置"}

    instances = session.exec(
        select(SogaInstance).where(
            SogaInstance.node_id == node.id,
            SogaInstance.enabled == True,  # noqa: E712
        )
    ).all()

    corrected = 0
    failed = []
    for inst in instances:
        if not inst.routes_token:
            inst.routes_token = _gen_routes_token()
            session.add(inst)
            session.commit()
        expected = _build_routes_url(public_url, inst.id, inst.routes_token)
        try:
            conf = read_conf(node, inst.folder_name)
            current = _parse_routes_url(conf)
            if current == expected:
                continue  # 已对齐
            new_text = _strip_routes_url(conf) + f"routes_url={expected}\n"
            write_conf(node, inst.folder_name, new_text)
            restart_soga(node, inst.folder_name)
            corrected += 1
        except SogaPushError as e:
            failed.append({"folder": inst.folder_name, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("auto_reconcile 单实例失败 folder=%s", inst.folder_name)
            failed.append({"folder": inst.folder_name, "error": f"{type(e).__name__}: {e}"})
    return {"corrected": corrected, "failed": failed, "total": len(instances)}


def _match_landing_node(session: Session, out: dict) -> Optional[int]:
    """根据 toml 出站 (server/port) 反查面板里的 landing 节点。

    匹配规则:host == node.host AND port == ss_config.listen_port
    SS / SOCKS 都靠 listen_port 匹配。
    """
    from models.node import Node
    server = out.get("server")
    port = out.get("port")
    if not server or not port:
        return None
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return None
    nodes = session.exec(select(Node).where(Node.kind == "landing", Node.host == server)).all()
    import json as _json
    for n in nodes:
        cfg_raw = getattr(n, "ss_config", None)
        if not cfg_raw:
            continue
        try:
            cfg = _json.loads(cfg_raw) if isinstance(cfg_raw, str) else cfg_raw
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        lp = cfg.get("listen_port")
        try:
            lp_int = int(lp) if lp is not None else None
        except (TypeError, ValueError):
            continue
        if lp_int is not None and lp_int == port_int:
            return n.id
    return None


@router.put("/{node_id}/instances/order")
def reorder_instances(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """body: {ids: [3, 1, 2, ...]} — 全量当前顺序, sort_order = idx*10。"""
    _require_soga_node(session, node_id)
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "ids 必须是整数数组")
    updated = 0
    for idx, iid in enumerate(ids):
        inst = session.get(SogaInstance, iid)
        if inst and inst.node_id == node_id:
            inst.sort_order = idx * 10
            session.add(inst)
            updated += 1
    session.commit()
    return {"ok": True, "updated": updated}


@router.get("/{node_id}/instances")
def list_instances(node_id: int, session: Session = Depends(get_session)):
    node = _require_soga_node(session, node_id)
    rows = session.exec(
        select(SogaInstance).where(SogaInstance.node_id == node_id).order_by(SogaInstance.sort_order, SogaInstance.id)
    ).all()
    items = []
    for inst in rows:
        # 用户路由 = 不算系统探活 (新逻辑系统探活不入库,老数据 is_system=True 也排除)
        cnt = len(session.exec(
            select(SogaRoute).where(
                SogaRoute.instance_id == inst.id,
                SogaRoute.is_system == False,  # noqa: E712
            )
        ).all())
        items.append({
            "id": inst.id,
            "folder_name": inst.folder_name,
            "display_name": inst.display_name,
            "enabled": inst.enabled,
            "sort_order": inst.sort_order,
            "route_count": cnt,
            "route_source": inst.route_source or "file",
            "routes_token": inst.routes_token,
            "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
        })
    return {
        "ok": True,
        "instances": items,
        "system_probe": bool(getattr(node, "soga_system_probe", True)),
        "system_probe_rules": _node_probe_rules(node) or list(SYSTEM_PROBE_DEFAULTS),
        "system_probe_custom": _node_probe_rules(node) is not None,
        "last_scanned_at": node.soga_last_scanned_at.isoformat() if node.soga_last_scanned_at else None,
    }


@router.patch("/{node_id}/system-probe")
def update_system_probe(node_id: int, payload: dict, session: Session = Depends(get_session)):
    """切换节点的系统探活开关 / 自定义规则。只保存,不重推(重推走 /push-all)。

    payload: {
        "enabled": bool (可选,不传则保持原值),
        "rules": list[str] | None (可选,传 list 即自定义,传 null 即恢复默认,不传则保持原值)
    }
    """
    import json

    node = _require_soga_node(session, node_id)
    if "enabled" in payload:
        node.soga_system_probe = bool(payload["enabled"])
    if "rules" in payload:
        rules = payload["rules"]
        if rules is None:
            node.soga_system_probe_rules = None
        elif isinstance(rules, list):
            cleaned = [str(x).strip() for x in rules if str(x).strip()]
            if not cleaned:
                raise HTTPException(400, "自定义规则不能为空(传 null 恢复默认)")
            node.soga_system_probe_rules = json.dumps(cleaned, ensure_ascii=False)
        else:
            raise HTTPException(400, "rules 必须是数组或 null")
    node.updated_at = _now()
    session.add(node)
    session.commit()

    probe_rules = _node_probe_rules(node)
    return {
        "ok": True,
        "enabled": bool(node.soga_system_probe),
        "rules": probe_rules or list(SYSTEM_PROBE_DEFAULTS),
        "custom": probe_rules is not None,
    }


@router.post("/{node_id}/push-all")
def push_all_instances(node_id: int, session: Session = Depends(get_session)):
    """一键同步所有 enabled 实例的 routes_url 配置 + 重启 soga。

    新语义(v2.2.6+): 不再下发 routes.toml,改为写 soga.conf 的 routes_url +
    `soga restart <folder>` 让 soga 重新从面板 HTTP 拉取路由。
    """
    from deploy.soga_push import (
        read_conf, write_conf, _strip_routes_url, _parse_routes_url,
        restart_soga, SogaPushError,
    )

    node = _require_soga_node(session, node_id)
    public_url = _panel_public_url(session)
    if not public_url:
        raise HTTPException(400, "请先在系统设置填写「面板公网地址(panel_public_url)」")

    instances = session.exec(
        select(SogaInstance).where(
            SogaInstance.node_id == node_id,
            SogaInstance.enabled == True,  # noqa: E712
        )
    ).all()

    pushed = 0
    failed = []
    for inst in instances:
        try:
            if not inst.routes_token:
                inst.routes_token = _gen_routes_token()
                session.add(inst)
                session.commit()
            expected = _build_routes_url(public_url, inst.id, inst.routes_token)
            conf = read_conf(node, inst.folder_name)
            if _parse_routes_url(conf) != expected:
                write_conf(node, inst.folder_name, _strip_routes_url(conf) + f"routes_url={expected}\n")
            restart_soga(node, inst.folder_name)
            pushed += 1
        except SogaPushError as e:
            failed.append({"folder": inst.folder_name, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("soga push_all 单实例失败 folder=%s", inst.folder_name)
            failed.append({"folder": inst.folder_name, "error": f"{type(e).__name__}: {e}"})

    return {
        "ok": True,
        "pushed": pushed,
        "failed": failed,
        "total": len(instances),
    }


@router.get("/instances/{instance_id}/conf")
def get_instance_conf(instance_id: int, session: Session = Depends(get_session)):
    """SSH 拉取该实例 /etc/soga/<folder>/soga.conf 原文。"""
    from deploy.soga_push import read_conf, SogaPushError

    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    node = session.get(Node, inst.node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    try:
        text = read_conf(node, inst.folder_name)
    except SogaPushError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("read_conf 失败 folder=%s", inst.folder_name)
        raise HTTPException(500, f"读取失败: {type(e).__name__}: {e}")
    return {"ok": True, "folder": inst.folder_name, "path": f"/etc/soga/{inst.folder_name}/soga.conf", "text": text}


@router.put("/instances/{instance_id}/conf")
def put_instance_conf(instance_id: int, payload: dict, session: Session = Depends(get_session)):
    """SSH 覆盖写 soga.conf。payload: {text: str}。保存后自动 `soga restart <folder>`。"""
    from deploy.soga_push import write_conf, restart_soga, SogaPushError

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "text 不能为空")
    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    node = session.get(Node, inst.node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    try:
        write_conf(node, inst.folder_name, text)
    except SogaPushError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("write_conf 失败 folder=%s", inst.folder_name)
        raise HTTPException(500, f"保存失败: {type(e).__name__}: {e}")
    # 写完自动重启
    restart_ok = True
    restart_msg = ""
    try:
        restart_msg = restart_soga(node, inst.folder_name)
    except SogaPushError as e:
        restart_ok = False
        restart_msg = str(e)
    except Exception as e:  # noqa: BLE001
        log.exception("restart_soga 失败 folder=%s", inst.folder_name)
        restart_ok = False
        restart_msg = f"{type(e).__name__}: {e}"
    return {"ok": True, "restarted": restart_ok, "restart_output": restart_msg}


@router.post("/instances/{instance_id}/restart")
def restart_instance(instance_id: int, session: Session = Depends(get_session)):
    """SSH 跑 `soga restart <folder>`。"""
    from deploy.soga_push import restart_soga, SogaPushError

    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    node = session.get(Node, inst.node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    try:
        out = restart_soga(node, inst.folder_name)
    except SogaPushError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("restart endpoint 失败 folder=%s", inst.folder_name)
        raise HTTPException(500, f"重启失败: {type(e).__name__}: {e}")
    return {"ok": True, "output": out}


@router.patch("/instances/{instance_id}")
def patch_instance(instance_id: int, payload: dict, session: Session = Depends(get_session)):
    """更新实例属性。当前只允许改 display_name(别名)。"""
    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    if "display_name" in payload:
        v = payload.get("display_name")
        if v is not None and not isinstance(v, str):
            raise HTTPException(400, "display_name 必须是字符串")
        if isinstance(v, str):
            v = v.strip()
            if len(v) > 64:
                raise HTTPException(400, "别名最长 64 字符")
        inst.display_name = v or None
        inst.updated_at = _now()
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return {
        "ok": True,
        "instance": {
            "id": inst.id,
            "folder_name": inst.folder_name,
            "display_name": inst.display_name,
        },
    }


@router.delete("/instances/{instance_id}")
def delete_instance(instance_id: int, session: Session = Depends(get_session)):
    """删除一个已消失的实例(级联清 routes / route_outs)。

    安全规则:仅允许 enabled=False 的实例被删,防止误删还活着的。
    活实例要清,只能在节点上 rm -rf /etc/soga/<folder>/ 后再扫一次。
    """
    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    if inst.enabled:
        raise HTTPException(400, "实例仍存活,请先在节点删除对应 /etc/soga/<folder>/ 目录并重新扫描")

    # 级联清:routes -> route_outs -> instance(全 raw SQL,绕开 ORM autoflush 的 FK 顺序问题,与 scan 保持一致)
    folder = inst.folder_name
    conn = session.connection()
    conn.execute(
        text("DELETE FROM soga_route_outs WHERE route_id IN (SELECT id FROM soga_routes WHERE instance_id = :iid)"),
        {"iid": instance_id},
    )
    conn.execute(text("DELETE FROM soga_routes WHERE instance_id = :iid"), {"iid": instance_id})
    conn.execute(text("DELETE FROM soga_instances WHERE id = :iid"), {"iid": instance_id})
    session.commit()
    return {"ok": True, "deleted": {"id": instance_id, "folder_name": folder}}


@router.post("/instances/{instance_id}/push")
def push_single_instance(instance_id: int, session: Session = Depends(get_session)):
    """同步单个实例的 routes_url 配置 + 重启 soga(新语义,v2.2.6+)。"""
    from deploy.soga_push import (
        read_conf, write_conf, _strip_routes_url, _parse_routes_url,
        restart_soga, SogaPushError,
    )

    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    node = session.get(Node, inst.node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if not inst.enabled:
        raise HTTPException(400, "实例已消失,无法推送")
    public_url = _panel_public_url(session)
    if not public_url:
        raise HTTPException(400, "请先在系统设置填写「面板公网地址(panel_public_url)」")
    try:
        if not inst.routes_token:
            inst.routes_token = _gen_routes_token()
            session.add(inst)
            session.commit()
        expected = _build_routes_url(public_url, inst.id, inst.routes_token)
        conf = read_conf(node, inst.folder_name)
        if _parse_routes_url(conf) != expected:
            write_conf(node, inst.folder_name, _strip_routes_url(conf) + f"routes_url={expected}\n")
        restart_soga(node, inst.folder_name)
    except SogaPushError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("push routes 失败 folder=%s", inst.folder_name)
        raise HTTPException(500, f"推送失败: {type(e).__name__}: {e}")
    return {"ok": True, "folder": inst.folder_name}


def _load_instance_routes_for_push(session: Session, instance_id: int):
    """从 DB 拉一个实例的路由 + 关联落地节点,返回 (routes_list, landings_map),
    供 render_routes_toml 使用。"""
    routes = session.exec(
        select(SogaRoute).where(
            SogaRoute.instance_id == instance_id,
            SogaRoute.is_system == False,  # noqa: E712
        ).order_by(SogaRoute.position)
    ).all()
    routes_data = []
    needed_ids = set()
    for r in routes:
        outs = session.exec(
            select(SogaRouteOut).where(SogaRouteOut.route_id == r.id).order_by(SogaRouteOut.position)
        ).all()
        out_list = []
        for o in outs:
            if o.landing_node_id:
                needed_ids.add(o.landing_node_id)
            out_list.append({"landing_node_id": o.landing_node_id, "listen": getattr(o, "listen", "") or ""})
        routes_data.append({
            "rules": r.rules or [],
            "balance": r.balance,
            "is_fallback": r.is_fallback,
            "outs": out_list,
        })
    landings = {}
    if needed_ids:
        landings = {n.id: n for n in session.exec(select(Node).where(Node.id.in_(needed_ids))).all()}
    return routes_data, landings


@router.get("/instances/{instance_id}/routes")
def get_instance_routes(instance_id: int, session: Session = Depends(get_session)):
    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    # 系统探活路由不再返回前端(由节点级开关管),只返用户路由
    routes = session.exec(
        select(SogaRoute).where(
            SogaRoute.instance_id == instance_id,
            SogaRoute.is_system == False,  # noqa: E712
        ).order_by(SogaRoute.position)
    ).all()
    data = []
    for r in routes:
        outs = session.exec(
            select(SogaRouteOut).where(SogaRouteOut.route_id == r.id).order_by(SogaRouteOut.position)
        ).all()
        data.append({
            "id": r.id,
            "position": r.position,
            "rules": r.rules,
            "balance": r.balance,
            "is_system": r.is_system,
            "is_fallback": r.is_fallback,
            "remark": r.remark,
            "outs": [{"id": o.id, "landing_node_id": o.landing_node_id, "listen": getattr(o, "listen", "") or ""} for o in outs],
        })
    return {
        "ok": True,
        "instance": {
            "id": inst.id,
            "folder_name": inst.folder_name,
            "display_name": inst.display_name,
            "route_source": inst.route_source or "file",
        },
        "routes": data,
    }


@router.put("/instances/{instance_id}/routes")
def save_instance_routes(instance_id: int, payload: dict, session: Session = Depends(get_session)):
    """保存路由 → DB。

    v2.2.6+ 后路由分发统一走 HTTP 拉取(soga -routes_url 指向面板),
    保存只写 DB,不下发 routes.toml,soga 下次拉取时自动取到新数据。
    如需立即生效,UI 上用「同步配置」按钮显式调一次 set_routes_url+restart_soga。

    payload: {"routes": [
        {"rules":[...], "balance":"ip_hash"|null,
         "is_system":bool, "is_fallback":bool, "remark":str|null,
         "outs":[{"landing_node_id": int|null, "listen": str|null}]}
    ]}
    """
    from deploy.soga_push import render_routes_toml, SogaPushError

    inst = session.get(SogaInstance, instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    node = session.get(Node, inst.node_id)
    if not node:
        raise HTTPException(404, "入口机节点不存在")

    routes_in = payload.get("routes") or []
    if not isinstance(routes_in, list) or not routes_in:
        raise HTTPException(400, "routes 不能为空")

    # 系统探活路由已外置到节点级开关,前端不该再传 is_system,统一过滤掉防止误塞
    routes_in = [r for r in routes_in if not r.get("is_system")]
    if not routes_in:
        raise HTTPException(400, "至少要有一条兜底路由")

    # 校验: 必须恰好 1 条兜底 + 兜底必须在末尾
    fallback_count = sum(1 for r in routes_in if r.get("is_fallback") or r.get("rules") == ["*"])
    if fallback_count != 1:
        raise HTTPException(400, f"必须有且仅有一条兜底路由(当前 {fallback_count})")
    last = routes_in[-1]
    if not (last.get("is_fallback") or last.get("rules") == ["*"]):
        raise HTTPException(400, "兜底路由必须排在最后")

    # 收集所有 landing_node_id,一次性查
    needed_ids = set()
    for r in routes_in:
        for o in (r.get("outs") or []):
            lid = o.get("landing_node_id")
            if lid:
                needed_ids.add(int(lid))
    landings = {n.id: n for n in session.exec(select(Node).where(Node.id.in_(needed_ids))).all()} if needed_ids else {}

    # 每个 out 都要有有效落地
    for idx, r in enumerate(routes_in):
        outs = r.get("outs") or []
        if not outs:
            raise HTTPException(400, f"路由 #{idx+1} 出站为空")
        for o in outs:
            lid = o.get("landing_node_id")
            if not lid or lid not in landings:
                raise HTTPException(400, f"路由 #{idx+1} 关联了不存在的落地节点")
            if landings[lid].kind != "landing":
                raise HTTPException(400, f"路由 #{idx+1} 关联节点必须是落地机")

    # 预渲染一次 TOML 仅用于校验 + 计算 bytes(出错及早抛,真实数据走 GET routes.toml 端点)
    enable_probe = bool(getattr(node, "soga_system_probe", True))
    probe_rules = _node_probe_rules(node)
    try:
        toml_text = render_routes_toml(
            routes_in, landings,
            enable_system_probe=enable_probe,
            system_probe_rules=probe_rules,
        )
    except SogaPushError as e:
        raise HTTPException(500, f"渲染失败: {e}")
    except Exception as e:
        log.exception("render_routes_toml 失败 instance_id=%s", instance_id)
        raise HTTPException(500, f"渲染异常: {type(e).__name__}: {e}")

    # 写 DB
    old_routes = session.exec(select(SogaRoute).where(SogaRoute.instance_id == instance_id)).all()
    for r in old_routes:
        session.exec(text(f"DELETE FROM soga_route_outs WHERE route_id={r.id}"))
        session.delete(r)
    session.flush()

    for pos, r in enumerate(routes_in):
        rec = SogaRoute(
            instance_id=instance_id,
            position=pos,
            rules=r.get("rules") or [],
            balance=r.get("balance"),
            is_system=False,
            is_fallback=bool(r.get("is_fallback") or r.get("rules") == ["*"]),
            remark=r.get("remark"),
        )
        session.add(rec)
        session.flush()
        for opos, o in enumerate(r.get("outs") or []):
            session.add(SogaRouteOut(
                route_id=rec.id,
                position=opos,
                landing_node_id=o.get("landing_node_id"),
                listen=(o.get("listen") or "").strip(),
            ))
    session.commit()

    return {"ok": True, "saved": True, "bytes": len(toml_text.encode("utf-8"))}


# ─── 路由分发模式切换 ────────────────────────────────────────────────────────
# v2.2.6+ 路由分发统一走 HTTP 拉取(soga -routes_url),已不再保留 file 模式切换接口。
# 历史端点 POST /instances/{id}/route-source 已删除,前端「分发模式 radio」也已下线。

def _gen_routes_token() -> str:
    import secrets
    return secrets.token_hex(32)


def _panel_public_url(session: Session) -> str:
    from api.settings import get_setting as _gs
    return (_gs(session, "panel_public_url", "") or "").rstrip("/")


def _build_routes_url(public_url: str, instance_id: int, token: str) -> str:
    return f"{public_url}/api/soga/instances/{instance_id}/routes.toml?token={token}"


# ─── 公开端点: soga 拉 routes.toml ───────────────────────────────────────────

@router.get("/instances/{instance_id}/routes.toml")
def serve_routes_toml(instance_id: int, token: str = "", session: Session = Depends(get_session)):
    """soga -routes_url 拉这里。token 错或缺失返 444 空 body 不泄漏存在性。"""
    from fastapi.responses import Response, PlainTextResponse
    from deploy.soga_push import render_routes_toml

    if not token:
        return Response(status_code=444)
    inst = session.get(SogaInstance, instance_id)
    if not inst or not inst.routes_token or not _consteq(token, inst.routes_token):
        return Response(status_code=444)
    node = session.get(Node, inst.node_id)
    if not node:
        return Response(status_code=444)

    enable_probe = bool(getattr(node, "soga_system_probe", True))
    probe_rules = _node_probe_rules(node)
    routes_data, landings_map = _load_instance_routes_for_push(session, inst.id)
    try:
        toml_text = render_routes_toml(
            routes_data, landings_map,
            enable_system_probe=enable_probe,
            system_probe_rules=probe_rules,
        )
    except Exception:
        log.exception("serve_routes_toml 渲染失败 instance_id=%s folder=%s", inst.id, inst.folder_name)
        return Response(status_code=444)
    return PlainTextResponse(toml_text, media_type="application/toml")


def _consteq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)
