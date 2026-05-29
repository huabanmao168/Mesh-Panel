"""FastAPI 入口。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from version import __version__ as MESH_VERSION
from config import BASE_DIR, FRONTEND_DIST
from database import init_db
from firstrun import ensure_default_admin
from api.nodes import router as nodes_router
from api.settings import router as settings_router
from api.ss_config import router as ss_router
from api.soga import router as soga_router
from api.auth import router as auth_router, is_whitelisted, check_auth
from ws.agents import router as ws_router, sweep_offline_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    import sys
    log = logging.getLogger(__name__)
    init_db()
    # 首跑确保默认管理员存在
    try:
        ensure_default_admin()
    except Exception as e:
        log.exception("默认管理员初始化失败: %s", e)
    # 一次性把存量明文凭据升级为密文
    # 失败必须退出 — 否则可能用错误的 key 加密新数据,污染存量密文
    try:
        from security.crypto import migrate_plaintext_credentials, FernetKeyMissing
        migrate_plaintext_credentials()
    except FernetKeyMissing as e:
        log.critical("加密密钥丢失,拒绝启动:\n%s", e)
        sys.exit(1)
    except Exception as e:
        log.critical("凭据加密迁移失败,拒绝启动: %s", e, exc_info=True)
        sys.exit(1)
    sweep_task = asyncio.create_task(sweep_offline_loop())
    try:
        yield
    finally:
        sweep_task.cancel()


app = FastAPI(title="MeshPanel", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def host_guard_middleware(request: Request, call_next):
    """域名强制访问:settings.panel_domain 非空时,Host header 必须匹配该域名。
    豁免 /api/health(让运维 curl IP 探活)和本机请求(127.0.0.1/::1)。
    WS 走不同的协议栈,在 ws/agents.py 单独处理(目前 agent 凭 token 鉴权,不校验 Host)。
    """
    from api.settings import get_setting as _gs
    from database import engine as _eng
    from sqlmodel import Session as _S

    path = request.url.path
    if path != "/api/health":
        with _S(_eng) as s:
            domain = _gs(s, "panel_domain", "").strip()
        if domain:
            host = (request.headers.get("host") or "").split(":")[0].lower()
            client_ip = request.client.host if request.client else ""
            if host != domain.lower() and client_ip not in ("127.0.0.1", "::1", "localhost"):
                # 静默拒绝:不回显域名,直接空响应 + 444 风格断连
                return Response(status_code=444)
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """所有 /api/* 路径必须带有效 cookie,白名单除外。WS 不走这里。"""
    path = request.url.path
    if path.startswith("/api/") and not is_whitelisted(path):
        user = check_auth(request)
        if not user:
            return JSONResponse(
                {"ok": False, "error": "未登录或会话已过期"},
                status_code=401,
            )
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True, "data": {"service": "MeshPanel", "version": MESH_VERSION}}


app.include_router(auth_router)
app.include_router(nodes_router)
app.include_router(settings_router)
app.include_router(ss_router)
app.include_router(soga_router)
app.include_router(ws_router)


# --- 前端静态文件服务(生产模式)---------------------------------
# 开发时前端跑 vite :5173,这段不会命中(因为浏览器直连 5173)。
# 生产时 npm run build 产物在 frontend/dist/,
# 由 FastAPI 同端口 serve,前端和 API/WS 同源,agent 走同一端口回连。
# PyInstaller 打包后 FRONTEND_DIST 指向 _MEIPASS/frontend/dist。
if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # /api/* /ws /assets/* 已被前面的路由吃掉,这里只接 SPA 路由
        # 优先返回 dist 下真实存在的文件(favicon.ico, robots.txt 等),
        # 否则 fallback 到 index.html 让 Vue Router 接管
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


# --- PyInstaller 入口:打包后 sys.frozen=True,直接起 uvicorn -----
# 源码模式不会进这里(用 `uvicorn main:app` 起)。
# 端口优先级: 环境变量 MESH_PANEL_PORT > DB settings.panel_port > 默认 8000
# host/tls 同理读 DB,跟 run_server.py 保持一致(DB 是 source of truth)。
if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    # --version / -v: 不启服务,只打印版本号(install.sh 检测当前版本用)
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
        print(MESH_VERSION)
        sys.exit(0)

    import uvicorn
    from sqlmodel import Session
    from database import engine
    from api.settings import get_setting

    # uvicorn.run 之前要先把表建好,否则 get_setting 查空表会 OperationalError
    init_db()

    with Session(engine) as s:
        db_host = get_setting(s, "panel_host", "0.0.0.0") or "0.0.0.0"
        db_port_s = get_setting(s, "panel_port", "8000") or "8000"
        tls_enabled = get_setting(s, "tls_enabled", "0") == "1"
        cert_path = get_setting(s, "tls_cert_path", "")
        key_path = get_setting(s, "tls_key_path", "")

    host = os.environ.get("MESH_PANEL_HOST", db_host)
    port_s = os.environ.get("MESH_PANEL_PORT", db_port_s)
    try:
        port = int(port_s)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        print(f"[main] invalid panel_port={port_s!r}, fallback to 8000", file=sys.stderr)
        port = 8000

    uvicorn_kwargs = {"app": app, "host": host, "port": port, "log_level": "info"}

    if tls_enabled:
        if cert_path and key_path and Path(cert_path).is_file() and Path(key_path).is_file():
            uvicorn_kwargs["ssl_certfile"] = cert_path
            uvicorn_kwargs["ssl_keyfile"] = key_path
            print(f"[main] TLS enabled: cert={cert_path}", file=sys.stderr)
        else:
            print(
                f"[main] TLS enabled but cert/key missing (cert={cert_path!r} "
                f"key={key_path!r}), serving plain HTTP",
                file=sys.stderr,
            )

    print(
        f"[main] starting uvicorn on {host}:{port} "
        f"({'HTTPS' if 'ssl_certfile' in uvicorn_kwargs else 'HTTP'})",
        file=sys.stderr,
    )
    uvicorn.run(**uvicorn_kwargs)
