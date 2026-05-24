"""首次启动初始化:确保默认管理员账号存在。

策略:settings 表里没有 admin_username 时,写入 admin / admin123456。
已有任何账号 → 完全不动(包括用户已改密的情况)。
不打印密码、不写文件。
"""
import bcrypt
from sqlmodel import Session

from database import engine
from api.auth import K_USERNAME, K_PWD_HASH, _setting_get, _setting_set


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123456"


def ensure_default_admin() -> None:
    with Session(engine) as s:
        if _setting_get(s, K_USERNAME):
            return  # 已有管理员,不动
        pwd_hash = bcrypt.hashpw(
            DEFAULT_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        _setting_set(s, K_USERNAME, DEFAULT_USERNAME)
        _setting_set(s, K_PWD_HASH, pwd_hash)
        s.commit()
