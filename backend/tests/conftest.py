import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://ledgerflow:ledgerflow@localhost:55432/ledgerflow_test"
)

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


@pytest.fixture(scope="session")
def migrated_database() -> None:
    from app.core.config import get_settings

    database = make_url(get_settings().database_url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("Integration tests require an explicitly disposable *_test database.")
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    from app.bootstrap import seed

    asyncio.run(seed())


@pytest_asyncio.fixture
async def client(migrated_database: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as instance:
        yield instance
