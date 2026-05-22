"""SS 配置推送到节点：SFTP 传 + sing-box check 校验 + 原子替换。"""
import io
import json
from dataclasses import dataclass

import paramiko

from deploy.installer import _connect

REMOTE_CONFIG = "/opt/meshPanel/config.json"
REMOTE_CONFIG_NEW = "/opt/meshPanel/config.json.new"


@dataclass
class PushResult:
    ok: bool
    error: str = ""
    check_output: str = ""


def push_config(node, singbox_json: dict) -> PushResult:
    """推配置到节点：SFTP → dry-run → atomic mv。"""
    try:
        client = _connect(node)
    except Exception as e:  # noqa: BLE001
        return PushResult(ok=False, error=f"SSH 连接失败: {type(e).__name__}: {e}")

    try:
        body = json.dumps(singbox_json, indent=2, ensure_ascii=False).encode("utf-8")
        sftp = client.open_sftp()
        try:
            client.exec_command("mkdir -p /opt/meshPanel")[1].read()
            with sftp.file(REMOTE_CONFIG_NEW, "wb") as f:
                f.write(body)
            sftp.chmod(REMOTE_CONFIG_NEW, 0o644)
        finally:
            sftp.close()

        # dry-run 校验
        _, stdout, stderr = client.exec_command(
            f"/opt/meshPanel/sing-box check -c {REMOTE_CONFIG_NEW}", timeout=15
        )
        rc = stdout.channel.recv_exit_status()
        check_out = stdout.read().decode(errors="replace") + stderr.read().decode(errors="replace")
        if rc != 0:
            # 不替换旧配置，清掉临时文件
            client.exec_command(f"rm -f {REMOTE_CONFIG_NEW}")[1].read()
            return PushResult(ok=False, error=f"sing-box check 失败 (rc={rc})", check_output=check_out)

        # 原子替换
        _, stdout, stderr = client.exec_command(
            f"mv {REMOTE_CONFIG_NEW} {REMOTE_CONFIG}", timeout=10
        )
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            return PushResult(ok=False, error=f"mv 失败 (rc={rc}): {stderr.read().decode(errors='replace')}")

        return PushResult(ok=True, check_output=check_out)
    except Exception as e:  # noqa: BLE001
        return PushResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass
