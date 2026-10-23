from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    if settings.db_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
