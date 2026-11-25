"""Provision the project-scoped runtime role after owner-run migrations.

Run only from the Compose migrate service. Runtime containers receive neither the
owner database URL nor its password. This helper never prints credential values.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = "ledgerflow_app"

APPEND_ONLY_TABLES = (
    "ledger_accounts",
    "journal_entries",
    "postings",
    "transfers",
    "refunds",
    "timeline_events",
    "audit_events",
    "login_attempts",
    "inbox_events",
    "discrepancies",
)
MUTABLE_TABLES = (
    "users",
    "auth_sessions",
    "wallets",
    "payments",
    "idempotency_records",
    "risk_reviews",
    "outbox_events",
    "webhook_subscriptions",
    "webhook_deliveries",
    "provider_circuits",
    "reconciliation_runs",
    "chaos_experiments",
)


async def provision() -> None:
    database_url = os.environ["DATABASE_URL"]
    password = os.environ["RUNTIME_DB_PASSWORD"]
    if not password:
        raise ValueError("A runtime database password is required.")
    engine = create_async_engine(database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(644101032)"))
            exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                {"role": ROLE},
            )
            # PostgreSQL performs literal/identifier quoting. The resulting DDL
            # is kept in memory and errors are deliberately not echoed below.
            role_ddl = await connection.scalar(
                text(
                    "SELECT format("
                    "'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L', "
                    "CAST(:role AS text), CAST(:password AS text))"
                    if exists
                    else "SELECT format("
                    "'CREATE ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L', "
                    "CAST(:role AS text), CAST(:password AS text))"
                ),
                {"role": ROLE, "password": password},
            )
            assert isinstance(role_ddl, str)
            await connection.exec_driver_sql(role_ddl)
            database_identifier = await connection.scalar(
                text("SELECT quote_ident(current_database())")
            )
            assert isinstance(database_identifier, str)
            await connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {database_identifier} TO {ROLE}"
            )
            # A temporary trigger could otherwise increase pg_trigger_depth()
            # and impersonate a trusted nested projection update. The runtime
            # needs neither temporary objects nor function/schema creation.
            await connection.exec_driver_sql(
                f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM PUBLIC"
            )
            await connection.exec_driver_sql(
                f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM {ROLE}"
            )
            await connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA public FROM {ROLE}")
            await connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
            await connection.exec_driver_sql(
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}"
            )
            await connection.exec_driver_sql(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}"
            )
            append_tables = ", ".join(f"public.{name}" for name in APPEND_ONLY_TABLES)
            mutable_tables = ", ".join(f"public.{name}" for name in MUTABLE_TABLES)
            await connection.exec_driver_sql(f"GRANT INSERT ON {append_tables} TO {ROLE}")
            await connection.exec_driver_sql(f"GRANT INSERT, UPDATE ON {mutable_tables} TO {ROLE}")
            # SELECT FOR UPDATE requires UPDATE on at least one column. The
            # projection guard still rejects direct edits, including by owners.
            await connection.exec_driver_sql(
                f"GRANT INSERT ON public.balance_projections, public.holds TO {ROLE}"
            )
            await connection.exec_driver_sql(
                "GRANT UPDATE (balance_minor, reserved_minor) "
                f"ON public.balance_projections TO {ROLE}"
            )
            await connection.exec_driver_sql(f"GRANT UPDATE (status) ON public.holds TO {ROLE}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(provision())
    except Exception as error:
        # SQLAlchemy exceptions may include SQL text containing a password.
        # Expose the error category only, never the statement or parameters.
        print(f"Runtime database role provisioning failed ({type(error).__name__}).")
        raise SystemExit(1) from None
    print("Runtime database role provisioned with restricted project privileges.")
