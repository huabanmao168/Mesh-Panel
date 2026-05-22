"""GeoIP 国家码查询：ip-api.com 免费接口，无 token。

返回 ISO2 小写国家码，如 hk / us / jp。失败返回 None。
"""
from __future__ import annotations
import json
import logging
import socket
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

# 跳过私网/特殊地址段
_PRIVATE_PREFIXES = ("10.", "127.", "192.168.", "169.254.", "0.")


def _resolve(host: str) -> Optional[str]:
    """域名 → IP；裸 IP 直接返回。"""
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


def lookup_country(host: str, timeout: float = 4.0) -> Optional[str]:
    """查 host 所属国家 ISO2 码（小写）。失败返回 None。"""
    if not host:
        return None
    ip = _resolve(host)
    if not ip:
        return None
    if ip.startswith(_PRIVATE_PREFIXES) or ip.startswith("172."):
        return None
    try:
        req = urllib.request.Request(
            f"http://ip-api.com/json/{ip}?fields=status,countryCode",
            headers={"User-Agent": "MeshPanel/0.0.3"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") == "success":
            code = (data.get("countryCode") or "").lower()
            return code or None
    except Exception as exc:
        log.warning("geoip lookup failed host=%s ip=%s: %s", host, ip, exc)
    return None
