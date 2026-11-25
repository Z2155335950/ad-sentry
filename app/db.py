"""数据库引擎与会话。SQLite + WAL，单 worker 场景够用；换库只改 DATABASE_URL。"""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


def _prepare_sqlite_dir(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    database = parsed.database
    if database and database != ":memory:":
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_dir(settings.database_url)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    # 导入模型以确保元数据已注册
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
