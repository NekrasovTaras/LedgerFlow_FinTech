# Core security review — 2026-11-25

Область: текущий sandbox backend, SQL triggers/runtime privileges, auth, providers, outbox/webhooks и UI permissions. Это инженерная проверка локального демонстрационного проекта, не сертификация безопасности.

## Исправленные подтверждённые дефекты

1. **P1 — nested projection update через временный trigger.** Read-only Security Engineer воспроизвёл в rollback-транзакции `ledgerflow_test`: NOSUPERUSER с UPDATE-проекции мог создать TEMP table/function/trigger и пройти depth-guard. Вся probe-транзакция откатилась. Runtime role теперь не имеет TEMP/CREATE/DDL/TRUNCATE; фактический CREATE TEMP под `ledgerflow_app` отклоняется42501. Journal не требует UPDATE или SECURITY DEFINER: creator-txid seal позволяет читать immutable header без избыточного FOR SHARE.
2. **P1 — analyst financial mutations.** Read permissions раньше использовались в cancel/refund/manual hold release/resolve. Добавлены FINANCIAL_ACTORS и отдельный object mutation guard. Проверки включают существующий payment и подтверждают403 для analyst; financial acceptance покрывает остальные пути.
3. **P2 — streamed body bypass.** Ограничение по Content-Length допускало chunked тело. ASGI middleware теперь считает реальные байты до JSON parsing; oversized chunked registration отвергается413 без отражения password/input. Отдельная bounded probe и regression подтверждены.
4. **P2 — sandbox identities при смене режима.** Добавлены persisted `User.is_sandbox`, migration0003/backfill, проверки login/refresh/access и fail-closed startup с sandbox identities в non-sandbox БД. Действующие sandbox sessions также блокируются.
5. **P2 — неподходящая receipt провайдера.** HTTP200/status не гарантирует соответствие платежу. Строгая схема теперь сверяет operation_id, integer amount, RUB и reference; mismatch означает UNKNOWN без capture/release/fallback. Проверены submit и lookup, включая ошибочные ID/amount/currency.

## Проверенные границы

- Argon2id, ограниченные JWT algorithms/issuer/required claims, opaque refresh digests, rotation/replay-family revocation и persistent login throttling.
- Webhook receiver задаётся конфигурацией, пользователь выбирает только `mock`; HMAC/timestamp, запрет redirects, retries/DLQ, owner filtering и unique inbox/delivery effects.
- Внешний HTTP не выполняется внутри финансовой транзакции. Неопределённый результат сохраняет hold; fallback требует подтверждённого отсутствия эффекта.
- Данные probe не сохранялись; личные секреты и внешние endpoint не использовались.

## Доказательства и остаточная работа

`artifacts/core-checkpoint-tests.xml`:146PASS; `financial-acceptance-final.xml`:52PASS; `events-tests.xml`:23PASS; отдельный auth/payments/security прогон53PASS. Прогоны пересекаются и не суммируются. Проверка SQL permissions выполнена DevOps/Security через actual runtime role и rollback-only probes.

Предстоит проверить итоговый Compose lifecycle,10Chaosexperiments, полные UI/E2E, measured coverage и нагрузку. Новые изменения требуют повторного целевого review. Общая готовность продукта пока не заявляется.
