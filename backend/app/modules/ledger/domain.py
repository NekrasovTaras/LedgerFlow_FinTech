from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import AppError

MAX_AMOUNT_MINOR = 100_000_000_000


class Side(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass(frozen=True)
class PostingInstruction:
    account_id: str
    side: Side
    amount_minor: int


def validate_amount(amount: int) -> None:
    if type(amount) is not int or not 0 < amount <= MAX_AMOUNT_MINOR:
        raise AppError(
            "INVALID_AMOUNT",
            "Amount must be positive integer minor units within the sandbox limit.",
            422,
        )


def validate_journal(postings: Sequence[PostingInstruction]) -> None:
    if len(postings) < 2:
        raise AppError("UNBALANCED_JOURNAL", "At least two postings are required.")
    debit = credit = 0
    for posting in postings:
        validate_amount(posting.amount_minor)
        if posting.side == Side.DEBIT:
            debit += posting.amount_minor
        elif posting.side == Side.CREDIT:
            credit += posting.amount_minor
        else:
            raise AppError("INVALID_SIDE", "Posting side must be DEBIT or CREDIT.")
    if debit != credit:
        raise AppError("UNBALANCED_JOURNAL", "Debit and credit totals must match.")
