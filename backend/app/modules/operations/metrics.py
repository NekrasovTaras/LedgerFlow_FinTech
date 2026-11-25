"""Cross-process operational metrics from committed PostgreSQL facts.

Refresh asynchronously before exposition; collect() itself never performs I/O.
Counters use retained audit/timeline facts and survive API/worker restarts. A
database restore can reset them, which Prometheus rate() handles as a reset.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from prometheus_client import REGISTRY
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.metrics_core import Metric
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import SessionLocal

OUTCOMES = ("SUCCEEDED", "FAILED", "CANCELED")
TIMING_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


@dataclass(frozen=True)
class BusinessSnapshot:
    outcomes: dict[str, int] = field(default_factory=dict)
    outbox_backlog: int = 0
    webhook_failures: int = 0
    discrepancies: int = 0
    invariant_failures: int = 0
    timing_counts: tuple[int, ...] = (0,) * (len(TIMING_BUCKETS) + 1)
    timing_sum: float = 0.0
    # Only the fixed alpha/beta names are read; never financial IDs or raw URLs.
    providers: tuple[tuple[str, int, int, int], ...] = ()


class BusinessMetricsCollector:
    def __init__(self) -> None:
        self.snapshot = BusinessSnapshot()

    def collect(self) -> Iterable[Metric]:
        snapshot = self.snapshot
        outcomes = CounterMetricFamily(
            "ledgerflow_payments",
            "Committed terminal payment transitions retained in the database",
            labels=["status"],
        )
        for status in OUTCOMES:
            outcomes.add_metric([status], snapshot.outcomes.get(status, 0))
        yield outcomes
        yield GaugeMetricFamily(
            "ledgerflow_outbox_backlog",
            "Committed events awaiting publication, excluding dead and isolated experiments",
            value=snapshot.outbox_backlog,
        )
        yield CounterMetricFamily(
            "ledgerflow_webhook_failures",
            "Failed webhook attempts retained in committed audit history across all relays",
            value=snapshot.webhook_failures,
        )
        yield GaugeMetricFamily(
            "ledgerflow_reconciliation_discrepancies",
            "Discrepancy count from the latest completed reconciliation across all workers",
            value=snapshot.discrepancies,
        )
        yield CounterMetricFamily(
            "ledgerflow_invariant_violations",
            "Persisted reconciliation checks reporting invalid financial invariants",
            value=snapshot.invariant_failures,
        )
        buckets = [
            (str(bound), count)
            for bound, count in zip(TIMING_BUCKETS, snapshot.timing_counts, strict=False)
        ]
        buckets.append(("+Inf", snapshot.timing_counts[-1]))
        yield HistogramMetricFamily(
            "ledgerflow_payment_processing_duration_seconds",
            "Persisted processing start to first success/failure, including UNKNOWN recovery; "
            "excludes legacy records whose terminal timestamp predates processing start",
            buckets=buckets,
            sum_value=snapshot.timing_sum,
        )
        requests = CounterMetricFamily(
            "ledgerflow_provider_submission_requests",
            "Provider submission HTTP attempts recorded by all processes",
            labels=["provider"],
        )
        responses = CounterMetricFamily(
            "ledgerflow_provider_confirmed_responses",
            "Valid definitive provider responses recorded by all processes",
            labels=["provider"],
        )
        latency = GaugeMetricFamily(
            "ledgerflow_provider_last_response_seconds",
            "Latest recorded submission HTTP latency across all processes",
            labels=["provider"],
        )
        for name, total, confirmed, latency_ms in snapshot.providers:
            requests.add_metric([name], total)
            responses.add_metric([name], confirmed)
            latency.add_metric([name], latency_ms / 1000)
        yield requests
        yield responses
        yield latency


business_metrics = BusinessMetricsCollector()
REGISTRY.register(business_metrics)


async def read_business_snapshot(session: AsyncSession) -> BusinessSnapshot:
    outcomes = {
        status: int(count)
        for status, count in (
            await session.execute(
                text("""
                    SELECT status, count(*) FROM timeline_events
                    WHERE kind = 'payment.state' AND status IN ('SUCCEEDED','FAILED','CANCELED')
                    GROUP BY status
                """)
            )
        ).all()
    }
    totals = (
        (
            await session.execute(
                text("""
                    SELECT
                      (SELECT count(*) FROM outbox_events
                         WHERE status IN ('PENDING','RETRY','PUBLISHING')) AS outbox_backlog,
                      (SELECT count(*) FROM audit_events WHERE action = 'webhook.attempt'
                         AND data->>'error' IS NOT NULL) AS webhook_failures,
                      coalesce((SELECT (summary->>'discrepancy_count')::bigint
                         FROM reconciliation_runs WHERE completed_at IS NOT NULL
                         ORDER BY completed_at DESC, id DESC LIMIT 1), 0) AS discrepancies,
                      (SELECT count(*) FROM reconciliation_runs WHERE completed_at IS NOT NULL
                         AND summary->'invariants'->>'valid' = 'false') AS invariant_failures
                """)
            )
        )
        .mappings()
        .one()
    )
    bucket_sql = ", ".join(
        f"count(*) FILTER (WHERE duration <= :bucket_{index}) AS bucket_{index}"
        for index in range(len(TIMING_BUCKETS))
    )
    timing = (
        (
            await session.execute(
                text(f"""
                    WITH terminal AS (
                      SELECT payment_id, min(created_at) AS finished_at FROM timeline_events
                      WHERE kind = 'payment.state' AND status IN ('SUCCEEDED','FAILED')
                      GROUP BY payment_id
                    ), durations AS (
                      SELECT extract(epoch FROM (t.finished_at - p.processing_started_at))
                        AS duration
                      FROM payments p JOIN terminal t ON t.payment_id = p.id
                      WHERE p.processing_started_at IS NOT NULL
                        AND t.finished_at >= p.processing_started_at
                    )
                    SELECT {bucket_sql}, count(*) AS total,
                      coalesce(sum(duration), 0) AS duration_sum FROM durations
                """),
                {f"bucket_{index}": bound for index, bound in enumerate(TIMING_BUCKETS)},
            )
        )
        .mappings()
        .one()
    )
    providers = (
        await session.execute(
            text("""
                SELECT name, total_requests, successes, last_latency_ms FROM provider_circuits
                WHERE name IN ('alpha','beta') ORDER BY name
            """)
        )
    ).all()
    return BusinessSnapshot(
        outcomes=outcomes,
        **{key: int(value) for key, value in totals.items()},
        timing_counts=(
            *(int(timing[f"bucket_{index}"]) for index in range(len(TIMING_BUCKETS))),
            int(timing["total"]),
        ),
        timing_sum=float(timing["duration_sum"]),
        providers=tuple(
            (str(name), int(total), int(ok), int(ms)) for name, total, ok, ms in providers
        ),
    )


async def refresh_business_metrics() -> None:
    async with SessionLocal.begin() as session:
        # All families describe the same committed snapshot, not a mixture of
        # payment state before commit and an outbox/backlog after another commit.
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        snapshot = await read_business_snapshot(session)
    business_metrics.snapshot = snapshot
