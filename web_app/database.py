"""Database configuration and session management."""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models.portals import Base

# SQLite en local, PostgreSQL en producción
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./broswer_portals.db")

# Railway entrega URLs con prefijo "postgres://" — SQLAlchemy 2.x requiere "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Crea todas las tablas."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Dependency para inyectar DB en endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_db_async() -> Session:
    """Async version de get_db."""
    db = SessionLocal()
    try:
        return db
    finally:
        pass  # Se cierra cuando se retorna
