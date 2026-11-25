"""Remove redundant journal row lock from the sealed posting trigger.

Journal headers are immutable and postings require the creating transaction ID.
Reading that header needs no UPDATE privilege or SECURITY DEFINER function.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_apply_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner_txid bigint; journal_currency varchar; account_currency varchar;
    BEGIN
        SELECT created_txid, currency INTO owner_txid, journal_currency
          FROM journal_entries WHERE id = NEW.journal_id;
        IF owner_txid IS DISTINCT FROM txid_current() THEN
            RAISE EXCEPTION 'cannot append postings to a committed journal'
              USING ERRCODE = '23514';
        END IF;
        SELECT currency INTO account_currency FROM ledger_accounts WHERE id = NEW.account_id;
        IF account_currency IS DISTINCT FROM journal_currency THEN
            RAISE EXCEPTION 'posting currency mismatch' USING ERRCODE = '23514';
        END IF;
        UPDATE balance_projections SET balance_minor = balance_minor +
            CASE WHEN NEW.side = 'DEBIT' THEN NEW.amount_minor ELSE -NEW.amount_minor END
            WHERE account_id = NEW.account_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'account projection is missing' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """)


def downgrade() -> None:
    raise RuntimeError("Financial migrations are forward-only; restore a verified backup.")
