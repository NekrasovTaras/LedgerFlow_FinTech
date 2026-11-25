# ADR 0004 — Committed business metrics across application processes

Status: accepted, 2026-11-25.

The API, payment worker, outbox relay and scheduler are separate processes.
An API-only Prometheus registry cannot describe business changes performed by
the other processes. Incrementing a payment counter before transaction commit
also counts changes that PostgreSQL may roll back.

Before `/metrics` exposition, the API reads a repeatable-read, read-only
PostgreSQL snapshot. A collector publishes retained payment transitions,
processing durations, outbox backlog, webhook failures, reconciliation findings
and provider request statistics from committed timeline/audit/domain records.
Financial IDs, actor IDs and URLs never become labels. Histogram durations are
operational seconds; ledger amounts remain exact integer minor units.

HTTP request latency and provider HTTP histograms remain process-local and say
so in their scope/documentation. Durable provider counters and latest latency
provide visibility into submissions made by other processes. A database restore
may reset cumulative series; Prometheus handles counter resets.

Legacy timeline timestamps can precede processing start. Such durations are
excluded with an explicit metric description; immutable history is not rewritten.
Current event helpers assign actual UTC insertion times to preserve causal order.

The sandbox collector scans retained history. Materialized transactionally
maintained aggregates or an incremental exporter are required before growing
history substantially. A failed database read fails the scrape rather than
publishing a new set of misleading zero values.

Evidence: `backend/tests/test_observability.py` verifies commit/rollback visibility,
real journal effects, bounded labels, histogram exposition and `/metrics`.
