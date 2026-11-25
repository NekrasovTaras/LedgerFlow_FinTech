# История изменений

Все записи описывают изменения проекта. Фактические результаты проверок, commit/push и текущий этап хранятся в `PROGRESS.md`.

## Unreleased

### Database hardening

- Froze the initial schema and financial trigger migration in version-local SQL assets so future ORM edits cannot change historical replay.
- Enforced account funding/overdraft policy, wallet-to-account owner/currency binding and immutable wallet identity, including explicit SQL NULL rejection.
- Added real restricted-role and clean migration/seed replay regressions.

### Product core

- Added FastAPI authentication, rotating/revocable sessions, PostgreSQL RBAC and object authorization.
- Added immutable double-entry RUB journal, trigger-maintained projections, holds, P2P transfers, durable idempotency, concurrent payments and compensating refunds.
- Added local providers, safe UNKNOWN reconciliation, risk review, transactional outbox/inbox, signed webhook retries and dead-letter handling.
- Added operational API, reconciliation CSV, React dashboard, reproducible locked dependencies and project-scoped Docker services.
- Fixed security findings covering analyst mutations, streamed request limits, sandbox identities, receipt validation and database temporary-trigger privileges.

### Добавлено

- Проектные инструкции автономной разработки, восемь TOML-профилей агентов Codex и ограничение параллелизма.
- Правила исключения локального состояния IDE, зависимостей, секретов и генерируемых артефактов из Git.
- Постоянные цели, дорожная карта, план, журнал прогресса и инструкции возобновления работы.
- Повторяемый preflight и проверки bootstrap-инструментов.
- Playwright-стенд с headless и headed режимами, замедлением действий и сохранением доказательств запуска.
- Зафиксированная QA-зависимость `@playwright/test` версии `1.65.1` и lockfile.
- GitHub Actions для проверок рабочей ветки.
- Проверка наличия записей и изоляции headed/headless-артефактов; исправлена передача режима в Playwright workers.

Начальный проверочный стенд остаётся отдельной bootstrap-проверкой и не считается продуктовой функциональностью.
