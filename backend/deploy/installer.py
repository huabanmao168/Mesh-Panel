"""部署逻辑：连节点 → 推 agent 二进制 → 喂 install.sh → 解析结果。"""
import io
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paramiko

from config import BASE_DIR
from ssh.client import _load_pkey
from security.crypto import decrypt
from deploy.scripts import INSTALL_SH

DEPLOY_TIMEOUT = 240
LOG_MAX_BYTES = 16 * 1024
RESULT_MARKER = "---MESH-PANEL-RESULT---"

AGENT_DIST_DIR = BASE_DIR / "backend" / "agent_dist"
AGENT_REMOTE_PATH = "/opt/meshPanel/mesh-agent"


@dataclass
class DeployResult:
    ok: bool
    version: Optional[str] = None
    arch: Optional[str] = None
    singbox_status: Optional[str] = None
    agent_status: Optional[str] = None
    log: str = ""
    error: Optional[str] = None


def _schema_from_version(ver: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)", ver or "")
    if not m:
        return "singbox-unknown"
    return f"singbox-{m.group(1)}.{m.group(2)}"


def derive_schema(version: str) -> str:
    return _schema_from_version(version)


def _connect(node) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=node.host,
        port=node.ssh_port,
        username=node.ssh_user,
        timeout=10,
        allow_agent=False,
        look_for_keys=False,
    )
    if node.auth_type == "password":
        kwargs["password"] = decrypt(node.ssh_password)
    elif node.auth_type == "key":
        kwargs["pkey"] = _load_pkey(decrypt(node.ssh_private_key) or "")
    else:
        raise ValueError(f"未知认证方式: {node.auth_type}")
    client.connect(**kwargs)
    return client


def _detect_arch(client: paramiko.SSHClient) -> str:
    _, stdout, _ = client.exec_command("uname -m", timeout=10)
    m = stdout.read().decode().strip()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7"}.get(m, m)


def _upload_agent(client: paramiko.SSHClient, arch: str) -> tuple[bool, bool, str]:
    """SFTP 上传对应架构的 agent 二进制。

    返回 (uploaded_ok, changed, message)
    - uploaded_ok: 二进制就位（已存在且 hash 一致 也算 ok）
    - changed: 这次是否实际写了文件（用于判断是否要重启 agent）
    """
    import hashlib
    local = AGENT_DIST_DIR / f"mesh-agent-{arch}"
    if not local.exists():
        return False, False, f"主控缺少 agent 二进制 mesh-agent-{arch}"

    local_sha = hashlib.sha256(local.read_bytes()).hexdigest()

    # 远端 hash（不存在则返回空）
    _, stdout, _ = client.exec_command(
        f"sha256sum {AGENT_REMOTE_PATH} 2>/dev/null | awk '{{print $1}}'", timeout=10
    )
    remote_sha = stdout.read().decode().strip()

    if remote_sha == local_sha:
        return True, False, "agent 二进制已最新，跳过上传"

    sftp = client.open_sftp()
    try:
        client.exec_command("mkdir -p /opt/meshPanel")[1].read()
        sftp.put(str(local), AGENT_REMOTE_PATH + ".new")
        sftp.chmod(AGENT_REMOTE_PATH + ".new", 0o755)
    finally:
        sftp.close()
    # 原子替换（避免 text file busy）
    client.exec_command(
        f"systemctl stop socks-agent.service 2>/dev/null; "
        f"systemctl stop mesh-agent.service 2>/dev/null; "
        f"mv {AGENT_REMOTE_PATH}.new {AGENT_REMOTE_PATH}"
    )[1].read()
    return True, True, "agent 二进制已更新"


def deploy_node(node, agent_endpoint: str) -> DeployResult:
    """执行整套部署。需要 agent_endpoint 来写 agent.env。"""
    try:
        client = _connect(node)
    except Exception as e:  # noqa: BLE001
        return DeployResult(ok=False, error=f"SSH 连接失败: {type(e).__name__}: {e}")

    try:
        # 先探一下架构，决定推哪个 agent
        try:
            arch = _detect_arch(client)
        except Exception as e:  # noqa: BLE001
            return DeployResult(ok=False, error=f"探测架构失败: {e}")

        # 推 agent（如果有 endpoint 才推，且本地有对应架构二进制）
        agent_uploaded = False
        agent_bin_changed = False
        agent_skip_reason = ""
        if not agent_endpoint:
            agent_skip_reason = "agent_endpoint 未配置，跳过 agent 部署"
        elif arch not in ("amd64", "arm64", "armv7"):
            agent_skip_reason = f"不支持的架构 {arch}"
        else:
            try:
                agent_uploaded, agent_bin_changed, upload_msg = _upload_agent(client, arch)
                if not agent_uploaded:
                    agent_skip_reason = upload_msg
                else:
                    agent_skip_reason = upload_msg  # 即使成功也记一行（"已最新"/"已更新"）
            except Exception as e:  # noqa: BLE001
                agent_skip_reason = f"上传 agent 失败: {e}"

        # 喂 install.sh
        install_mode = "agent_only" if getattr(node, "kind", "landing") in ("soga", "other") else "full"
        env_parts = [
            f"INSTALL_MODE={install_mode}",
            f"AGENT_TOKEN={shlex.quote(node.agent_token or '')}",
            f"AGENT_ENDPOINT={shlex.quote(agent_endpoint or '')}",
            f"AGENT_NODE_ID={node.id}",
            f"AGENT_BIN_CHANGED={'1' if agent_bin_changed else '0'}",
        ]
        cmd = " ".join(env_parts) + " bash -s"

        transport = client.get_transport()
        channel = transport.open_session()
        channel.settimeout(DEPLOY_TIMEOUT)
        channel.exec_command(cmd)
        channel.sendall(INSTALL_SH.encode("utf-8"))
        channel.shutdown_write()

        buf_out = io.BytesIO()
        buf_err = io.BytesIO()
        while True:
            wrote = False
            if channel.recv_ready():
                buf_out.write(channel.recv(65536))
                wrote = True
            if channel.recv_stderr_ready():
                buf_err.write(channel.recv_stderr(65536))
                wrote = True
            if channel.exit_status_ready() and not wrote:
                break

        while channel.recv_ready():
            buf_out.write(channel.recv(65536))
        while channel.recv_stderr_ready():
            buf_err.write(channel.recv_stderr(65536))

        exit_status = channel.recv_exit_status()
        stdout_text = buf_out.getvalue().decode("utf-8", errors="replace")
        stderr_text = buf_err.getvalue().decode("utf-8", errors="replace")

        full_log = stdout_text
        if stderr_text:
            full_log += "\n--- stderr ---\n" + stderr_text
        if agent_skip_reason:
            full_log = f"[agent] {agent_skip_reason}\n\n" + full_log
        if len(full_log) > LOG_MAX_BYTES:
            full_log = "...(truncated)...\n" + full_log[-LOG_MAX_BYTES:]

        if exit_status != 0:
            return DeployResult(ok=False, log=full_log, error=f"install.sh 退出码 {exit_status}")

        result = _extract_result(stdout_text)
        if not result:
            return DeployResult(ok=False, log=full_log, error="未找到结果标记")

        return DeployResult(
            ok=True,
            version=result.get("version"),
            arch=result.get("arch") or arch,
            singbox_status=result.get("singbox_status"),
            agent_status=result.get("agent_status"),
            log=full_log,
        )
    except Exception as e:  # noqa: BLE001
        return DeployResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass


def _extract_result(stdout: str) -> Optional[dict]:
    idx = stdout.rfind(RESULT_MARKER)
    if idx < 0:
        return None
    tail = stdout[idx + len(RESULT_MARKER):].strip()
    for line in tail.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


# ─── 仅推 agent.env（改 endpoint 后批量调用）──────────

AGENT_ENV_REMOTE = "/opt/meshPanel/agent.env"


def push_agent_env(node, agent_endpoint: str) -> tuple[bool, str]:
    """只更新节点上的 agent.env 并重启 agent。"""
    if not node.agent_token:
        return False, "节点未生成 agent_token，请先完整部署一次"
    try:
        client = _connect(node)
    except Exception as e:  # noqa: BLE001
        return False, f"SSH 连接失败: {type(e).__name__}: {e}"
    try:
        content = (
            f"MESH_AGENT_TOKEN={node.agent_token}\n"
            f"MESH_AGENT_ENDPOINT={agent_endpoint}\n"
            f"MESH_AGENT_NODE_ID={node.id}\n"
        )
        cmd = (
            f"mkdir -p /opt/meshPanel && "
            f"cat > {AGENT_ENV_REMOTE} <<'__EOF__'\n{content}__EOF__\n"
            f"chmod 600 {AGENT_ENV_REMOTE} && "
            f"systemctl restart mesh-agent.service"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=30)
        rc = stdout.channel.recv_exit_status()
        err = stderr.read().decode(errors="replace").strip()
        if rc != 0:
            return False, f"退出码 {rc}: {err or '(无错误输出)'}"
        return True, "已推送"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            client.close()
        except Exception:
            pass
