"""Settings 表：key/value 全局配置。"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = Field(default="")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# 默认设置项及缺省值
DEFAULTS: dict[str, str] = {
    # agent 回连地址（节点 agent 用此地址连主控 WS）
    "agent_endpoint": "",            # 例：ws://1.2.3.4:8000 或 wss://panel.example.com

    # 面板自身监听
    "panel_host": "0.0.0.0",          # 监听 IP
    "panel_port": "8000",             # 监听端口，1-65535
    "panel_domain": "",               # 强制 Host 校验。非空时只允许此域名访问面板（agent WS 豁免）

    # TLS（启用后 panel 由 uvicorn 直接 serve HTTPS）
    "tls_enabled": "0",               # "1" 启用
    "tls_cert_path": "",              # 证书 fullchain 文件绝对路径
    "tls_key_path": "",              # 私钥文件绝对路径

    # 面板对外公网地址(给 soga -routes_url 拼链接用)。空 = 未配置,UI 提示先填
    # 例: https://panel.example.com 或 http://1.2.3.4:8000 (末尾不带 /)
    "panel_public_url": "",
}
