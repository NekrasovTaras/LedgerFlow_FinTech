"""Exercise real runtime privileges using a role owned only by this test module.

Only disposable *_test databases are accepted. Valid synthetic financial history
is retained; cleanup removes the random role and its grants, never ledger rows.
"""

import importlib.util
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.database import engine as owner_engine
from app.infrastructure.models import Posting, User, Wallet
from app.modules.ledger.application import (
    create_wallet,
    finish_hold,
    reserve,
    topup,
    wallet_view,
)
from app.modules.payments.application import create_transfer

pytestmark = pytest.mark.integration
ROLE_HELPER = Path(__file__).resolve().parents[2] / "infrastructure/postgres/runtime_role.py"


@dataclass
class RestrictedDatabase:
    name: str
    engine: AsyncEngine
    actor: User
    recipient: User
    source: Wallet
    target: Wallet
    journal_id: str
    posting_id: str


@pytest_asyncio.fixture(scope="module")
async def restricted_database(migrated_database: None) -> AsyncIterator[RestrictedDatabase]:
    owner_url = make_url(get_settings().database_url)
    if not (owner_url.database or "").endswith("_test"):
        raise RuntimeError("Runtime-role probes require an explicitly disposable *_test DB.")
    role = f"lf_test_{uuid4().hex}"
    password = uuid4().hex
    assert re.fullmatch(r"lf_test_[0-9a-f]{32}", role)
    specification = importlib.util.spec_from_file_location(f"runtime_role_{role}", ROLE_HELPER)
    assert specification and specification.loader
    helper = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(helper)
    helper.ROLE = role  # Never reprovision or change the real application's password.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", owner_url.render_as_string(hide_password=False))
        patch.setenv("RUNTIME_DB_PASSWORD", password)
        await helper.provision()
    runtime_engine = create_async_engine(
        owner_url.set(username=role, password=password), hide_parameters=True
    )
    sessions = async_sessionmaker(runtime_engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            actor = User(
                email=f"{role}@example.test",
                display_name="Runtime role fixture",
                password_hash="not-a-login-account",
                role="customer",
                active=True,
                is_sandbox=True,
            )
            recipient = User(
                email=f"{role}-recipient@example.test",
                display_name="Runtime role recipient",
                password_hash="not-a-login-account",
                role="customer",
                active=True,
                is_sandbox=True,
            )
            session.add_all([actor, recipient])
            await session.flush()
            source = await create_wallet(session, actor.id, "Permission source")
            target = await create_wallet(session, recipient.id, "Permission destination")
            funding = await topup(session, actor, source.id, 100_000)
            posting_id = await session.scalar(
                select(Posting.id).where(Posting.journal_id == funding["journal_id"])
            )
            assert posting_id is not None
        yield RestrictedDatabase(
            role,
            runtime_engine,
            actor,
            recipient,
            source,
            target,
            funding["journal_id"],
            posting_id,
        )
    finally:
        await runtime_engine.dispose()
        async with owner_engine.begin() as connection:
            # DROP OWNED is used only to revoke this role's grants. Refuse cleanup
            # if any object ownership exists; never delete schema or financial data.
            owns_objects = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_shdepend "
                    "WHERE refclassid = 'pg_authid'::regclass AND deptype = 'o' "
                    "AND refobjid = (SELECT oid FROM pg_roles WHERE rolname = :role))"
                ),
                {"role": role},
            )
            assert not owns_objects, "Test role unexpectedly owns objects; refusing DROP OWNED."
            await connection.exec_driver_sql(f"DROP OWNED BY {role}")
            await connection.exec_driver_sql(f"DROP ROLE {role}")


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None) or getattr(
        getattr(error.orig, "__cause__", None), "sqlstate", None
    )


async def assert_rejected(
    database: RestrictedDatabase, statement: str, parameters: dict[str, str], code: str
) -> None:
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as failure:
                await connection.execute(text(statement), parameters)
            assert sqlstate(failure.value) == code
        finally:
            # Even an unexpectedly successful permission probe must never commit.
            await transaction.rollback()


async def test_actual_login_role_has_only_required_privileges(restricted_database) -> None:
    database = restricted_database
    async with database.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT current_user AS name, rolsuper, rolcreatedb, rolcreaterole, "
                        "rolinherit, rolreplication, rolbypassrls, "
                        "has_database_privilege(current_user, current_database(), 'TEMP') AS temp, "
                        "has_database_privilege(current_user, current_database(), 'CREATE') "
                        "AS database_create, "
                        "has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create, "
                        "has_table_privilege(current_user, 'postings', 'INSERT') "
                        "AS posting_insert, "
                        "has_table_privilege(current_user, 'postings', 'UPDATE') "
                        "AS posting_update, "
                        "has_table_privilege(current_user, 'postings', 'DELETE') "
                        "AS posting_delete, "
                        "has_table_privilege(current_user, 'journal_entries', 'TRUNCATE') "
                        "AS truncate, "
                        "has_column_privilege(current_user, 'balance_projections', "
                        "'balance_minor', 'UPDATE') AS balance_update, "
                        "has_column_privilege(current_user, 'balance_projections', "
                        "'allow_overdraft', 'UPDATE') AS policy_update, "
                        "has_column_privilege(current_user, 'holds', 'status', 'UPDATE') "
                        "AS hold_close, "
                        "has_column_privilege(current_user, 'holds', 'amount_minor', 'UPDATE') "
                        "AS hold_rewrite FROM pg_roles WHERE rolname = current_user"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["name"] == database.name
    enabled = {name for name, value in row.items() if value is True}
    assert enabled == {"posting_insert", "balance_update", "hold_close"}


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TEMP TABLE permission_probe (value integer)",
        "CREATE TABLE public.permission_probe (value integer)",
        "CREATE SCHEMA permission_probe",
        "CREATE FUNCTION public.permission_probe() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RETURN NEW; END $$",
        "CREATE TRIGGER permission_probe BEFORE UPDATE ON public.wallets "
        "FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation()",
        "ALTER TABLE public.postings DISABLE TRIGGER USER",
        "SET LOCAL session_replication_role = replica",
        "SET LOCAL ROLE ledgerflow",
        "UPDATE journal_entries SET description = description WHERE id = CAST(:journal AS uuid)",
        "UPDATE postings SET amount_minor = amount_minor WHERE id = CAST(:posting AS uuid)",
        "DELETE FROM postings WHERE id = CAST(:posting AS uuid)",
        "TRUNCATE journal_entries",
        "TRUNCATE payments",
        "UPDATE balance_projections SET allow_overdraft = true "
        "WHERE account_id = CAST(:account AS uuid)",
        "UPDATE holds SET amount_minor = amount_minor WHERE false",
    ],
)
async def test_ddl_history_and_policy_changes_are_forbidden(
    restricted_database, statement: str
) -> None:
    database = restricted_database
    await assert_rejected(
        database,
        statement,
        {
            "journal": database.journal_id,
            "posting": database.posting_id,
            "account": database.source.account_id,
        },
        "42501",
    )


@pytest.mark.parametrize("column", ["balance_minor", "reserved_minor"])
async def test_direct_projection_edit_is_rejected_by_trigger(restricted_database, column) -> None:
    database = restricted_database
    await assert_rejected(
        database,
        f"UPDATE balance_projections SET {column} = {column} + 1 "
        "WHERE account_id = CAST(:account AS uuid)",
        {"account": database.source.account_id},
        "23514",
    )


async def test_balanced_late_append_is_rejected_under_runtime_role(restricted_database) -> None:
    database = restricted_database
    await assert_rejected(
        database,
        "INSERT INTO postings (id, journal_id, account_id, side, amount_minor) "
        "VALUES (gen_random_uuid(), CAST(:journal AS uuid), "
        "CAST(:target AS uuid), 'DEBIT', 1), "
        "(gen_random_uuid(), CAST(:journal AS uuid), "
        "CAST(:source AS uuid), 'CREDIT', 1)",
        {
            "journal": database.journal_id,
            "source": database.source.account_id,
            "target": database.target.account_id,
        },
        "23514",
    )


async def test_user_wallet_account_cannot_enable_overdraft(restricted_database) -> None:
    database = restricted_database
    await assert_rejected(
        database,
        "INSERT INTO ledger_accounts "
        "(id, name, owner_id, kind, currency, allow_overdraft, system_key) "
        "VALUES (gen_random_uuid(), 'Invalid overdraft', CAST(:owner AS uuid), "
        "'USER_WALLET', 'RUB', true, NULL)",
        {"owner": database.actor.id},
        "23514",
    )


@pytest.mark.parametrize(
    ("kind", "overdraft"), [("SYSTEM_FUNDING", "true"), ("SETTLEMENT", "false")]
)
async def test_system_account_cannot_bypass_policy_with_null_key(
    restricted_database, kind: str, overdraft: str
) -> None:
    # PostgreSQL CHECK accepts NULL as well as TRUE. A policy that only compares
    # system_key = '...' would therefore allow this invalid privileged account.
    await assert_rejected(
        restricted_database,
        "INSERT INTO ledger_accounts "
        "(id, name, owner_id, kind, currency, allow_overdraft, system_key) "
        "VALUES (gen_random_uuid(), 'Invalid null policy', NULL, :kind, "
        f"'RUB', {overdraft}, NULL)",
        {"kind": kind},
        "23514",
    )


async def test_wallet_cannot_bind_another_owners_new_account(restricted_database) -> None:
    database = restricted_database
    account_id = str(uuid4())
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # A new account avoids confusing the owner FK violation with the
            # existing wallet.account_id unique constraint.
            await connection.execute(
                text(
                    "INSERT INTO ledger_accounts "
                    "(id, name, owner_id, kind, currency, allow_overdraft, system_key) "
                    "VALUES (CAST(:account AS uuid), 'Binding source', CAST(:owner AS uuid), "
                    "'USER_WALLET', 'RUB', false, NULL)"
                ),
                {"account": account_id, "owner": database.actor.id},
            )
            with pytest.raises(DBAPIError) as failure:
                await connection.execute(
                    text(
                        "INSERT INTO wallets (id, owner_id, account_id, name, currency, status) "
                        "VALUES (gen_random_uuid(), CAST(:wrong_owner AS uuid), "
                        "CAST(:account AS uuid), 'Invalid binding', 'RUB', 'ACTIVE')"
                    ),
                    {"account": account_id, "wrong_owner": database.recipient.id},
                )
            assert sqlstate(failure.value) == "23503"
        finally:
            await transaction.rollback()


async def test_wallet_cannot_rebind_account_identity(restricted_database) -> None:
    database = restricted_database
    await assert_rejected(
        database,
        "UPDATE wallets SET account_id = CAST(:other_account AS uuid) "
        "WHERE id = CAST(:wallet AS uuid)",
        {"other_account": database.target.account_id, "wallet": database.source.id},
        "23514",
    )


async def test_runtime_role_can_create_fund_reserve_release_and_transfer(
    restricted_database,
) -> None:
    database = restricted_database
    sessions = async_sessionmaker(database.engine, expire_on_commit=False)
    async with sessions() as session:
        try:
            source = await create_wallet(session, database.actor.id, "Runtime workflow source")
            target = await create_wallet(session, database.recipient.id, "Runtime workflow target")
            await topup(session, database.actor, source.id, 100_000)
            hold = await reserve(session, source, 20_000, str(uuid4()))
            reserved = await wallet_view(session, source)
            assert (reserved["posted_minor"], reserved["reserved_minor"]) == (100_000, 20_000)
            assert reserved["available_minor"] == 80_000
            await finish_hold(session, hold.id, "RELEASED")
            result = await create_transfer(session, database.actor, source.id, target.id, 10_000)
            assert result["amount_minor"] == 10_000
            assert (await wallet_view(session, source))["available_minor"] == 90_000
            assert (await wallet_view(session, target))["available_minor"] == 10_000
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            await session.rollback()
