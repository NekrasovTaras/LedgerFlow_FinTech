from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

engine = create_async_engine(
    get_settings().database_url,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    connect_args={
        "server_settings": {"application_name": "ledgerflow", "statement_timeout": "15000"}
    },
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
