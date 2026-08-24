from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,          # keep 5 connections warm and reused
    max_overflow=10,      # allow up to 10 extra under burst load
    pool_timeout=30,      # wait up to 30s for a free connection
    pool_recycle=1800,    # recycle connections every 30 min (avoids stale TCP)
    pool_pre_ping=True,   # test connection before use (handles Neon idle drops)
    connect_args={
        "connect_timeout": 10,
    },
    echo=False,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
