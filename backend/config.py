"""全局配置：路径、数据库 URL 等。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # /root/mesh-panel
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

SSH_CONNECT_TIMEOUT = 8  # 秒
SSH_EXEC_TIMEOUT = 5
