import json
import logging
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

from app.core.context import correlation_id

HTTP_REQUESTS = Counter("ledgerflow_http_requests_total", "HTTP requests", ["method", "status"])
HTTP_DURATION = Histogram("ledgerflow_http_request_duration_seconds", "Request latency in seconds")
PAYMENTS = Counter("ledgerflow_payments_total", "Terminal payment transitions", ["status"])
OUTBOX_BACKLOG = Gauge("ledgerflow_outbox_backlog", "Unpublished durable outbox events")
WEBHOOK_FAILURES = Counter("ledgerflow_webhook_failures_total", "Webhook failed attempts")
RECONCILIATION_DISCREPANCIES = Gauge(
    "ledgerflow_reconciliation_discrepancies", "Latest reconciliation discrepancies"
)
INVARIANT_VIOLATIONS = Counter(
    "ledgerflow_invariant_violations_total", "Observed invariant violations"
)
PROVIDER_DURATION = Histogram(
    "ledgerflow_provider_response_seconds", "Provider request latency", ["provider"]
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only explicit event messages: never request bodies, credentials or SQL parameters.
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "correlation_id": correlation_id.get(),
            }
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("ledgerflow")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def configure_tracing(app: Any) -> None:
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    from app.core.config import get_settings

    provider = TracerProvider(resource=Resource.create({"service.name": "ledgerflow-backend"}))
    endpoint = get_settings().otel_exporter_otlp_endpoint
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")
