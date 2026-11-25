from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import INVARIANT_VIOLATIONS, RECONCILIATION_DISCREPANCIES
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import Discrepancy, Payment, ReconciliationRun, new_id
from app.modules.ledger.application import audit, serialize
from app.modules.providers.application import provider_url


async def verify_invariants(session: AsyncSession) -> dict[str, Any]:
    result = (
        (
            await session.execute(
                text("""
        SELECT
          (SELECT count(*) FROM (
             SELECT j.id FROM journal_entries j
             LEFT JOIN postings p ON p.journal_id = j.id
             GROUP BY j.id
             HAVING count(p.id) < 2 OR coalesce(sum(
               CASE WHEN p.side = 'DEBIT' THEN p.amount_minor::numeric
                    ELSE -p.amount_minor::numeric END), 0) <> 0
           ) bad) AS unbalanced_journals,
          (SELECT count(*) FROM balance_projections b
             LEFT JOIN (
               SELECT account_id, sum(CASE WHEN side = 'DEBIT'
                 THEN amount_minor::numeric ELSE -amount_minor::numeric END) amount
               FROM postings GROUP BY account_id
             ) p USING(account_id)
             WHERE b.balance_minor <> coalesce(p.amount, 0)) AS projection_mismatches,
          (SELECT count(*) FROM balance_projections b
             LEFT JOIN (
               SELECT account_id, sum(amount_minor::numeric) amount
               FROM holds WHERE status = 'ACTIVE' GROUP BY account_id
             ) h USING(account_id)
             WHERE b.reserved_minor <> coalesce(h.amount, 0)) AS hold_mismatches,
          (SELECT count(*) FROM balance_projections
             WHERE NOT allow_overdraft AND balance_minor < reserved_minor) AS negative_available,
          (SELECT coalesce(sum(balance_minor::numeric), 0)
             FROM balance_projections) AS closed_system_total,
          (SELECT count(*) FROM payments
             WHERE refunded_minor > amount_minor OR refunded_minor < 0) AS invalid_refunds
    """)
            )
        )
        .mappings()
        .one()
    )
    checks = {key: int(value) for key, value in result.items()}
    valid = all(value == 0 for value in checks.values())
    if not valid:
        INVARIANT_VIOLATIONS.inc()
    return {"valid": valid, **checks}


async def run_reconciliation(actor_id: str | None = None) -> dict[str, Any]:
    run_id = new_id()
    async with SessionLocal.begin() as session:
        session.add(ReconciliationRun(id=run_id, actor_id=actor_id, status="RUNNING", summary={}))
        audit(session, actor_id, "reconciliation.started", run_id)
    discrepancies: list[dict[str, str]] = []
    async with SessionLocal() as session:
        invariants = await verify_invariants(session)
        if not invariants["valid"]:
            discrepancies.append(
                {
                    "classification": "LEDGER_INVARIANT",
                    "reference_id": "ledger",
                    "detail": str(invariants),
                    "recommendation": (
                        "Freeze affected operations; inspect immutable journal and restore"
                        " through audited compensating entries. Never overwrite balances."
                    ),
                }
            )
        payments = {row.id: row for row in (await session.scalars(select(Payment))).all()}
    provider_counts: dict[str, int] = {}
    for provider in ["alpha", "beta"]:
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(f"{provider_url(provider)}/report")
                response.raise_for_status()
                receipts = response.json()["receipts"]
            provider_counts[provider] = len(receipts)
            receipt_by_id = {receipt["operation_id"]: receipt for receipt in receipts}
            for receipt in receipts:
                payment = payments.get(receipt["operation_id"])
                if payment is None:
                    # Tests and independent sandboxes may share mock providers; do not
                    # misclassify their receipts as this database's financial mismatch.
                    continue
                if payment.provider != provider:
                    discrepancies.append(
                        {
                            "classification": "WRONG_PROVIDER",
                            "reference_id": payment.id,
                            "detail": (
                                f"Receipt exists at {provider}, "
                                f"recorded provider is {payment.provider}."
                            ),
                            "recommendation": (
                                "Investigate routing and duplicate external effects; do not submit"
                                " again."
                            ),
                        }
                    )
                if receipt["amount_minor"] != payment.amount_minor:
                    discrepancies.append(
                        {
                            "classification": "PROVIDER_AMOUNT",
                            "reference_id": payment.id,
                            "detail": (
                                f"Provider amount {receipt['amount_minor']} differs "
                                f"from ledger payment {payment.amount_minor}."
                            ),
                            "recommendation": (
                                "Verify the provider receipt and create an approved compensating "
                                "ledger entry if required."
                            ),
                        }
                    )
                expected = payment.status in {"SUCCEEDED", "PARTIALLY_REFUNDED", "REFUNDED"}
                if receipt["status"] == "SUCCEEDED" and not expected:
                    discrepancies.append(
                        {
                            "classification": "UNRESOLVED_PROVIDER_SUCCESS",
                            "reference_id": payment.id,
                            "detail": f"Provider succeeded while payment is {payment.status}.",
                            "recommendation": (
                                "Resolve the original provider status and capture the existing "
                                "hold once."
                            ),
                        }
                    )
            for payment in payments.values():
                if (
                    payment.provider == provider
                    and payment.status in {"SUCCEEDED", "PARTIALLY_REFUNDED", "REFUNDED"}
                    and payment.id not in receipt_by_id
                ):
                    discrepancies.append(
                        {
                            "classification": "MISSING_PROVIDER_RECEIPT",
                            "reference_id": payment.id,
                            "detail": "A settled payment has no provider receipt.",
                            "recommendation": (
                                "Investigate the original provider without unsafe fallback or "
                                "balance edits."
                            ),
                        }
                    )
        except (httpx.HTTPError, ValueError, KeyError):
            discrepancies.append(
                {
                    "classification": "PROVIDER_UNAVAILABLE",
                    "reference_id": provider,
                    "detail": "Provider report could not be obtained.",
                    "recommendation": "Restore the local mock provider and rerun reconciliation.",
                }
            )
    async with SessionLocal.begin() as session:
        run = await session.get(ReconciliationRun, run_id)
        assert run is not None
        for discrepancy in discrepancies:
            session.add(Discrepancy(run_id=run_id, **discrepancy))
        run.status = "COMPLETED" if not discrepancies else "DISCREPANCIES"
        run.completed_at = datetime.now(UTC)
        run.summary = {
            "invariants": invariants,
            "discrepancy_count": len(discrepancies),
            "payments_checked": len(payments),
            "provider_receipts": provider_counts,
        }
        audit(
            session,
            actor_id,
            "reconciliation.completed",
            run_id,
            {"discrepancy_count": len(discrepancies)},
        )
        await session.flush()
        result = serialize(run)
    RECONCILIATION_DISCREPANCIES.set(len(discrepancies))
    return {**result, "discrepancies": discrepancies}
