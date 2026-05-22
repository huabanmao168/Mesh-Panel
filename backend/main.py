"""FastAPI 入口。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR
from database import init_db
from api.nodes import router as nodes_router
from api.settings import router as settings_router
from api.ss_config import router as ss_router
from api.auth import router as auth_router, is_whitelisted, check_auth
from ws.agents import router as ws_router, sweep_offline_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
                return JSONResponse(
                    {"ok": False, "error": f"此面板仅允许通过域名 {domain} 访问"},
                    status_code=403,
                )
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
    return {"ok": True, "data": {"service": "MeshPanel", "version": "0.0.2"}}


app.include_router(auth_router)
app.include_router(nodes_router)
app.include_router(settings_router)
app.include_router(ss_router)
app.include_router(ws_router)


# --- 前端静态文件服务（生产模式）---------------------------------
# 开发时前端跑 vite :5173,这段不会命中(因为浏览器直连 5173)。
# 生产时 install.sh 会把 npm run build 产物放到 frontend/dist/,
# 由 FastAPI 同端口 serve,前端和 API/WS 同源,agent 走同一端口回连。
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
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
