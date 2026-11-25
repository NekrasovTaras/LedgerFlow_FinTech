from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    expected_behavior: str
    recovery: str

    def public(self) -> dict[str, str]:
        return asdict(self)


SCENARIOS = {
    item.id: item
    for item in [
        Scenario(
            "provider_unavailable",
            "Provider unavailable before acceptance",
            "The first local provider explicitly rejects before accepting the synthetic payment.",
            "Confirmed no-effect permits fallback; the payment has exactly one captured journal.",
            "Restore the provider; retry only confirmed no-effect operations. Never "
            "reroute UNKNOWN.",
        ),
        Scenario(
            "provider_lost_response",
            "Provider accepted, response lost",
            "A provider commits its receipt, then delays its response beyond the client timeout.",
            "UNKNOWN retains the hold; original-provider status lookup captures exactly once.",
            "Query the original provider by operation ID; keep the hold while the "
            "result is unknown.",
        ),
        Scenario(
            "broker_outage",
            "AMQP client-route outage and recovery",
            "An experiment-owned TCP listener disconnects the publisher before the AMQP handshake. "
            "The RabbitMQ process itself is not stopped.",
            "The durable outbox records the failed attempt, then publishes after route recovery.",
            "Restore the broker route and replay the retained event; preserve the same event ID.",
        ),
        Scenario(
            "worker_before_ack",
            "Consumer disconnect before acknowledgement",
            "A dedicated experiment queue loses its consumer connection after the PostgreSQL "
            "business effect commits but before ack. No OS process is killed.",
            "RabbitMQ redelivers the unacknowledged message; inbox dedup keeps one "
            "business effect.",
            "Reconnect the consumer and acknowledge after its idempotent PostgreSQL transaction.",
        ),
        Scenario(
            "duplicate_event",
            "Duplicate broker delivery",
            "The same durable event envelope is actually published twice to an experiment queue.",
            "Two messages produce one inbox record and one webhook delivery.",
            "Keep stable event IDs and the inbox uniqueness constraint; acknowledge duplicates.",
        ),
        Scenario(
            "duplicate_requests",
            "100 identical financial commands",
            "One hundred concurrent application commands use the same PostgreSQL idempotency key. "
            "This is application-entrypoint contention, not a TCP throughput benchmark.",
            "All commands identify one payment, and that payment is captured exactly once.",
            "Retry the original idempotency key; inspect its stored response instead of creating "
            "another debit.",
        ),
        Scenario(
            "concurrent_debits",
            "20 concurrent debits against 1,000 RUB",
            "Twenty application commands each request 100 RUB from an isolated 1,000 RUB wallet; "
            "additional risk rules are explicitly bypassed only inside this experiment.",
            "Ten operations succeed, ten fail for insufficient funds, and final balance is zero.",
            "Preserve sorted PostgreSQL account locks and atomic holds/captures; never "
            "edit balances.",
        ),
        Scenario(
            "webhook_failure",
            "Webhook receiver HTTP 500",
            "The configured project receiver rejects one real signed delivery, then recovers.",
            "The failed attempt is retained; signed retry reaches DELIVERED with the "
            "same event ID.",
            "Restore the receiver and retry the durable delivery; replay DEAD rows only"
            " after review.",
        ),
        Scenario(
            "reconciliation_discrepancy",
            "Targeted provider-report discrepancy",
            "Only an experiment-owned receipt is altered in the local provider report copy. "
            "The actual receipt and ledger are not changed.",
            "Reconciliation records PROVIDER_AMOUNT for that payment, then the report is restored.",
            "Verify original receipts and use approved compensating entries; never "
            "overwrite a balance.",
        ),
        Scenario(
            "redis_outage",
            "Redis client-route interruption",
            "An experiment-owned TCP listener interrupts a Redis client. The shared Redis process "
            "remains running and PostgreSQL remains the source of financial truth.",
            "A real ledger transfer succeeds during the client-route failure and Redis reconnects.",
            "Restore the cache route; recover state from PostgreSQL, never from an "
            "assumed cache balance.",
        ),
    ]
}
