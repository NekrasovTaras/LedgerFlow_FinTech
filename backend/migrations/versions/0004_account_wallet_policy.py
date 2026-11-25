"""Enforce wallet ownership, non-overdraft account policy and immutable identity."""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ledger_accounts ADD CONSTRAINT uq_ledger_account_identity
        UNIQUE (id, owner_id, currency)
    """)
    op.execute("""
        ALTER TABLE ledger_accounts ADD CONSTRAINT ck_ledger_account_policy CHECK (
          (kind IN ('USER_WALLET','MERCHANT_WALLET') AND owner_id IS NOT NULL
             AND system_key IS NULL AND NOT allow_overdraft) OR
          (kind = 'SYSTEM_FUNDING' AND owner_id IS NULL
             AND system_key = 'sandbox_funding' AND allow_overdraft) OR
          (kind = 'SETTLEMENT' AND owner_id IS NULL
             AND system_key = 'settlement' AND NOT allow_overdraft) OR
          (kind = 'SYSTEM' AND owner_id IS NULL
             AND system_key IS NOT NULL AND NOT allow_overdraft)
        )
    """)
    op.execute("""
        ALTER TABLE wallets ADD CONSTRAINT fk_wallet_account_identity
        FOREIGN KEY (account_id, owner_id, currency)
        REFERENCES ledger_accounts (id, owner_id, currency)
    """)
    op.execute("""
        CREATE FUNCTION ledger_wallet_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.account_id IS DISTINCT FROM OLD.account_id
               OR NEW.currency IS DISTINCT FROM OLD.currency THEN
                RAISE EXCEPTION 'wallet financial identity is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER wallet_identity_guard BEFORE UPDATE ON wallets
        FOR EACH ROW EXECUTE FUNCTION ledger_wallet_identity_guard()
    """)


def downgrade() -> None:
    raise RuntimeError("Do not remove financial account policy protections.")
