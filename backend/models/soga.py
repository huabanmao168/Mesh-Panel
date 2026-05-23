"""SoGa 路由管理 — 三表关联。

soga_instances    入口机上的每个 /etc/soga/<folder>/ 一行
soga_routes       一个实例下的路由列表 (顺序由 position 决定)
soga_route_outs   一条路由的出站(落地节点)池
"""
from typing import Optional
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


def _now():
    return datetime.now(timezone.utc)


class SogaInstance(SQLModel, table=True):
    __tablename__ = "soga_instances"
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(index=True, foreign_key="nodes.id")
    folder_name: str = Field(description="如 ABC-HK")
    display_name: Optional[str] = Field(default=None, description="可选别名")
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SogaRoute(SQLModel, table=True):
    __tablename__ = "soga_routes"
    id: Optional[int] = Field(default=None, primary_key=True)
    instance_id: int = Field(index=True, foreign_key="soga_instances.id")
    position: int = Field(default=0, description="排序")
    rules: list = Field(default_factory=list, sa_column=Column(JSON))
    balance: Optional[str] = Field(default=None, description="random/round_robin/ip_hash/null")
    is_system: bool = Field(default=False, description="系统探活路由,固定不可编辑")
    is_fallback: bool = Field(default=False, description="rules=['*'] 兜底路由")
    remark: Optional[str] = Field(default=None)


class SogaRouteOut(SQLModel, table=True):
    __tablename__ = "soga_route_outs"
    id: Optional[int] = Field(default=None, primary_key=True)
    route_id: int = Field(index=True, foreign_key="soga_routes.id")
    position: int = Field(default=0)
    landing_node_id: int = Field(foreign_key="nodes.id", description="关联的落地节点")
