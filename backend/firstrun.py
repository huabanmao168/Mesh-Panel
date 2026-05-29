"""首次启动初始化:确保默认管理员账号存在。

策略:settings 表里没有 admin_username 时,生成随机密码 + 写"必须改密"标志。
随机密码同时:
  1) 写入 data/initial_password.txt (0600),仅本地 root/启动用户可读
  2) 打印到 stderr 一次,方便用户从 systemd journal 看
已有任何账号 → 完全不动(包括用户已改密的情况)。
"""
import os
import sys
import secrets
import logging

import bcrypt
from sqlmodel import Session

from database import engine
from config import DATA_DIR
from api.auth import (
    K_USERNAME, K_PWD_HASH, K_MUST_CHANGE,
    _setting_get, _setting_set,
)

log = logging.getLogger(__name__)

DEFAULT_USERNAME = "admin"
_PWD_FILE = DATA_DIR / "initial_password.txt"


def _gen_password(n: int = 16) -> str:
    """url-safe 字符,16 字符约 96 bit 熵,足够。"""
    return secrets.token_urlsafe(n)[:n]


def ensure_default_admin() -> None:
    with Session(engine) as s:
        if _setting_get(s, K_USERNAME):
            return  # 已有管理员,不动

        pwd = _gen_password()
        pwd_hash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        _setting_set(s, K_USERNAME, DEFAULT_USERNAME)
        _setting_set(s, K_PWD_HASH, pwd_hash)
        _setting_set(s, K_MUST_CHANGE, "1")
        s.commit()

    # 落地到文件,方便用户找回(只有本机有访问权的用户能读)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _PWD_FILE.write_text(
            f"username: {DEFAULT_USERNAME}\npassword: {pwd}\n"
            f"(此文件仅供首次登录,登录后请立即在 Web 面板修改密码并删除此文件)\n",
            encoding="utf-8",
        )
        try:
            os.chmod(_PWD_FILE, 0o600)
        except Exception:
            pass
    except Exception as e:
        log.warning("写入 initial_password.txt 失败: %s", e)

    # 打印到 stderr 一次 — systemd journal 可查
    banner = (
        "\n"
        "================================================================\n"
        " MeshPanel 首次启动 — 已生成默认管理员账号\n"
        f"   用户名: {DEFAULT_USERNAME}\n"
        f"   密码:   {pwd}\n"
        f"   (同样保存到 {_PWD_FILE})\n"
        " 登录后请立即修改密码,系统会强制提示。\n"
        "================================================================\n"
    )
    print(banner, file=sys.stderr, flush=True)
