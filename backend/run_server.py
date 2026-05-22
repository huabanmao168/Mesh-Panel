"""MeshPanel 启动入口。

从 DB settings 读 panel_host/port/tls_* 决定 uvicorn 参数。
被 systemd unit ExecStart 调用,而不是手敲 uvicorn 命令行。

注意:这个脚本在导入 app 之前就读 DB,所以 DB 必须能 init。第一次跑时
DB 不存在没关系——database.init_db() 会自动建表;DEFAULTS 在 api/settings.py
get_setting 里兜底,所以即使 settings 表是空的也能拿到 0.0.0.0:8000。
"""
import sys
from pathlib import Path

# 让 backend/ 下的模块能 import(脚本可能从 /opt/mesh-panel 跑)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import init_db, engine  # noqa: E402
from sqlmodel import Session          # noqa: E402
from api.settings import get_setting  # noqa: E402

init_db()

with Session(engine) as s:
    host = get_setting(s, "panel_host", "0.0.0.0") or "0.0.0.0"
    port_s = get_setting(s, "panel_port", "8000") or "8000"
    tls_enabled = get_setting(s, "tls_enabled", "0") == "1"
    cert_path = get_setting(s, "tls_cert_path", "")
    key_path = get_setting(s, "tls_key_path", "")

try:
    port = int(port_s)
    if not (1 <= port <= 65535):
        raise ValueError
except ValueError:
    print(f"[run_server] invalid panel_port={port_s!r}, fallback to 8000", file=sys.stderr)
    port = 8000

uvicorn_kwargs = {
    "app": "main:app",
    "host": host,
    "port": port,
}

if tls_enabled:
    if cert_path and key_path and Path(cert_path).is_file() and Path(key_path).is_file():
        uvicorn_kwargs["ssl_certfile"] = cert_path
        uvicorn_kwargs["ssl_keyfile"] = key_path
        print(f"[run_server] TLS enabled: cert={cert_path}", file=sys.stderr)
    else:
        print(
            f"[run_server] TLS enabled but cert/key missing (cert={cert_path!r} "
            f"key={key_path!r}), serving plain HTTP",
            file=sys.stderr,
        )

print(
    f"[run_server] starting uvicorn on {host}:{port} "
    f"({'HTTPS' if 'ssl_certfile' in uvicorn_kwargs else 'HTTP'})",
    file=sys.stderr,
)

import uvicorn  # noqa: E402
uvicorn.run(**uvicorn_kwargs)
