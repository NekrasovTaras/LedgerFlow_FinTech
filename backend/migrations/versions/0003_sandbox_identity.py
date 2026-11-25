"""Persist sandbox identity restrictions across environment changes."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_sandbox boolean NOT NULL DEFAULT false")
    op.execute("""
        UPDATE users SET is_sandbox = true
        WHERE email IN (
          'admin@ledgerflow.local', 'customer@ledgerflow.local', 'merchant@ledgerflow.local',
          'analyst@ledgerflow.local', 'operator@ledgerflow.local'
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Do not remove persistent sandbox identity protections.")
