"""Apply migrations and create explicitly enabled, repeatable sandbox data."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import select, text

from app.core.config import get_settings
from app.infrastructure.database import SessionLocal, engine
from app.infrastructure.models import BalanceProjection, LedgerAccount, ProviderCircuit, User
from app.modules.ledger.application import create_wallet, topup

DEMO_PASSWORD = "DemoPass!2026"  # Public synthetic sandbox credential, never a production account.


async def validate_runtime_mode() -> None:
    if get_settings().sandbox_mode:
        return
    async with SessionLocal() as session:
        if await session.scalar(select(User.id).where(User.is_sandbox.is_(True)).limit(1)):
            raise RuntimeError(
                "Refusing non-sandbox startup with sandbox identities. Use a clean database."
            )


async def seed() -> None:
    await validate_runtime_mode()
    async with SessionLocal.begin() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(644101031)"))
        for key, name, overdraft in [
            ("sandbox_funding", "Sandbox funding", True),
            ("settlement", "Settlement", False),
        ]:
            if not await session.scalar(
                select(LedgerAccount).where(LedgerAccount.system_key == key)
            ):
                account = LedgerAccount(
                    name=name,
                    kind="SYSTEM_FUNDING" if overdraft else "SETTLEMENT",
                    currency="RUB",
                    allow_overdraft=overdraft,
                    system_key=key,
                )
                session.add(account)
                await session.flush()
                session.add(
                    BalanceProjection(
                        account_id=account.id,
                        balance_minor=0,
                        reserved_minor=0,
                        allow_overdraft=overdraft,
                    )
                )
        for name in ["alpha", "beta"]:
            if not await session.get(ProviderCircuit, name):
                session.add(
                    ProviderCircuit(
                        name=name, failures=0, total_requests=0, successes=0, last_latency_ms=0
                    )
                )
        await session.flush()
        if get_settings().sandbox_mode:
            hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
            for role, local in [
                ("administrator", "admin"),
                ("customer", "customer"),
                ("merchant", "merchant"),
                ("risk_analyst", "analyst"),
                ("operator", "operator"),
            ]:
                email = f"{local}@ledgerflow.local"
                if await session.scalar(select(User).where(User.email == email)):
                    continue
                user = User(
                    email=email,
                    display_name=f"Demo {role.replace('_', ' ').title()}",
                    password_hash=hasher.hash(DEMO_PASSWORD),
                    role=role,
                    active=True,
                    is_sandbox=True,
                )
                session.add(user)
                await session.flush()
                if role in {"customer", "merchant", "administrator"}:
                    wallet = await create_wallet(session, user.id, f"{local.title()} RUB wallet")
                    await topup(
                        session, user, wallet.id, 500_000 if role != "merchant" else 100_000
                    )
    await engine.dispose()


def main() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    asyncio.run(seed())


if __name__ == "__main__":
    main()
