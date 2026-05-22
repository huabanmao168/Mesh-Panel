"""Paramiko 封装：一次性 SSH 连接 + 命令执行。"""
import io
from typing import Optional, Tuple

import paramiko

from config import SSH_CONNECT_TIMEOUT, SSH_EXEC_TIMEOUT


def _load_pkey(private_key: str) -> paramiko.PKey:
    """尝试按 RSA / Ed25519 / ECDSA / DSS 顺序解析私钥。"""
    errors = []
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(private_key))
        except paramiko.SSHException as e:
            errors.append(f"{cls.__name__}: {e}")
    raise paramiko.SSHException("无法解析私钥；尝试过 " + "; ".join(errors))


def test_connection(
    host: str,
    port: int,
    user: str,
    auth_type: str,
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    timeout: int = SSH_CONNECT_TIMEOUT,
) -> Tuple[bool, str]:
    """连一次 SSH 并跑 `uname -a`。

    返回 (success, message)：
      - success=True 时 message 是 uname 输出
      - success=False 时 message 是错误描述
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if auth_type == "password":
            if not password:
                return False, "认证方式为 password 但未提供密码"
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        elif auth_type == "key":
            if not private_key:
                return False, "认证方式为 key 但未提供私钥"
            pkey = _load_pkey(private_key)
            client.connect(
                hostname=host,
                port=port,
                username=user,
                pkey=pkey,
                timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        else:
            return False, f"未知认证方式: {auth_type}"

        _, stdout, stderr = client.exec_command("uname -a", timeout=SSH_EXEC_TIMEOUT)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        if not out and err:
            return False, f"命令出错: {err}"
        return True, out or "(空输出)"
    except paramiko.AuthenticationException as e:
        return False, f"认证失败: {e}"
    except paramiko.SSHException as e:
        return False, f"SSH 错误: {e}"
    except OSError as e:
        # 包含 socket 超时、拒绝连接、DNS 失败
        return False, f"网络错误: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"未知错误: {type(e).__name__}: {e}"
    finally:
        try:
            client.close()
        except Exception:
            pass
