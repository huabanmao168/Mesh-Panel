"""SQLModel engine + session 工厂。"""
from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL

# SQLite 多线程：FastAPI 的 threadpool 会跨线程用 session
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    # 触发所有 model 的注册
    from models.node import Node  # noqa: F401
    from models.setting import Setting, DEFAULTS  # noqa: F401
    SQLModel.metadata.create_all(engine)
    # 填充默认设置项
    with Session(engine) as s:
        for k, v in DEFAULTS.items():
            if s.get(Setting, k) is None:
                s.add(Setting(key=k, value=v))
        s.commit()


def get_session():
    with Session(engine) as session:
        yield session
