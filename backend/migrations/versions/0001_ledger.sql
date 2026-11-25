CREATE FUNCTION ledger_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger history is immutable' USING ERRCODE = '23514';
END $$;
-- statement-break
CREATE TRIGGER journal_immutable BEFORE UPDATE OR DELETE ON journal_entries
FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER posting_immutable BEFORE UPDATE OR DELETE ON postings
FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER account_immutable BEFORE UPDATE OR DELETE ON ledger_accounts
FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER timeline_immutable BEFORE UPDATE OR DELETE ON timeline_events
FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER journal_no_truncate BEFORE TRUNCATE ON journal_entries
FOR EACH STATEMENT EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE TRIGGER posting_no_truncate BEFORE TRUNCATE ON postings
FOR EACH STATEMENT EXECUTE FUNCTION ledger_reject_mutation();
-- statement-break
CREATE FUNCTION ledger_stamp_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.created_txid := txid_current();
    RETURN NEW;
END $$;
-- statement-break
CREATE TRIGGER journal_stamp BEFORE INSERT ON journal_entries
FOR EACH ROW EXECUTE FUNCTION ledger_stamp_transaction();
-- statement-break
CREATE FUNCTION ledger_projection_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE overdraft boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT allow_overdraft INTO overdraft FROM ledger_accounts WHERE id = NEW.account_id;
        IF NEW.balance_minor != 0 OR NEW.reserved_minor != 0 OR NEW.allow_overdraft IS DISTINCT FROM overdraft THEN
            RAISE EXCEPTION 'projection must start at zero with account policy' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_OP = 'DELETE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'projection is maintained exclusively by ledger triggers' USING ERRCODE = '23514';
    ELSIF NEW.account_id IS DISTINCT FROM OLD.account_id OR NEW.allow_overdraft IS DISTINCT FROM OLD.allow_overdraft THEN
        RAISE EXCEPTION 'projection identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF abs(NEW.balance_minor::numeric) > 9007199254740991 OR NEW.reserved_minor > 9007199254740991 THEN
        RAISE EXCEPTION 'balance exceeds JSON safe integer range' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
-- statement-break
CREATE TRIGGER projection_guard BEFORE INSERT OR UPDATE OR DELETE ON balance_projections
FOR EACH ROW EXECUTE FUNCTION ledger_projection_guard();
-- statement-break
CREATE FUNCTION ledger_apply_posting() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_txid bigint; journal_currency varchar; account_currency varchar;
BEGIN
    SELECT created_txid, currency INTO owner_txid, journal_currency FROM journal_entries WHERE id = NEW.journal_id;
    IF owner_txid IS DISTINCT FROM txid_current() THEN
        RAISE EXCEPTION 'cannot append postings to a committed journal' USING ERRCODE = '23514';
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
-- statement-break
CREATE TRIGGER posting_projection AFTER INSERT ON postings
FOR EACH ROW EXECUTE FUNCTION ledger_apply_posting();
-- statement-break
CREATE FUNCTION ledger_check_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE entry_id uuid; difference numeric; posting_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'journal_entries' THEN entry_id := NEW.id;
    ELSE entry_id := NEW.journal_id; END IF;
    SELECT count(*), coalesce(sum(CASE WHEN side = 'DEBIT' THEN amount_minor::numeric ELSE -amount_minor::numeric END), 0)
      INTO posting_count, difference FROM postings WHERE journal_id = entry_id;
    IF posting_count < 2 OR difference != 0 THEN
        RAISE EXCEPTION 'journal must have at least two balanced postings' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
-- statement-break
CREATE CONSTRAINT TRIGGER journal_balanced AFTER INSERT ON journal_entries
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_balance();
-- statement-break
CREATE CONSTRAINT TRIGGER posting_balanced AFTER INSERT ON postings
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_balance();
-- statement-break
CREATE FUNCTION ledger_apply_hold() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'hold history cannot be deleted' USING ERRCODE = '23514';
    ELSIF TG_OP = 'INSERT' THEN
        IF NEW.status != 'ACTIVE' THEN
            RAISE EXCEPTION 'new hold must be ACTIVE' USING ERRCODE = '23514';
        END IF;
        UPDATE balance_projections SET reserved_minor = reserved_minor + NEW.amount_minor WHERE account_id = NEW.account_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'hold projection is missing' USING ERRCODE = '23514'; END IF;
    ELSE
        IF OLD.status != 'ACTIVE' OR NEW.status NOT IN ('CAPTURED','RELEASED')
           OR NEW.amount_minor != OLD.amount_minor OR NEW.account_id != OLD.account_id
           OR NEW.reference_id != OLD.reference_id OR NEW.id != OLD.id THEN
            RAISE EXCEPTION 'invalid or repeated hold transition' USING ERRCODE = '23514';
        END IF;
        UPDATE balance_projections SET reserved_minor = reserved_minor - OLD.amount_minor WHERE account_id = OLD.account_id;
    END IF;
    RETURN NEW;
END $$;
-- statement-break
CREATE TRIGGER hold_projection AFTER INSERT OR UPDATE OR DELETE ON holds
FOR EACH ROW EXECUTE FUNCTION ledger_apply_hold();
