"""Initial modular monolith schema and enforced financial invariants."""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical revisions must not import future ORM metadata or mutable SQL.
    directory = Path(__file__).resolve().parent
    for name in ("0001_schema.sql", "0001_ledger.sql"):
        for statement in (directory / name).read_text(encoding="utf-8").split("-- statement-break"):
            if statement.strip():
                op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Financial history is intentionally irreversible; restore a verified backup.")
