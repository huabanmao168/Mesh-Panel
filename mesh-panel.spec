# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MeshPanel single-binary build.

build:  pyinstaller mesh-panel.spec
output: dist/mesh-panel  (≈ 80MB onefile)
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# --- 嵌入的资源 ----------------------------------------------------
# (src, dst_inside_bundle)
# dst 路径要跟 backend/config.py 里 RESOURCE_ROOT 下的相对位置对齐:
#   FRONTEND_DIST   = RESOURCE_ROOT / "frontend" / "dist"
#   AGENT_DIST_DIR  = RESOURCE_ROOT / "agent_dist"   (frozen 模式)
datas = [
    ('frontend/dist', 'frontend/dist'),
    ('backend/agent_dist', 'agent_dist'),
]

# --- 动态导入大户:把整个包都搜罗进去 -------------------------------
hidden = []
for pkg in (
    'sqlalchemy',
    'sqlalchemy.dialects.sqlite',
    'sqlmodel',
    'passlib',
    'passlib.handlers.bcrypt',
    'cryptography',
    'bcrypt',
    'jwt',
    'paramiko',
    'uvicorn',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
    'websockets',
    'wsproto',
    'email.mime.multipart',
    'email.mime.text',
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        hidden += h
    except Exception:
        # 某些 pkg 可能没装(passlib 可选),collect_all 会抛
        hidden += collect_submodules(pkg) if pkg else []

# --- Analysis ------------------------------------------------------
a = Analysis(
    ['backend/main.py'],
    pathex=['backend'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL', 'IPython'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='mesh-panel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
