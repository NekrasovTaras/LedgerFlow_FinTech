export interface ListResponse<T> { items: T[]; total: number; page: number; page_size: number }
export interface User { id: string; email: string; display_name: string; role: string }
export interface SessionResponse { access_token: string; token_type: string; user: User }
export interface Wallet { id: string; name: string; currency: string; status: string; available_minor: number; reserved_minor: number; posted_minor: number; owner_id: string }
export interface Hold { id: string; account_id: string; amount_minor: number; status: string; created_at: string; reference_id: string }
export interface Payment { id: string; wallet_id: string; merchant_wallet_id: string; amount_minor: number; currency: string; status: string; description: string; provider: string | null; provider_reference: string | null; risk_score: number | null; risk_decision: string | null; refunded_minor: number; created_at: string; version: number }
export interface Transfer { id: string; source_wallet_id: string; destination_wallet_id: string; amount_minor: number; currency: string; description: string; status?: string; created_at: string }
export interface Posting { account_id: string; side: string; amount_minor: number }
export interface Journal { id: string; kind: string; reference_id: string | null; created_at: string; postings: Posting[]; description: string }
export interface Account { id: string; name: string; kind: string; currency: string; balance_minor: number; owner_id: string | null }
export interface TimelineEvent { id: string; kind: string; title: string; detail: string; status: string; created_at: string; correlation_id: string | null; trace_id: string | null; data: Record<string, unknown> }
export interface RiskReview { id: string; payment_id: string; score: number; rules: string[]; explanation: string; status: string; created_at: string }
export interface Provider { name: string; status: string; available: boolean; fee_bps: number; circuit_state: string; success_rate: number; latency_ms: number }
export interface Delivery { id: string; event_id: string; event_type: string; status: string; attempts: number; last_error: string | null; created_at: string }
export interface Subscription { id: string; receiver: string; event_types: string[]; active?: boolean; created_at?: string }
export interface OutboxEvent { id: string; event_type: string; status: string; attempts: number; created_at: string; last_error: string | null }
export interface ReconciliationRun { id: string; status: string; discrepancy_count?: number; discrepancies?: Discrepancy[]; created_at: string; completed_at?: string; summary?: Record<string, unknown> }
export interface Discrepancy { id?: string; classification?: string; reference_id?: string; detail?: string; kind?: string; type?: string; entity_id?: string; description?: string; expected?: unknown; actual?: unknown; recommendation?: string }
export interface ChaosScenario { id: string; name: string; description: string; expected_behavior: string; recovery: string }
export interface Experiment { id: string; scenario_id: string; status: string; expected_behavior: string; result: Record<string, unknown> | string; invariants: Record<string, unknown> | boolean; timeline: Array<Record<string, unknown>>; recovery: string; created_at: string }
export interface Health { status: string; services: Record<string, unknown>; sandbox_mode: boolean }
export interface Settings { sandbox_mode: boolean; currency: string; risk_rules: unknown; limits: unknown }
export interface Session { id: string; created_at: string; expires_at: string; revoked_at?: string | null; current?: boolean }
export interface Dashboard { available_minor: number; reserved_minor: number; total_payments: number; succeeded_payments: number; failed_payments: number; pending_payments: number; outbox_pending: number; webhook_failures: number; reconciliation_discrepancies: number; recent_payments: Payment[]; volume_series: Array<{ date: string; amount_minor: number; count: number }> }
