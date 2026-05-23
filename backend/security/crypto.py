"""Fernet 对称加密 — SSH 凭据落库前加密。

设计:
- 密钥文件 data/secret.key (0600),首次启动自动生成。
- 密文前缀 "enc:v1:" + base64(fernet)。
- 解密遇到无前缀 = 旧明文,直接返回(向后兼容,迁移期用)。
- 启动后跑一次 migrate_plaintext_credentials() 把存量明文升级成密文。

约定:
- encrypt(s) — None/空串透传不加密(避免污染 NULL/空字符串语义)。
- decrypt(s) — None/空/无前缀都按"原样字符串"返回。
"""
import os
import logging
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"

# data/ 目录由 database.py 创建,这里复用约定
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_KEY_PATH = _DATA_DIR / "secret.key"

_fernet: Optional[Fernet] = None


def _load_or_create_key() -> bytes:
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes().strip()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    try:
        os.chmod(_KEY_PATH, 0o600)
    except Exception:
        pass
    log.warning("生成新密钥: %s — 请妥善备份此文件,丢失后所有 SSH 凭据无法解密!", _KEY_PATH)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plain: Optional[str]) -> Optional[str]:
    """加密;None/空字符串原样返回。"""
    if plain is None or plain == "":
        return plain
    if plain.startswith(PREFIX):
        # 已经是密文,幂等
        return plain
    token = _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(stored: Optional[str]) -> Optional[str]:
    """解密;None/空字符串/无前缀(旧明文)按原样返回。"""
    if stored is None or stored == "":
        return stored
    if not stored.startswith(PREFIX):
        # 旧明文,迁移期兼容
        return stored
    token = stored[len(PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("Fernet 解密失败 — 密钥不匹配或数据损坏")
        return None


def is_encrypted(stored: Optional[str]) -> bool:
    return bool(stored) and stored.startswith(PREFIX)


def migrate_plaintext_credentials() -> int:
    """启动时调用一次:把 nodes 表里所有未加密的 ssh_password / ssh_private_key 升级成密文。

    返回升级条数。
    """
    from sqlmodel import Session, select
    from database import engine
    from models.node import Node

    count = 0
    with Session(engine) as s:
        nodes = s.exec(select(Node)).all()
        for n in nodes:
            changed = False
            if n.ssh_password and not is_encrypted(n.ssh_password):
                n.ssh_password = encrypt(n.ssh_password)
                changed = True
            if n.ssh_private_key and not is_encrypted(n.ssh_private_key):
                n.ssh_private_key = encrypt(n.ssh_private_key)
                changed = True
            if changed:
                s.add(n)
                count += 1
        if count:
            s.commit()
    if count:
        log.info("已加密 %d 个节点的 SSH 凭据", count)
    return count
