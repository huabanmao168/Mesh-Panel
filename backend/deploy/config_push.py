"""SS 配置推送到节点：通过 agent RPC 写文件 + sing-box check 校验 + 原子替换。

agent 离线直接返回 PushResult(ok=False)。
"""
import json
from dataclasses import dataclass

from deploy import remote

REMOTE_CONFIG = "/opt/meshPanel/config.json"
REMOTE_CONFIG_NEW = "/opt/meshPanel/config.json.new"


@dataclass
class PushResult:
    ok: bool
    error: str = ""
    check_output: str = ""


def push_config(node, singbox_json: dict) -> PushResult:
    """推配置到节点：写临时文件 → sing-box check → atomic mv。"""
    if not remote.is_online(node.id):
        return PushResult(ok=False, error="agent 离线")

    try:
        body = json.dumps(singbox_json, indent=2, ensure_ascii=False)

        # 1) 确保目录存在
        try:
            remote.remote_exec(node, "mkdir -p /opt/meshPanel", timeout=10)
        except remote.RemoteError as e:
            return PushResult(ok=False, error=f"mkdir 失败: {e}")

        # 2) 写新配置
        try:
            remote.remote_write(node, REMOTE_CONFIG_NEW, body, mode=0o644)
        except remote.RemoteError as e:
            return PushResult(ok=False, error=f"写入临时配置失败: {e}")

        # 3) dry-run 校验
        try:
            r = remote.remote_exec(
                node,
                f"/opt/meshPanel/sing-box check -c {REMOTE_CONFIG_NEW}",
                timeout=15,
            )
        except remote.RemoteError as e:
            return PushResult(ok=False, error=f"check 调用失败: {e}")

        rc = r.get("rc", -1)
        check_out = (r.get("stdout", "") or "") + (r.get("stderr", "") or "")
        if rc != 0:
            # 清掉临时文件
            try:
                remote.remote_exec(node, f"rm -f {REMOTE_CONFIG_NEW}", timeout=10)
            except remote.RemoteError:
                pass
            return PushResult(ok=False, error=f"sing-box check 失败 (rc={rc})", check_output=check_out)

        # 4) 原子替换
        try:
            r = remote.remote_exec(node, f"mv {REMOTE_CONFIG_NEW} {REMOTE_CONFIG}", timeout=10)
        except remote.RemoteError as e:
            return PushResult(ok=False, error=f"mv 调用失败: {e}")
        rc = r.get("rc", -1)
        if rc != 0:
            return PushResult(
                ok=False,
                error=f"mv 失败 (rc={rc}): {r.get('stderr', '')}",
            )

        return PushResult(ok=True, check_output=check_out)
    except Exception as e:  # noqa: BLE001
        return PushResult(ok=False, error=f"{type(e).__name__}: {e}")
