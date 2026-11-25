"""Initial modular monolith schema and enforced financial invariants."""
from pathlib import Path

from alembic import op

from app.infrastructure.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())
    source = Path(__file__).resolve().parents[2] / "app/infrastructure/ledger_constraints.sql"
    for statement in source.read_text(encoding="utf-8").split("-- statement-break"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Financial history is intentionally irreversible; restore a verified backup.")
