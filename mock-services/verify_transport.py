"""Exercise actual HTTP timeouts and signatures against disposable local mocks."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4


def local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise argparse.ArgumentTypeError("Transport smoke checks require a loopback HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("Use a plain loopback service URL")
    return value.rstrip("/")


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 3,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-url", type=local_url, default="http://127.0.0.1:19001")
    parser.add_argument("--receiver-url", type=local_url, default="http://127.0.0.1:19003")
    args = parser.parse_args()
    for base in [args.provider_url, args.receiver_url]:
        for attempt in range(30):
            try:
                if request_json(base + "/health", timeout=1)[0] == 200:
                    break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 29:
                    raise
            time.sleep(0.2)
        assert request_json(base + "/health")[0] == 200
    operation_id = "transport-" + uuid4().hex
    payload: dict[str, Any] = {
        "operation_id": operation_id,
        "amount_minor": 1_000,
        "currency": "RUB",
        "behavior": "lost_response",
    }
    timed_out = False
    try:
        request_json(args.provider_url + "/payments", payload, timeout=0.25)
    except TimeoutError:
        timed_out = True
    assert timed_out, "Lost response must cause a real client timeout"
    status, receipt = request_json(args.provider_url + "/payments/" + operation_id)
    assert status == 200 and receipt["status"] == "SUCCEEDED"
    payload["behavior"] = "success"
    assert request_json(args.provider_url + "/payments", payload)[1] == receipt

    event_id = "transport-event-" + uuid4().hex
    event: dict[str, Any] = {"event_id": event_id, "type": "payment.succeeded"}
    body = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    material = timestamp.encode() + b"." + event_id.encode() + b"." + body
    signature = hmac.new(b"sandbox-webhook-signing-secret", material, hashlib.sha256).hexdigest()
    headers = {
        "X-LedgerFlow-Event-ID": event_id,
        "X-LedgerFlow-Timestamp": timestamp,
        "X-LedgerFlow-Signature": "sha256=" + signature,
    }
    request_json(args.receiver_url + "/control/fail", {"fail": True})
    assert request_json(args.receiver_url + "/events", event, headers)[0] == 500
    request_json(args.receiver_url + "/control/fail", {"fail": False})
    assert request_json(args.receiver_url + "/events", event, headers)[1]["duplicate"] is False
    assert request_json(args.receiver_url + "/events", event, headers)[1]["duplicate"] is True
    print(
        json.dumps(
            {
                "real_client_timeout": timed_out,
                "durable_status_after_timeout": receipt["status"],
                "provider_replay_same_receipt": True,
                "signed_receiver_recovery_and_dedup": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
