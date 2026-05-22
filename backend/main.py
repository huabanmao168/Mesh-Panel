"""FastAPI 入口。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
async def auth_middleware(request: Request, call_next):
    """所有 /api/* 路径必须带有效 cookie，白名单除外。WS 不走这里。"""
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
    return {"ok": True, "data": {"service": "MeshPanel", "version": "0.0.1"}}


app.include_router(auth_router)
app.include_router(nodes_router)
app.include_router(settings_router)
app.include_router(ss_router)
app.include_router(ws_router)
