"""/api/settings —— 全局设置 CRUD。"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from database import get_session
from models.setting import Setting, DEFAULTS

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def list_settings(session: Session = Depends(get_session)):
    rows = session.exec(select(Setting)).all()
    data = {row.key: row.value for row in rows}
    # 补默认值
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return {"ok": True, "data": data}


@router.patch("")
def update_settings(payload: dict, session: Session = Depends(get_session)):
    now = datetime.utcnow()
    for k, v in payload.items():
        if k not in DEFAULTS:
            # 暂时只允许已声明的 key
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


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value
