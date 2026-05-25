"""SQLModel engine + session 工厂。"""
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text, event
from config import DATABASE_URL

# SQLite 多线程:FastAPI 的 threadpool 会跨线程用 session
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


# SQLite 默认 foreign_keys=OFF,SQLModel 的 foreign_key= 声明形同虚设
# 每次新连接打开都强制开启 + WAL 模式提升并发写性能 + busy_timeout 避免 database is locked
@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """SQLite 兼容的 ALTER TABLE ADD COLUMN IF NOT EXISTS。"""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    cols = {r[1] for r in rows}
    if column not in cols:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    # 触发所有 model 的注册
    from models.node import Node  # noqa: F401
    from models.setting import Setting, DEFAULTS  # noqa: F401
    from models.soga import SogaInstance, SogaRoute, SogaRouteOut  # noqa: F401
    SQLModel.metadata.create_all(engine)
    # 增量字段(老库自动补列)
    with engine.begin() as conn:
        _ensure_column(conn, "nodes", "kind", "kind TEXT NOT NULL DEFAULT 'landing'")
        _ensure_column(conn, "nodes", "tags", "tags TEXT")
        _ensure_column(conn, "nodes", "soga_system_probe", "soga_system_probe INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "nodes", "soga_system_probe_rules", "soga_system_probe_rules TEXT")
        _ensure_column(conn, "nodes", "soga_last_scanned_at", "soga_last_scanned_at TEXT")
        _ensure_column(conn, "nodes", "sort_order", "sort_order INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "nodes", "soga_version", "soga_version TEXT")
        _ensure_column(conn, "nodes", "os_pretty", "os_pretty TEXT")
        _ensure_column(conn, "soga_instances", "sort_order", "sort_order INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "soga_instances", "route_source", "route_source TEXT NOT NULL DEFAULT 'file'")
        _ensure_column(conn, "soga_instances", "routes_token", "routes_token TEXT")
    # 填充默认设置项
    with Session(engine) as s:
        for k, v in DEFAULTS.items():
            if s.get(Setting, k) is None:
                s.add(Setting(key=k, value=v))
        s.commit()


def get_session():
    with Session(engine) as session:
        yield session
