"""/api/settings —— 全局设置 CRUD + 证书上传。"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import Session, select

from config import DATA_DIR
from database import get_session
from models.setting import Setting, DEFAULTS

router = APIRouter(prefix="/api/settings", tags=["settings"])

CERT_DIR = DATA_DIR / "certs"
CERT_DIR.mkdir(parents=True, exist_ok=True)


@router.get("")
def list_settings(session: Session = Depends(get_session)):
    rows = session.exec(select(Setting)).all()
    data = {row.key: row.value for row in rows}
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return {"ok": True, "data": data}


def _validate(payload: dict, session: Session = None) -> None:
    """改写前的字段合法性校验。"""
    if "panel_port" in payload:
        try:
            p = int(payload["panel_port"])
        except (ValueError, TypeError):
            raise HTTPException(400, "panel_port 必须是整数")
        if not (1 <= p <= 65535):
            raise HTTPException(400, "panel_port 必须在 1-65535")

    if "panel_host" in payload:
        h = str(payload["panel_host"]).strip()
        if not h:
            raise HTTPException(400, "panel_host 不能为空")

    if "tls_enabled" in payload and str(payload["tls_enabled"]) in ("1", "true", "True"):
        # 启用 TLS 时,证书路径必须存在(payload 没传就回落到 DB 现值)
        cert = _read(payload, "tls_cert_path")
        key = _read(payload, "tls_key_path")
        if (not cert or not key) and session is not None:
            if not cert:
                row = session.get(Setting, "tls_cert_path")
                cert = (row.value if row else "") or ""
            if not key:
                row = session.get(Setting, "tls_key_path")
                key = (row.value if row else "") or ""
        if not cert or not key:
            raise HTTPException(400, "启用 TLS 前请先上传证书和私钥")


def _read(payload: dict, key: str) -> str:
    return str(payload.get(key, "")).strip()


@router.patch("")
def update_settings(payload: dict, session: Session = Depends(get_session)):
    _validate(payload, session)
    now = datetime.now(timezone.utc)
    for k, v in payload.items():
        if k not in DEFAULTS:
            continue
        if not isinstance(v, str):
            v = str(v)
        row = session.get(Setting, k)
        if row is None:
            row = Setting(key=k, value=v, updated_at=now)
        else:
            row.value = v
            row.updated_at = now
        session.add(row)
    session.commit()
    return list_settings(session)


@router.post("/cert")
async def upload_cert(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """上传证书 + 私钥,落盘到 data/certs/,DB 记录绝对路径。

    不验证证书内容(让 uvicorn 起服务时报错来兜底)。重启服务后才生效。
    """
    cert_bytes = await cert.read()
    key_bytes = await key.read()

    if not cert_bytes or not key_bytes:
        raise HTTPException(400, "证书或私钥为空")
    # 简单嗅探一下
    if b"BEGIN CERTIFICATE" not in cert_bytes:
        raise HTTPException(400, "证书文件格式不对(应为 PEM,包含 BEGIN CERTIFICATE)")
    if b"PRIVATE KEY" not in key_bytes:
        raise HTTPException(400, "私钥文件格式不对(应为 PEM,包含 PRIVATE KEY)")

    cert_path = CERT_DIR / "fullchain.pem"
    key_path = CERT_DIR / "privkey.pem"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    # 私钥权限收紧
    try:
        key_path.chmod(0o600)
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    for k, v in [("tls_cert_path", str(cert_path)), ("tls_key_path", str(key_path))]:
        row = session.get(Setting, k)
        if row is None:
            row = Setting(key=k, value=v, updated_at=now)
        else:
            row.value = v
            row.updated_at = now
        session.add(row)
    session.commit()

    return {"ok": True, "data": {"tls_cert_path": str(cert_path), "tls_key_path": str(key_path)}}


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value
