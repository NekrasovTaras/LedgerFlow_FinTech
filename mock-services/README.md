# Independent local mock services

These processes simulate external providers and a webhook receiver using public
sandbox values only. They refuse startup without `SANDBOX_MODE=true`.

Compose runs `provider-a` (alpha, port 9001), `provider-b` (beta, port 9002) and
`receiver` (port 9003). Host ports bind to `127.0.0.1`. Each service owns a separate
persistent SQLite volume containing synthetic receipts, not LedgerFlow balances.

## Provider contract

`POST /payments` accepts:

```json
{"operation_id":"stable-business-id","amount_minor":10000,"currency":"RUB","behavior":"success"}
```

Supported behaviors:

- `success`: persist and return `SUCCEEDED` with a stable `provider_reference`.
- `decline`: persist and return `FAILED`; the simulated fee is zero.
- `unavailable`: return HTTP 503 before acceptance, with
  `detail.no_effect=true` and `detail.code="UNAVAILABLE"`; no receipt is stored.
- `lost_response`: persist `SUCCEEDED`, wait five seconds, then return HTTP 504.
  Backend timeout is shorter. Check status before considering another provider.
- `configured`: use the configured success probability; deterministic tests use
  probability 0 or 1, or one of the explicit behaviors.

`GET /payments/{operation_id}` returns `SUCCEEDED`, `FAILED` or `NOT_FOUND`.
Replaying the same operation and amount/currency returns its original receipt.
Reusing its ID with different amount/currency returns HTTP 409. The request's
behavior is simulation configuration and does not change an existing receipt.

`POST /control` configures `available`, `success_probability`, `delay_ms`,
`fee_bps`, `force_behavior` and `report_discrepancy_minor`. This is a full control
replacement with defaults, so send all values that should remain enabled.
`GET /control` returns current controls. `GET /report` returns receipts; its
optional discrepancy affects only the report, never the stored receipt.

## Signed receiver contract

`POST /events` requires `X-LedgerFlow-Event-ID`,
`X-LedgerFlow-Timestamp` (Unix seconds) and `X-LedgerFlow-Signature`.
Calculate the signature over the exact transmitted body bytes:

```text
sha256=<hex HMAC-SHA256(secret, timestamp + "." + event_id + "." + raw_body)>
```

Requests older/newer than five minutes are rejected. A repeated event with the
same body returns `duplicate=true`; it increments the delivery count without
creating a second business receipt. A different body for the same event ID is a
409 conflict. The JSON `event_id`, when present, must match the signed header.
Bodies are limited to 64 KiB.

`POST /control/fail` with `{"fail":true}` enables HTTP 500 before the event is
recorded. Send `{"fail":false}` to recover. `GET /events` lists received event IDs
and delivery counts. `GET /health` checks the local state store.

## Verification

From `backend/`, after `uv sync --frozen --all-groups`:

```sh
uv run pytest -c pyproject.toml ../mock-services/test_service.py -q
uv run ruff check --config pyproject.toml ../mock-services
uv run ruff format --check --config pyproject.toml ../mock-services
```

The tests cover explicit sandbox gating, persistence across restarts, payload
conflicts, receipt independence, success-before-lost-response, no-effect 503,
signed webhook validation, duplicate handling and receiver failure/recovery.
