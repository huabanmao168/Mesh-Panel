"""全局配置:路径、数据库 URL 等。

路径模型:
- RESOURCE_ROOT  只读资源根(前端 dist、agent 二进制)
                 源码模式 = 项目根; PyInstaller 模式 = sys._MEIPASS
- USER_HOME      可写数据根(sqlite、密钥、证书)
                 默认 /opt/mesh-panel; 可被 $MESH_PANEL_HOME 覆盖
"""
import os
import sys
import shutil
from pathlib import Path


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onefile:_MEIPASS 是临时解压目录
        return Path(getattr(sys, "_MEIPASS"))
    # 源码模式:此文件 backend/config.py → 项目根
    return Path(__file__).resolve().parent.parent


def _user_home() -> Path:
    env = os.environ.get("MESH_PANEL_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # 默认 /opt/mesh-panel,非 root 写不了 → 降级到 ~/.mesh-panel
    default = Path("/opt/mesh-panel")
    if os.geteuid() == 0:
        return default
    try:
        default.mkdir(parents=True, exist_ok=True)
        # 试探可写
        probe = default / ".write_probe"
        probe.touch()
        probe.unlink()
        return default
    except (PermissionError, OSError):
        return Path.home() / ".mesh-panel"


RESOURCE_ROOT = _resource_root()
BASE_DIR = RESOURCE_ROOT  # 兼容历史引用(只读资源)

USER_HOME = _user_home()
DATA_DIR = USER_HOME / "data"

# --- 老数据迁移:首次启动若新目录无 data 但 /root/mesh-panel/data 有 → 整体 cp 过来
# 这是给开发机/老用户原地升级的兜底,非 root 用户访问 /root/ 会 PermissionError,
# 必须 try 住,失败时降级到空目录(全新部署正常流程)。
_LEGACY_DATA = Path("/root/mesh-panel/data")
if not DATA_DIR.exists():
    try:
        legacy_exists = _LEGACY_DATA.is_dir()
    except (PermissionError, OSError):
        legacy_exists = False
    if legacy_exists:
        USER_HOME.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(_LEGACY_DATA, DATA_DIR)
        except Exception:
            DATA_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# --- 只读资源路径 ---------------------------------------------------
FRONTEND_DIST = RESOURCE_ROOT / "frontend" / "dist"

# agent 二进制:源码模式在 backend/agent_dist/,打包模式 spec 会放到 _MEIPASS/agent_dist/
if getattr(sys, "frozen", False):
    AGENT_DIST_DIR = RESOURCE_ROOT / "agent_dist"
else:
    AGENT_DIST_DIR = RESOURCE_ROOT / "backend" / "agent_dist"

SSH_CONNECT_TIMEOUT = 8  # 秒
SSH_EXEC_TIMEOUT = 5
