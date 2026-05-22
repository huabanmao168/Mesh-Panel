"""Node 表：节点的 SSH 连接信息 + 最近一次连通性状态。"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class NodeBase(SQLModel):
    name: str = Field(index=True, description="节点别名")
    host: str = Field(description="IP 或域名")
    ssh_port: int = Field(default=22)
    ssh_user: str = Field(default="root")
    auth_type: str = Field(default="password", description="password / key")
    ssh_password: Optional[str] = Field(default=None, description="本块明文，第 2 块加密")
    ssh_private_key: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None, description="ISO2 国家码小写，如 hk / us / jp，用于国旗显示")


class Node(NodeBase, table=True):
    __tablename__ = "nodes"

    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default="unknown", description="unknown / reachable / unreachable")
    last_check_at: Optional[datetime] = Field(default=None)
    last_error: Optional[str] = Field(default=None)
    last_uname: Optional[str] = Field(default=None, description="测连接时 uname -a 输出")

    # 部署相关（第 2 块）
    arch: Optional[str] = Field(default=None, description="节点架构 amd64 / arm64 / armv7")
    singbox_version: Optional[str] = Field(default=None, description="节点上实际安装的版本，例如 1.10.7")
    config_schema: Optional[str] = Field(
        default=None,
        description="配置 schema 标记，从 major.minor 推导，例如 singbox-1.10",
    )
    deploy_status: str = Field(
        default="not_deployed",
        description="not_deployed / deploying / deployed / failed",
    )
    deploy_log: Optional[str] = Field(default=None, description="最后一次部署日志（截断到 16KB）")
    deployed_at: Optional[datetime] = Field(default=None)

    # Agent 相关（第 3 块）
    agent_token: Optional[str] = Field(default=None, description="WS 鉴权 token，uuid4")
    agent_status: str = Field(default="offline", description="online / offline")
    agent_last_seen: Optional[datetime] = Field(default=None)
    agent_version: Optional[str] = Field(default=None, description="agent 上报的自己版本")
    agent_iface: Optional[str] = Field(
        default=None,
        description="探针网卡名，留空 agent 自动探测默认路由网卡",
    )

    # SS 配置（第 4 块）
    ss_config: Optional[str] = Field(default=None, description="SS 配置 JSON 字符串")
    ss_apply_status: str = Field(default="never", description="never / applied / failed")
    ss_applied_at: Optional[datetime] = Field(default=None)
    ss_apply_error: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── 请求 / 响应模型 ──────────────────────────────────────

class NodeCreate(NodeBase):
    pass


class NodeUpdate(SQLModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    auth_type: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    agent_iface: Optional[str] = None
    country: Optional[str] = None


class NodeRead(SQLModel):
    """对外返回，永远不带凭据字段。"""
    id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    auth_type: str
    status: str
    last_check_at: Optional[datetime]
    last_error: Optional[str]
    last_uname: Optional[str]
    arch: Optional[str]
    singbox_version: Optional[str]
    config_schema: Optional[str]
    deploy_status: str
    deployed_at: Optional[datetime]
    agent_status: str
    agent_last_seen: Optional[datetime]
    agent_version: Optional[str]
    agent_iface: Optional[str]
    country: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_node(cls, n: "Node") -> "NodeRead":
        return cls(
            id=n.id,
            name=n.name,
            host=n.host,
            ssh_port=n.ssh_port,
            ssh_user=n.ssh_user,
            auth_type=n.auth_type,
            status=n.status,
            last_check_at=n.last_check_at,
            last_error=n.last_error,
            last_uname=n.last_uname,
            arch=n.arch,
            singbox_version=n.singbox_version,
            config_schema=n.config_schema,
            deploy_status=n.deploy_status,
            deployed_at=n.deployed_at,
            agent_status=n.agent_status,
            agent_last_seen=n.agent_last_seen,
            agent_version=n.agent_version,
            agent_iface=n.agent_iface,
            country=n.country,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )
