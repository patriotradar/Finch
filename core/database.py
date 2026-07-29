"""Database configuration for durable Aegis storage."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        if os.environ.get("VERCEL_ENV") == "production":
            raise RuntimeError("DATABASE_URL is required in production")
        return "sqlite:///./data/aegis-dev.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def build_engine(url: str | None = None):
    resolved = url or database_url()
    connect_args = {"check_same_thread": False} if resolved.startswith("sqlite") else {}
    options = {"poolclass": StaticPool} if resolved in {"sqlite://", "sqlite:///:memory:"} else {}
    return create_engine(resolved, pool_pre_ping=True, connect_args=connect_args, **options)


def build_session_factory(engine=None):
    return sessionmaker(bind=engine or build_engine(), expire_on_commit=False, class_=Session)


def migrate_to_head() -> None:
    """Apply committed migrations before serving database-backed requests."""
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    engine = build_engine()

    with engine.connect() as connection:
        is_postgres = connection.dialect.name == "postgresql"
        if is_postgres:
            connection.execute(text("SELECT pg_advisory_lock(641347204716)"))
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            if is_postgres:
                connection.execute(text("SELECT pg_advisory_unlock(641347204716)"))


@contextmanager
def session_scope(factory=None):
    session = (factory or build_session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
