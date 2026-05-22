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
    "agent_endpoint": "",  # 主控公网 WS 地址，例如 ws://1.2.3.4:8000
}
