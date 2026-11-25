from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def service_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    @contextmanager
    def create(kind: str = "provider", name: str = "alpha", sandbox: bool = True):  # type: ignore[no-untyped-def]
        monkeypatch.setenv("SERVICE_KIND", kind)
        monkeypatch.setenv("SERVICE_NAME", name)
        monkeypatch.setenv("SANDBOX_MODE", str(sandbox).lower())
        monkeypatch.setenv("STATE_PATH", str(tmp_path / f"{name}.sqlite3"))
        monkeypatch.setenv("LOST_RESPONSE_DELAY_SECONDS", "0.01")
        monkeypatch.setenv("WEBHOOK_SECRET", "sandbox-test-secret")
        module_name = f"mock_service_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(
            module_name, Path(__file__).with_name("service.py")
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        try:
            with TestClient(module.app) as client:
                yield client
        finally:
            sys.modules.pop(module_name, None)

    return create


def payment(operation_id: str = "operation-1", behavior: str = "success") -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "amount_minor": 10_000,
        "currency": "RUB",
        "behavior": behavior,
    }


def signed_headers(
    body: bytes, event_id: str = "event-1", timestamp: int | None = None
) -> dict[str, str]:
    signed_at = str(timestamp if timestamp is not None else int(time.time()))
    material = signed_at.encode() + b"." + event_id.encode() + b"." + body
    signature = hmac.new(b"sandbox-test-secret", material, hashlib.sha256).hexdigest()
    return {
        "X-LedgerFlow-Event-ID": event_id,
        "X-LedgerFlow-Timestamp": signed_at,
        "X-LedgerFlow-Signature": "sha256=" + signature,
        "Content-Type": "application/json",
    }


def test_explicit_sandbox_required(service_factory):  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeError, match="explicit SANDBOX_MODE"):
        with service_factory(sandbox=False):
            pass


def test_report_fault_targets_only_selected_operation(service_factory):  # type: ignore[no-untyped-def]
    with service_factory() as client:
        assert client.post("/payments", json=payment("earlier")).status_code == 200
        assert client.post("/payments", json=payment("selected")).status_code == 200
        changed = client.post(
            "/control", json={"report_discrepancy_minor": 5, "report_operation_id": "selected"}
        )
        assert changed.status_code == 200
        report = {row["operation_id"]: row for row in client.get("/report").json()["receipts"]}
        assert report["earlier"]["amount_minor"] == 10_000
        assert report["selected"]["amount_minor"] == 10_005
        assert client.get("/payments/selected").json()["amount_minor"] == 10_000
    with service_factory(kind="receiver", name="receiver") as client:
        assert client.get("/control/fail").json() == {"fail": False}
        assert client.post("/control/fail", json={"fail": True}).status_code == 200
        assert client.get("/control/fail").json() == {"fail": True}


def test_provider_receipt_survives_restart_and_replay(service_factory):  # type: ignore[no-untyped-def]
    with service_factory() as client:
        original = client.post("/payments", json=payment())
        assert original.status_code == 200
        assert original.json()["status"] == "SUCCEEDED"
        assert original.json()["fee_minor"] == 25
        assert client.post("/payments", json=payment()).json() == original.json()
        conflicting = payment()
        conflicting["amount_minor"] = 1
        assert client.post("/payments", json=conflicting).status_code == 409
        assert len(client.get("/report").json()["receipts"]) == 1
    with service_factory() as restarted:
        assert restarted.get("/payments/operation-1").json() == original.json()


def test_decline_and_unavailable_have_distinct_receipts(service_factory):  # type: ignore[no-untyped-def]
    with service_factory() as client:
        decline = client.post("/payments", json=payment("declined", "decline"))
        assert decline.status_code == 200
        assert decline.json()["status"] == "FAILED"
        assert decline.json()["fee_minor"] == 0
        unavailable = client.post("/payments", json=payment("unavailable", "unavailable"))
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["no_effect"] is True
        assert unavailable.json()["detail"]["code"] == "UNAVAILABLE"
        assert client.get("/payments/unavailable").json()["status"] == "NOT_FOUND"


def test_lost_response_is_committed_before_http_failure(service_factory):  # type: ignore[no-untyped-def]
    with service_factory() as client:
        lost = client.post("/payments", json=payment(behavior="lost_response"))
        assert lost.status_code == 504
        reconciled = client.get("/payments/operation-1").json()
        assert reconciled["status"] == "SUCCEEDED"
        assert client.post("/payments", json=payment()).json() == reconciled
        assert len(client.get("/report").json()["receipts"]) == 1


def test_provider_configuration_and_report_discrepancy(service_factory):  # type: ignore[no-untyped-def]
    with service_factory() as client:
        assert client.post("/control", json={"success_probability": 0}).status_code == 200
        assert (
            client.post("/payments", json=payment(behavior="configured")).json()["status"]
            == "FAILED"
        )
        client.post("/control", json={"report_discrepancy_minor": 10})
        assert client.get("/report").json()["receipts"][0]["amount_minor"] == 10_010
        assert client.get("/payments/operation-1").json()["amount_minor"] == 10_000


def test_provider_state_is_independent(service_factory):  # type: ignore[no-untyped-def]
    with service_factory(name="alpha") as first:
        first_receipt = first.post("/payments", json=payment()).json()
    with service_factory(name="beta") as second:
        assert second.get("/payments/operation-1").json()["status"] == "NOT_FOUND"
        second_receipt = second.post("/payments", json=payment()).json()
        assert second_receipt["provider_reference"] != first_receipt["provider_reference"]


def test_webhook_signature_dedup_and_payload_conflict(service_factory):  # type: ignore[no-untyped-def]
    with service_factory(kind="receiver", name="receiver") as client:
        body = json.dumps({"event_id": "event-1", "type": "payment.succeeded"}).encode()
        invalid = signed_headers(body)
        invalid["X-LedgerFlow-Signature"] = "sha256=" + "0" * 64
        assert client.post("/events", content=body, headers=invalid).status_code == 401
        accepted = client.post("/events", content=body, headers=signed_headers(body))
        assert accepted.status_code == 200
        assert accepted.json()["duplicate"] is False
        duplicate = client.post("/events", content=body, headers=signed_headers(body))
        assert duplicate.json()["duplicate"] is True
        assert client.get("/events").json()["events"][0]["deliveries"] == 2
        conflicting = b'{"event_id":"event-1","type":"payment.failed"}'
        assert (
            client.post(
                "/events", content=conflicting, headers=signed_headers(conflicting)
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/events", content=body, headers=signed_headers(body, timestamp=1)
            ).status_code
            == 401
        )


def test_webhook_failure_recovers_without_receiving_event(service_factory):  # type: ignore[no-untyped-def]
    with service_factory(kind="receiver", name="receiver") as client:
        body = b'{"event_id":"event-1"}'
        assert client.post("/control/fail", json={"fail": True}).status_code == 200
        assert client.post("/events", content=body, headers=signed_headers(body)).status_code == 500
        assert client.get("/events").json()["events"] == []
        client.post("/control/fail", json={"fail": False})
        assert client.post("/events", content=body, headers=signed_headers(body)).status_code == 200
