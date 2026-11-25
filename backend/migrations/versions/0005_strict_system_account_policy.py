"""Reject SQL NULL policy outcomes for privileged system accounts."""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger_accounts DROP CONSTRAINT ck_ledger_account_policy")
    op.execute("""
        ALTER TABLE ledger_accounts ADD CONSTRAINT ck_ledger_account_policy CHECK (
          (kind IN ('USER_WALLET','MERCHANT_WALLET') AND owner_id IS NOT NULL
             AND system_key IS NULL AND NOT allow_overdraft) OR
          (kind = 'SYSTEM_FUNDING' AND owner_id IS NULL AND system_key IS NOT NULL
             AND system_key = 'sandbox_funding' AND allow_overdraft) OR
          (kind = 'SETTLEMENT' AND owner_id IS NULL AND system_key IS NOT NULL
             AND system_key = 'settlement' AND NOT allow_overdraft) OR
          (kind = 'SYSTEM' AND owner_id IS NULL
             AND system_key IS NOT NULL AND NOT allow_overdraft)
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Do not remove strict financial account policy protections.")
