"""Connexion a la base operationnelle et gestion des sessions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apps.api.models.base import Base
from platform_core.config import settings


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"echo": settings.sql_echo, "future": True}
    if url.startswith("sqlite"):
        # check_same_thread=False : necessaire pour le pool de threads d'uvicorn.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(url, **kwargs)


engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
    """Active les cles etrangeres sous SQLite (desactivees par defaut)."""
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def init_db() -> None:
    """Cree le schema s'il n'existe pas (usage local et tests).

    En dev et en production, le schema est gere par les scripts DDL versionnes
    dans ``warehouse/ddl`` et les migrations, pas par ``create_all``.
    """
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """Dependance FastAPI : une session par requete."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session transactionnelle pour les scripts et taches de fond."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
