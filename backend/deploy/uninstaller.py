"""节点卸载：SSH 进去停服务、删 unit、清目录。"""
import json
import re
from dataclasses import dataclass
from typing import Optional

from deploy.installer import _connect

RESULT_MARKER = "---MESH-PANEL-RESULT---"
UNINSTALL_TIMEOUT = 60

UNINSTALL_SH = r"""#!/usr/bin/env bash
# 不 set -e —— 任一步失败仍继续清扫
set -u

echo "[1/5] stopping services..."
systemctl stop --no-block sing-box.service mesh-agent.service socks-agent.service 2>/dev/null || true
sleep 1
systemctl stop sing-box.service mesh-agent.service socks-agent.service 2>/dev/null || true

echo "[2/5] disabling services..."
systemctl disable sing-box.service mesh-agent.service socks-agent.service 2>/dev/null || true

echo "[3/5] removing unit files..."
rm -f /etc/systemd/system/sing-box.service
rm -f /etc/systemd/system/mesh-agent.service
rm -f /etc/systemd/system/socks-agent.service
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed sing-box.service mesh-agent.service socks-agent.service 2>/dev/null || true

echo "[4/5] removing directories..."
rm -rf /opt/meshPanel

echo "[5/5] done"

# 检查残留
LEFTOVERS=""
[ -d /opt/meshPanel ] && LEFTOVERS="$LEFTOVERS /opt/meshPanel"
[ -f /etc/systemd/system/sing-box.service ] && LEFTOVERS="$LEFTOVERS sing-box.service"
[ -f /etc/systemd/system/mesh-agent.service ] && LEFTOVERS="$LEFTOVERS mesh-agent.service"
[ -f /etc/systemd/system/socks-agent.service ] && LEFTOVERS="$LEFTOVERS socks-agent.service"

echo "---MESH-PANEL-RESULT---"
if [ -z "$LEFTOVERS" ]; then
  printf '{"ok":true,"leftovers":""}\n'
else
  printf '{"ok":false,"leftovers":"%s"}\n' "$LEFTOVERS"
fi
"""


@dataclass
class UninstallResult:
    ok: bool
    log: str = ""
    error: Optional[str] = None
    leftovers: str = ""


def uninstall_node(node) -> UninstallResult:
    """SSH 进节点，跑卸载脚本，返回结果。"""
    try:
        client = _connect(node)
    except Exception as e:  # noqa: BLE001
        return UninstallResult(ok=False, error=f"SSH 连接失败: {type(e).__name__}: {e}")

    try:
        stdin, stdout, stderr = client.exec_command(
            "bash -s", timeout=UNINSTALL_TIMEOUT, get_pty=False
        )
        stdin.write(UNINSTALL_SH)
        stdin.channel.shutdown_write()

        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        rc = stdout.channel.recv_exit_status()
        log = out + (("\n[stderr]\n" + err) if err.strip() else "")

        # 解析 RESULT_MARKER
        marker_idx = out.rfind(RESULT_MARKER)
        if marker_idx >= 0:
            json_line = out[marker_idx + len(RESULT_MARKER):].strip().splitlines()
            json_line = json_line[0] if json_line else "{}"
            try:
                meta = json.loads(json_line)
            except Exception:
                meta = {}
            ok = bool(meta.get("ok"))
            leftovers = meta.get("leftovers", "") or ""
            return UninstallResult(
                ok=ok,
                log=log,
                error=None if ok else f"残留: {leftovers.strip()}",
                leftovers=leftovers.strip(),
            )

        return UninstallResult(
            ok=False,
            log=log,
            error=f"未找到结果标记 (rc={rc})",
        )
    except Exception as e:  # noqa: BLE001
        return UninstallResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass
