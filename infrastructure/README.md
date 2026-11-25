# Local sandbox infrastructure

`docker compose up --build -d` starts the complete application with synthetic
credentials, persistent project volumes and loopback-only host ports. The
`migrate` service runs `python -m app.bootstrap` and the runtime-role provisioning
helper before API and background processes start. It applies migrations,
idempotently seeds demonstration data and grants the separate `ledgerflow_app`
role the required data privileges. Only this one-shot service receives the owner
database URL. Runtime containers cannot create schema or temporary objects,
disable triggers, truncate tables or update/delete journal history.

PowerShell: `pwsh -File scripts/start.ps1`; stop without removing data:
`pwsh -File scripts/stop.ps1`. Linux/macOS: `sh scripts/start.sh` and
`docker compose --profile monitoring down`.

Add `--profile monitoring` (or PowerShell `-Monitoring`) for Prometheus on 9090,
Grafana on 3000 and an OTLP/HTTP collector on 4318. Grafana sandbox credentials:
`sandbox-admin` / `sandbox-observability-password`. Dashboards display actual
Prometheus data and show no data when a metric has not been emitted.

To export backend traces inside Compose, set `OTEL_EXPORTER_OTLP_ENDPOINT` to
`http://otel-collector:4318` and run the monitoring profile. Host-based development
uses `http://127.0.0.1:4318`. The collector's debug exporter makes local spans
observable in `docker compose logs otel-collector`; it is not a persistent tracing
backend. No external telemetry destination or credentials are configured.

PostgreSQL is the financial source of truth. Redis is a cache/task backend only.
Provider SQLite volumes store independent synthetic provider receipts; they do
not store LedgerFlow ledger balances. Mock receivers and providers refuse startup
without explicit `SANDBOX_MODE=true`. Their control endpoints are local simulation
controls, not production administrative interfaces.

All values in `.env.example` are public demonstration values. Do not reuse these
credentials or expose this sandbox to the Internet. Do not run volume deletion or
Docker prune commands against a shared Docker installation.
