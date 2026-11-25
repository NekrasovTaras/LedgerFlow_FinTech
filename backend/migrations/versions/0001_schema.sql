-- Frozen initial published schema; change it only through subsequent revisions.
CREATE TABLE inbox_events (
	event_id UUID NOT NULL,
	consumer VARCHAR(60) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_inbox_consumer UNIQUE (event_id, consumer)
);
-- statement-break
CREATE TABLE journal_entries (
	kind VARCHAR(40) NOT NULL,
	reference_id VARCHAR(100) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	description VARCHAR(300) NOT NULL,
	created_txid BIGINT DEFAULT txid_current() NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (currency = 'RUB'),
	UNIQUE (reference_id)
);
-- statement-break
CREATE TABLE login_attempts (
	subject_hash VARCHAR(64) NOT NULL,
	succeeded BOOLEAN NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);
-- statement-break
CREATE TABLE outbox_events (
	event_type VARCHAR(80) NOT NULL,
	payload JSONB NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempts INTEGER NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	locked_until TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(500),
	published_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);
-- statement-break
CREATE TABLE provider_circuits (
	name VARCHAR(30) NOT NULL,
	failures INTEGER NOT NULL,
	opened_until TIMESTAMP WITH TIME ZONE,
	total_requests INTEGER NOT NULL,
	successes INTEGER NOT NULL,
	last_latency_ms INTEGER NOT NULL,
	PRIMARY KEY (name)
);
-- statement-break
CREATE TABLE users (
	email VARCHAR(254) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	password_hash TEXT NOT NULL,
	role VARCHAR(30) NOT NULL,
	active BOOLEAN NOT NULL,
	is_sandbox BOOLEAN DEFAULT 'false' NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (role IN ('customer','merchant','risk_analyst','operator','administrator')),
	UNIQUE (email)
);
-- statement-break
CREATE TABLE audit_events (
	actor_id UUID,
	action VARCHAR(100) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	correlation_id VARCHAR(80) NOT NULL,
	data JSONB NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE auth_sessions (
	user_id UUID NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	family_id UUID NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	replaced_by UUID,
	user_agent VARCHAR(200) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (token_hash)
);
-- statement-break
CREATE TABLE chaos_experiments (
	actor_id UUID NOT NULL,
	scenario_id VARCHAR(60) NOT NULL,
	status VARCHAR(20) NOT NULL,
	expected_behavior TEXT NOT NULL,
	recovery TEXT NOT NULL,
	result JSONB NOT NULL,
	invariants JSONB NOT NULL,
	timeline JSONB NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE idempotency_records (
	actor_id UUID NOT NULL,
	operation VARCHAR(160) NOT NULL,
	key VARCHAR(200) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	response JSONB,
	status_code INTEGER,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_idempotency_scope UNIQUE (actor_id, operation, key),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE ledger_accounts (
	name VARCHAR(120) NOT NULL,
	owner_id UUID,
	kind VARCHAR(40) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	allow_overdraft BOOLEAN NOT NULL,
	system_key VARCHAR(60),
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (currency = 'RUB'),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (system_key)
);
-- statement-break
CREATE TABLE reconciliation_runs (
	actor_id UUID,
	status VARCHAR(20) NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	summary JSONB NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE webhook_subscriptions (
	owner_id UUID NOT NULL,
	receiver VARCHAR(30) NOT NULL,
	event_types JSONB NOT NULL,
	active BOOLEAN NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_webhook_subscription UNIQUE (owner_id, receiver),
	CHECK (receiver = 'mock'),
	FOREIGN KEY(owner_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE balance_projections (
	account_id UUID NOT NULL,
	balance_minor BIGINT DEFAULT '0' NOT NULL,
	reserved_minor BIGINT DEFAULT '0' NOT NULL,
	allow_overdraft BOOLEAN NOT NULL,
	PRIMARY KEY (account_id),
	CONSTRAINT reserved_nonnegative CHECK (reserved_minor >= 0),
	CONSTRAINT available_nonnegative CHECK (allow_overdraft OR balance_minor >= reserved_minor),
	FOREIGN KEY(account_id) REFERENCES ledger_accounts (id)
);
-- statement-break
CREATE TABLE discrepancies (
	run_id UUID NOT NULL,
	classification VARCHAR(60) NOT NULL,
	reference_id VARCHAR(100) NOT NULL,
	detail TEXT NOT NULL,
	recommendation TEXT NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES reconciliation_runs (id)
);
-- statement-break
CREATE TABLE holds (
	account_id UUID NOT NULL,
	amount_minor BIGINT NOT NULL,
	status VARCHAR(20) NOT NULL,
	reference_id VARCHAR(100) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount_minor > 0),
	CHECK (status IN ('ACTIVE','CAPTURED','RELEASED')),
	FOREIGN KEY(account_id) REFERENCES ledger_accounts (id),
	UNIQUE (reference_id)
);
-- statement-break
CREATE TABLE postings (
	journal_id UUID NOT NULL,
	account_id UUID NOT NULL,
	side VARCHAR(6) NOT NULL,
	amount_minor BIGINT NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount_minor > 0),
	CHECK (side IN ('DEBIT','CREDIT')),
	FOREIGN KEY(journal_id) REFERENCES journal_entries (id),
	FOREIGN KEY(account_id) REFERENCES ledger_accounts (id)
);
-- statement-break
CREATE TABLE wallets (
	owner_id UUID NOT NULL,
	account_id UUID NOT NULL,
	name VARCHAR(100) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	status VARCHAR(20) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (currency = 'RUB'),
	CHECK (status IN ('ACTIVE','BLOCKED')),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (account_id),
	FOREIGN KEY(account_id) REFERENCES ledger_accounts (id)
);
-- statement-break
CREATE TABLE webhook_deliveries (
	event_id UUID NOT NULL,
	subscription_id UUID NOT NULL,
	event_type VARCHAR(80) NOT NULL,
	status VARCHAR(20) NOT NULL,
	attempts INTEGER NOT NULL,
	last_error VARCHAR(500),
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_webhook_event_subscription UNIQUE (event_id, subscription_id),
	FOREIGN KEY(event_id) REFERENCES outbox_events (id),
	FOREIGN KEY(subscription_id) REFERENCES webhook_subscriptions (id)
);
-- statement-break
CREATE TABLE payments (
	wallet_id UUID NOT NULL,
	merchant_wallet_id UUID NOT NULL,
	amount_minor BIGINT NOT NULL,
	currency VARCHAR(3) NOT NULL,
	description VARCHAR(300) NOT NULL,
	status VARCHAR(30) NOT NULL,
	hold_id UUID,
	provider VARCHAR(30),
	provider_reference VARCHAR(100),
	provider_behavior VARCHAR(30) NOT NULL,
	risk_score INTEGER NOT NULL,
	risk_decision VARCHAR(20) NOT NULL,
	refunded_minor BIGINT NOT NULL,
	version INTEGER NOT NULL,
	processing_started_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount_minor > 0),
	CHECK (refunded_minor >= 0 AND refunded_minor <= amount_minor),
	CHECK (status IN ('CREATED','PENDING','REQUIRES_REVIEW','AUTHORIZED','PROCESSING','UNKNOWN','SUCCEEDED','FAILED','CANCELED','PARTIALLY_REFUNDED','REFUNDED')),
	FOREIGN KEY(wallet_id) REFERENCES wallets (id),
	FOREIGN KEY(merchant_wallet_id) REFERENCES wallets (id),
	FOREIGN KEY(hold_id) REFERENCES holds (id)
);
-- statement-break
CREATE TABLE transfers (
	source_wallet_id UUID NOT NULL,
	destination_wallet_id UUID NOT NULL,
	amount_minor BIGINT NOT NULL,
	currency VARCHAR(3) NOT NULL,
	description VARCHAR(300) NOT NULL,
	journal_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount_minor > 0),
	FOREIGN KEY(source_wallet_id) REFERENCES wallets (id),
	FOREIGN KEY(destination_wallet_id) REFERENCES wallets (id),
	FOREIGN KEY(journal_id) REFERENCES journal_entries (id)
);
-- statement-break
CREATE TABLE refunds (
	payment_id UUID NOT NULL,
	amount_minor BIGINT NOT NULL,
	journal_id UUID NOT NULL,
	status VARCHAR(20) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount_minor > 0),
	FOREIGN KEY(payment_id) REFERENCES payments (id),
	FOREIGN KEY(journal_id) REFERENCES journal_entries (id)
);
-- statement-break
CREATE TABLE risk_reviews (
	payment_id UUID NOT NULL,
	score INTEGER NOT NULL,
	rules JSONB NOT NULL,
	explanation TEXT NOT NULL,
	rules_version VARCHAR(30) NOT NULL,
	status VARCHAR(20) NOT NULL,
	reviewer_id UUID,
	reason VARCHAR(500),
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (payment_id),
	FOREIGN KEY(payment_id) REFERENCES payments (id),
	FOREIGN KEY(reviewer_id) REFERENCES users (id)
);
-- statement-break
CREATE TABLE timeline_events (
	payment_id UUID,
	kind VARCHAR(80) NOT NULL,
	title VARCHAR(160) NOT NULL,
	detail TEXT NOT NULL,
	status VARCHAR(40) NOT NULL,
	correlation_id VARCHAR(80) NOT NULL,
	trace_id VARCHAR(32) NOT NULL,
	data JSONB NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(payment_id) REFERENCES payments (id)
);
-- statement-break
CREATE INDEX ix_journal_entries_kind ON journal_entries (kind);
-- statement-break
CREATE INDEX ix_login_attempts_subject_hash ON login_attempts (subject_hash);
-- statement-break
CREATE INDEX ix_outbox_events_event_type ON outbox_events (event_type);
-- statement-break
CREATE INDEX ix_outbox_events_status ON outbox_events (status);
-- statement-break
CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);
-- statement-break
CREATE INDEX ix_auth_sessions_family_id ON auth_sessions (family_id);
-- statement-break
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);
-- statement-break
CREATE INDEX ix_ledger_accounts_owner_id ON ledger_accounts (owner_id);
-- statement-break
CREATE INDEX ix_webhook_subscriptions_owner_id ON webhook_subscriptions (owner_id);
-- statement-break
CREATE INDEX ix_discrepancies_run_id ON discrepancies (run_id);
-- statement-break
CREATE INDEX ix_holds_account_id ON holds (account_id);
-- statement-break
CREATE INDEX ix_postings_account_id ON postings (account_id);
-- statement-break
CREATE INDEX ix_postings_journal_id ON postings (journal_id);
-- statement-break
CREATE INDEX ix_wallets_owner_id ON wallets (owner_id);
-- statement-break
CREATE INDEX ix_webhook_deliveries_status ON webhook_deliveries (status);
-- statement-break
CREATE INDEX ix_payments_status ON payments (status);
-- statement-break
CREATE INDEX ix_payments_wallet_created ON payments (wallet_id, created_at);
-- statement-break
CREATE INDEX ix_payments_wallet_id ON payments (wallet_id);
-- statement-break
CREATE INDEX ix_transfers_destination_wallet_id ON transfers (destination_wallet_id);
-- statement-break
CREATE INDEX ix_transfers_source_wallet_id ON transfers (source_wallet_id);
-- statement-break
CREATE INDEX ix_refunds_payment_id ON refunds (payment_id);
-- statement-break
CREATE INDEX ix_timeline_events_payment_id ON timeline_events (payment_id);
