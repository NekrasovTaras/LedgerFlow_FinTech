from app.core.errors import AppError

TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"PENDING", "FAILED", "CANCELED"},
    "PENDING": {"AUTHORIZED", "REQUIRES_REVIEW", "FAILED", "CANCELED"},
    "REQUIRES_REVIEW": {"AUTHORIZED", "FAILED", "CANCELED"},
    "AUTHORIZED": {"PROCESSING", "CANCELED", "FAILED"},
    "PROCESSING": {"SUCCEEDED", "UNKNOWN", "FAILED"},
    "UNKNOWN": {"SUCCEEDED", "FAILED"},
    "SUCCEEDED": {"PARTIALLY_REFUNDED", "REFUNDED"},
    "PARTIALLY_REFUNDED": {"PARTIALLY_REFUNDED", "REFUNDED"},
    "FAILED": set(),
    "CANCELED": set(),
    "REFUNDED": set(),
}


def validate_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise AppError("INVALID_PAYMENT_STATE", f"Cannot transition {current} to {target}.", 409)


def risk_decision(
    amount_minor: int,
    recent_count: int,
    failures: int,
    new_recipient: bool,
    review_threshold: int,
    decline_threshold: int,
    velocity_limit: int,
) -> dict:
    score = 0
    rules: list[str] = []
    if amount_minor >= decline_threshold:
        score += 90
        rules.append("amount_limit")
    elif amount_minor >= review_threshold:
        score += 60
        rules.append("high_amount")
    if recent_count >= velocity_limit:
        score += 70
        rules.append("velocity")
    if failures >= 5:
        score += 45
        rules.append("consecutive_failures")
    if new_recipient and amount_minor >= review_threshold // 2:
        score += 15
        rules.append("new_recipient_unusual_amount")
    score = min(score, 100)
    return {
        "score": score,
        "rules": rules,
        "decision": "DECLINE" if score >= 85 else "REVIEW" if score >= 50 else "ALLOW",
        "explanation": ", ".join(rules) or "No risk rules triggered.",
        "rules_version": "2026-11-v1",
    }
