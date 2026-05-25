"""认证模块：JWT cookie + 单管理员账户（密码 bcrypt）。

设计：
- 首次访问无管理员 → 前端弹「设置」表单 → POST /auth/setup
- 登录 → 后端签 JWT 塞 httpOnly cookie（30 天）
- 中间件拦截所有 /api/* 路由，除白名单
- JWT secret 启动时若 settings 表没有就随机生成持久化（重启不踢人）
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from database import engine, get_session
from models.setting import Setting

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "socks_panel_session"
TOKEN_TTL = timedelta(days=30)
JWT_ALG = "HS256"

# settings 表 key
K_USERNAME = "admin_username"
K_PWD_HASH = "admin_password_hash"
K_JWT_SECRET = "jwt_secret"


def _setting_get(s: Session, key: str) -> Optional[str]:
    row = s.get(Setting, key)
    return row.value if row and row.value else None


def _setting_set(s: Session, key: str, value: str) -> None:
    row = s.get(Setting, key)
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        row = Setting(key=key, value=value)
    s.add(row)


def get_jwt_secret() -> str:
    """启动时取 secret，没有就生成持久化。"""
    with Session(engine) as s:
        sec = _setting_get(s, K_JWT_SECRET)
        if not sec:
            sec = secrets.token_urlsafe(48)
            _setting_set(s, K_JWT_SECRET, sec)
            s.commit()
        return sec


_JWT_SECRET_CACHE: Optional[str] = None


def jwt_secret() -> str:
    global _JWT_SECRET_CACHE
    if _JWT_SECRET_CACHE is None:
        _JWT_SECRET_CACHE = get_jwt_secret()
    return _JWT_SECRET_CACHE


def _hash_pwd(pwd: str) -> str:
    return bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()


def _verify_pwd(pwd: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pwd.encode(), hashed.encode())
    except Exception:
        return False


def _make_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + TOKEN_TTL,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALG)


def _decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


# ─── 请求模型 ────────────────────────────────────────
class SetupPayload(BaseModel):
    username: str
    password: str


class LoginPayload(BaseModel):
    username: str
    password: str


class ChangePwdPayload(BaseModel):
    old_password: str
    new_password: str


# ─── 路由 ────────────────────────────────────────────
@router.get("/status")
def auth_status(session: Session = Depends(get_session)):
    """返回是否已设置管理员 + 当前是否登录。"""
    has_admin = bool(_setting_get(session, K_USERNAME))
    return {"ok": True, "data": {"setup_required": not has_admin}}


@router.post("/setup")
def auth_setup(payload: SetupPayload, response: Response, session: Session = Depends(get_session)):
    if _setting_get(session, K_USERNAME):
        raise HTTPException(400, "管理员已存在，请走登录")
    u = (payload.username or "").strip()
    p = payload.password or ""
    if len(u) < 2 or len(u) > 32:
        raise HTTPException(400, "用户名 2-32 个字符")
    if len(p) < 6:
        raise HTTPException(400, "密码至少 6 位")
    _setting_set(session, K_USERNAME, u)
    _setting_set(session, K_PWD_HASH, _hash_pwd(p))
    session.commit()
    token = _make_token(u)
    _set_cookie(response, token)
    return {"ok": True, "data": {"username": u}}


@router.post("/login")
def auth_login(payload: LoginPayload, response: Response, session: Session = Depends(get_session)):
    u_stored = _setting_get(session, K_USERNAME)
    h_stored = _setting_get(session, K_PWD_HASH)
    if not u_stored or not h_stored:
        raise HTTPException(401, "凭据错误")
    if (payload.username or "").strip() != u_stored or not _verify_pwd(payload.password or "", h_stored):
        raise HTTPException(401, "凭据错误")
    token = _make_token(u_stored)
    _set_cookie(response, token)
    return {"ok": True, "data": {"username": u_stored}}


@router.post("/logout")
def auth_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "data": None}


@router.get("/me")
def auth_me(request: Request):
    user = _require_user(request)
    return {"ok": True, "data": {"username": user}}


@router.post("/change-password")
def change_password(payload: ChangePwdPayload, request: Request, session: Session = Depends(get_session)):
    user = _require_user(request)
    h = _setting_get(session, K_PWD_HASH)
    if not h or not _verify_pwd(payload.old_password, h):
        raise HTTPException(401, "原密码错误")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    _setting_set(session, K_PWD_HASH, _hash_pwd(payload.new_password))
    session.commit()
    return {"ok": True, "data": {"username": user}}


# ─── 工具 ────────────────────────────────────────────
def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
        # secure=False —— 裸 http 部署，等接 nginx https 再开
    )


def _require_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    payload = _decode_token(token)
    if not payload:
        raise HTTPException(401, "会话已过期，请重新登录")
    return payload["sub"]


# ─── 中间件用：判断是否需要鉴权 ─────────────────────
AUTH_WHITELIST_PREFIXES = (
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/health",
)


def is_whitelisted(path: str) -> bool:
    # /api/soga/instances/<id>/routes.toml 是 soga 端拉路由的公开端点,自带 token 鉴权,豁免 cookie
    if path.startswith("/api/soga/instances/") and path.endswith("/routes.toml"):
        return True
    return any(path.startswith(p) for p in AUTH_WHITELIST_PREFIXES)


def check_auth(request: Request) -> Optional[str]:
    """对受保护 API 路径校验 cookie，返回 username 或抛 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = _decode_token(token)
    if not payload:
        return None
    return payload.get("sub")
