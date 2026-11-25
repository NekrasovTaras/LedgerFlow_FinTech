"""Bounded local sandbox workload. Never targets real payments or remote hosts.

Run through scripts/run-benchmarks.ps1. HTTP samples include explicitly named setup
and final checks; transaction outcomes and integrity are reported separately.
"""

import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from locust import HttpUser, LoadTestShape, between, events, task
from locust.exception import StopUser

SEED_MINOR = 10_000_000
AMOUNT_MINOR = 100
SANDBOX_PASSWORD = "DemoPass!2026"
OUTPUT = Path(os.environ.get("LEDGERFLOW_BENCH_OUTPUT", "artifacts/benchmarks/latest"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTORS: list[dict] = []
CHECKS: dict = {"before": None, "after": None, "errors": []}
STARTED_AT: str | None = None
SPAWNED: list[dict] = []


def local_host(host: str) -> str:
    address = urlsplit(host)
    if address.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Benchmark host must be a local LedgerFlow sandbox.")
    if address.scheme != "http" or address.username or address.password:
        raise ValueError("Use a plain local HTTP URL without credentials.")
    return host.rstrip("/")


def reconciliation(host: str) -> dict:
    """Global SQL invariants are measured, never inferred from HTTP success."""
    with requests.Session() as client:
        response = client.post(
            f"{host}/api/v1/auth/login",
            json={"email": "admin@ledgerflow.local", "password": SANDBOX_PASSWORD},
            timeout=20,
        )
        response.raise_for_status()
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        response = client.post(f"{host}/api/v1/reconciliation/runs", json={}, timeout=60)
        response.raise_for_status()
        body = response.json()
        return {"id": body["id"], "status": body["status"], "summary": body["summary"]}


@events.test_start.add_listener
def before_run(environment, **_kwargs):
    global STARTED_AT
    host = local_host(environment.host or "http://127.0.0.1:8000")
    output = (PROJECT_ROOT / OUTPUT).resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("Benchmark evidence must stay in project artifacts/.")
    output.mkdir(parents=True, exist_ok=True)
    STARTED_AT = datetime.now(UTC).isoformat()
    random.seed(20261125)
    try:
        CHECKS["before"] = reconciliation(host)
    except (requests.RequestException, ValueError, KeyError) as error:
        raise SystemExit(f"Baseline verification failed: {error}") from error
    if not CHECKS["before"]["summary"]["invariants"]["valid"]:
        raise SystemExit("Financial invariants are already invalid; abort benchmark.")


@events.spawning_complete.add_listener
def record_spawn(user_count, **_kwargs):
    SPAWNED.append({"at": datetime.now(UTC).isoformat(), "users": user_count})


@events.test_stop.add_listener
def after_run(environment, **_kwargs):
    try:
        CHECKS["after"] = reconciliation(local_host(environment.host))
    except (requests.RequestException, ValueError, KeyError) as error:
        CHECKS["errors"].append(f"Post-run reconciliation: {type(error).__name__}: {error}")
    stats = environment.stats.total
    errors = stats.num_failures
    invalid_wallets = [actor for actor in ACTORS if not actor.get("valid", False)]
    valid = bool(
        CHECKS["after"]
        and CHECKS["after"]["summary"]["invariants"]["valid"]
        and not CHECKS["errors"]
        and ACTORS
        and not invalid_wallets
        and not errors
    )
    result = {
        "started_at": STARTED_AT,
        "finished_at": datetime.now(UTC).isoformat(),
        "host": environment.host,
        "shape": [
            {"until_seconds": 10, "users": 1},
            {"until_seconds": 20, "users": 5},
            {"until_seconds": 30, "users": 10},
        ],
        "seed_per_wallet_minor": SEED_MINOR,
        "amount_minor": AMOUNT_MINOR,
        "random_seed": 20261125,
        "spawned": SPAWNED,
        "requests_including_setup_and_final_checks": stats.num_requests,
        "failures": errors,
        "failure_rate": stats.fail_ratio,
        "requests_per_second": stats.total_rps,
        "response_time_ms": {
            "p50": stats.get_response_time_percentile(0.50),
            "p95": stats.get_response_time_percentile(0.95),
            "p99": stats.get_response_time_percentile(0.99),
        },
        "request_groups": [
            {
                "method": item.method,
                "name": item.name,
                "requests": item.num_requests,
                "failures": item.num_failures,
                "rps": item.total_rps,
                "p50_ms": item.get_response_time_percentile(0.50),
                "p95_ms": item.get_response_time_percentile(0.95),
                "p99_ms": item.get_response_time_percentile(0.99),
            }
            for item in environment.stats.entries.values()
        ],
        "financial_integrity": CHECKS,
        "actor_checks": ACTORS,
        "valid": valid,
    }
    (PROJECT_ROOT / OUTPUT / "summary.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    if not valid:
        environment.process_exit_code = 1


class LocalFinancialUser(HttpUser):
    wait_time = between(0.2, 0.5)

    def call(self, method, path, body=None, name=None, key=None):
        headers = {"Idempotency-Key": key or str(uuid4())} if method == "POST" else {}
        with self.client.request(
            method,
            f"/api/v1{path}",
            json=body,
            headers=headers,
            name=name or path,
            catch_response=True,
            timeout=20,
        ) as response:
            if response.status_code not in {200, 201}:
                response.failure(f"Unexpected HTTP {response.status_code}")
                raise StopUser()
            data = response.json()
            if not isinstance(data, dict):
                response.failure("Expected a JSON object")
                raise StopUser()
            return data

    def on_start(self):
        local_host(self.host)
        self.record = {
            "actor": str(uuid4()),
            "payments": 0,
            "transfers": 0,
            "idempotent_repeats": 0,
            "valid": False,
        }
        ACTORS.append(self.record)
        self.source = self.destination = None
        email = f"locust-{self.record['actor']}@ledgerflow.local"
        self.call(
            "POST",
            "/auth/register",
            {
                "email": email,
                "password": SANDBOX_PASSWORD,
                "display_name": "Locust synthetic customer",
            },
            "setup/register",
        )
        auth = self.call(
            "POST",
            "/auth/login",
            {"email": email, "password": SANDBOX_PASSWORD},
            "setup/login",
        )
        self.client.headers["Authorization"] = f"Bearer {auth['access_token']}"
        wallets = []
        for suffix in ("source", "destination"):
            wallet = self.call(
                "POST",
                "/wallets",
                {"name": f"Locust {suffix} {self.record['actor']}", "currency": "RUB"},
                "setup/wallet",
            )
            self.call(
                "POST",
                f"/wallets/{wallet['id']}/sandbox-topup",
                {"amount_minor": SEED_MINOR},
                "setup/sandbox-topup",
            )
            wallets.append(wallet["id"])
        self.source, self.destination = wallets
        self.record.update(
            {"source_wallet_id": self.source, "destination_wallet_id": self.destination}
        )
        merchants = self.call("GET", "/merchants", name="setup/merchant-directory")["items"]
        if not merchants:
            CHECKS["errors"].append("No published local merchant.")
            raise StopUser()
        self.merchant = merchants[0]["wallet_id"]

    @task(5)
    def history(self):
        self.call("GET", "/payments?page_size=20", name="business/payment-history")

    @task(2)
    def ledger_history(self):
        self.call(
            "GET",
            f"/wallets/{self.source}/transactions?page_size=20",
            name="business/ledger-history",
        )

    @task(2)
    def payment(self):
        result = self.call(
            "POST",
            "/payments",
            {
                "wallet_id": self.source,
                "merchant_wallet_id": self.merchant,
                "amount_minor": AMOUNT_MINOR,
                "description": "Locust virtual payment",
                "provider_behavior": "success",
                "auto_process": True,
            },
            "business/payment",
        )
        if result["status"] != "SUCCEEDED":
            CHECKS["errors"].append(
                f"Payment outcome {result['status']}; workload expects funded, low-risk success."
            )
            raise StopUser()
        self.record["payments"] += 1

    @task(2)
    def transfer(self):
        self.call(
            "POST",
            "/transfers",
            {
                "source_wallet_id": self.source,
                "destination_wallet_id": self.destination,
                "amount_minor": AMOUNT_MINOR,
                "description": "Locust virtual transfer",
            },
            "business/transfer",
        )
        self.record["transfers"] += 1

    @task(2)
    def idempotent_repeat(self):
        key = str(uuid4())
        body = {
            "source_wallet_id": self.source,
            "destination_wallet_id": self.destination,
            "amount_minor": AMOUNT_MINOR,
            "description": "Locust idempotent transfer",
        }
        first = self.call("POST", "/transfers", body, "business/idempotent-first", key)
        self.record["transfers"] += 1
        again = self.call("POST", "/transfers", body, "business/idempotent-repeat", key)
        if first["id"] != again["id"] or first["journal_id"] != again["journal_id"]:
            CHECKS["errors"].append("An idempotent repeat returned a different financial effect.")
            raise StopUser()
        self.record["idempotent_repeats"] += 1

    def on_stop(self):
        if not self.source or not self.destination:
            return
        try:
            source = self.call("GET", f"/wallets/{self.source}", name="verify/source-balance")
            destination = self.call(
                "GET", f"/wallets/{self.destination}", name="verify/destination-balance"
            )
            expected_source = SEED_MINOR - AMOUNT_MINOR * (
                self.record["payments"] + self.record["transfers"]
            )
            expected_destination = SEED_MINOR + AMOUNT_MINOR * self.record["transfers"]
            self.record.update(
                {
                    "expected_source_minor": expected_source,
                    "actual_source_minor": source["posted_minor"],
                    "expected_destination_minor": expected_destination,
                    "actual_destination_minor": destination["posted_minor"],
                    "valid": source["posted_minor"] == source["available_minor"] == expected_source
                    and destination["posted_minor"]
                    == destination["available_minor"]
                    == expected_destination
                    and source["reserved_minor"] == destination["reserved_minor"] == 0,
                }
            )
        except (StopUser, requests.RequestException, ValueError, KeyError) as error:
            CHECKS["errors"].append(f"Wallet verification failed: {type(error).__name__}: {error}")


class BoundedSandboxShape(LoadTestShape):
    def tick(self):
        elapsed = self.get_run_time()
        for until, users in ((10, 1), (20, 5), (30, 10)):
            if elapsed < until:
                return users, 1
        return None
