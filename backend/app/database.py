"""SQLAlchemy engine and session helpers for the application database."""

from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Parent class that collects every ORM model into one table registry."""


# SQLite is used by request-handling threads in development, so this option
# prevents SQLite's single-thread connection guard from rejecting valid work.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield one database session per request and always close it afterward."""
    database_session = SessionLocal()
    try:
        yield database_session
    finally:
        database_session.close()

