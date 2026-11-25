"""Replay historical revisions in a newly created, isolated project database."""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[1]


def test_frozen_foundation_then_head_replay_and_seed() -> None:
    owner = make_url(get_settings().database_url)
    if not (owner.database or "").endswith("_test"):
        raise RuntimeError("Migration replay requires an explicitly disposable *_test DB.")
    database = f"lf_migration_{uuid4().hex}_test"
    assert re.fullmatch(r"lf_migration_[0-9a-f]{32}_test", database)
    admin_dsn = owner.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    database_url = owner.set(database=database).render_as_string(hide_password=False)
    database_dsn = owner.set(drivername="postgresql", database=database).render_as_string(
        hide_password=False
    )
    created = False

    async def create_database() -> None:
        nonlocal created
        connection = await asyncpg.connect(admin_dsn)
        try:
            assert not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)", database
            )
            await connection.execute(f'CREATE DATABASE "{database}"')
            created = True
        finally:
            await connection.close()

    def run(*arguments: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", *arguments],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": database_url, "SANDBOX_MODE": "true"},
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    async def check_foundation() -> None:
        connection = await asyncpg.connect(database_dsn)
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0001"
            assert await connection.fetchval("SELECT to_regclass('public.postings') IS NOT NULL")
            # Future ORM metadata already includes this constraint; revision
            # 0001 must still create its original schema, not the future model.
            assert not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_ledger_account_policy')"
            )
        finally:
            await connection.close()

    async def check_head() -> None:
        connection = await asyncpg.connect(database_dsn)
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0005"
            constraints = await connection.fetch(
                "SELECT conname FROM pg_constraint WHERE conname = ANY($1::text[])",
                [
                    "ck_ledger_account_policy",
                    "uq_ledger_account_identity",
                    "fk_wallet_account_identity",
                ],
            )
            assert len(constraints) == 3
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'wallet_identity_guard' "
                    "AND NOT tgisinternal"
                )
                == 1
            )
            assert await connection.fetchval("SELECT count(*) FROM users WHERE is_sandbox") == 5
            assert await connection.fetchval("SELECT count(*) FROM journal_entries") == 3
            assert (
                await connection.fetchval("SELECT sum(balance_minor) FROM balance_projections") == 0
            )
            assert not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM balance_projections "
                "WHERE NOT allow_overdraft AND balance_minor < reserved_minor)"
            )
        finally:
            await connection.close()

    async def remove_created_database() -> None:
        # Only this test's successfully created, unpredictable database can be
        # removed. No existing user/project DB is reset, terminated or dropped.
        if not created:
            return
        assert re.fullmatch(r"lf_migration_[0-9a-f]{32}_test", database)
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(f'DROP DATABASE "{database}"')
        finally:
            await connection.close()

    asyncio.run(create_database())
    try:
        run("alembic", "upgrade", "0001")
        asyncio.run(check_foundation())
        run("app.bootstrap")
        asyncio.run(check_head())
        # Bootstrap is repeatable: no duplicate demo funds, users or journals.
        run("app.bootstrap")
        asyncio.run(check_head())
    finally:
        asyncio.run(remove_created_database())
