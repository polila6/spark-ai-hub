"""Database engine and session helpers (SQLAlchemy 2.x, sync, psycopg 3)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.effective_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


SCHEDULER_LOCK_ID = 74_210_001


@contextmanager
def advisory_lock(db: Session, lock_id: int = SCHEDULER_LOCK_ID) -> Iterator[None]:
    """Transaction-scoped Postgres advisory lock used to serialise scheduling decisions."""
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
    yield
