# Активный план

## M0 — Подготовка к полному ТЗ

Этап завершён. Подтверждённые результаты и ссылка на первый успешный CI находятся в `PROGRESS.md`.

1. **DONE:** проверить Git, инструменты, Docker и GitHub; подтвердить отсутствие исходной истории локально и на GitHub.
2. **DONE:** создать `.gitignore`, `AGENTS.md`, рабочую ветку `feat/ledgerflow-build` и восемь проектных TOML-профилей Codex.
3. **DONE:** запустить субагентов с явным чтением профилей; распределить непересекающиеся области QA, DevOps и документации.
4. **DONE:** интегрировать постоянную документацию, preflight, CI и Playwright-стенд.
5. **DONE:** выполнить `npm run preflight`, `npm run check`, headless и headed Playwright; проверить артефакты в обоих режимах.
6. **DONE:** показать `AGENTS.md` в PyCharm и фактическую браузерную проверку через Computer Use.
7. **DONE:** проверить staged diff и отсутствие секретов/генерируемых файлов, создать Conventional Commit и push.
8. **DONE:** записать реальные результаты, ссылку на commit, успешный GitHub Actions и ограничения в `PROGRESS.md`.

Полное ТЗ получено и сохранено в `docs/requirements/MASTER_SPEC.md`; подготовка M0 не повторяется.

## Исполняемый план полного продукта

1. **DONE / Phase 0:** ТЗ/API-контракты, Python3.13/uv lock, полный Compose и миграции0001–0005 проверены. Clean replay и repeatable seed подтверждены. Core и последующие проверенные блоки отправлены в рабочую ветку.
2. **DONE / Phase 1:** Argon2id/JWT, HttpOnly refresh rotation/revocation/sessions, login throttling, RBAC/object authorization; API/security regressions PASS.
3. **DONE / Phase 2:** ledger/wallets/topup/holds, DB constraints/triggers и финансовые инварианты. Настоящий PostgreSQL, Hypothesis и запреты изменения/поздних проводок подтверждены тестами.
4. **DONE / Phase 3:** P2P/payments/idempotency/refunds; 100 одинаковых HTTP-команд→один эффект, 20 списаний→10/10, встречные переводы и refund/cancel/capture races PASS.
5. **DONE / Phases 4–5:** outbox confirms/inbox, Celery/scheduler, HMAC/retry/DLQ, providers/risk/UNKNOWN проверены. Полный Compose подтвердил worker/beat, runtime role, scheduled reconciliation и outbox/inbox. UI проверяет UNKNOWN recovery через исходного provider.
6. **DONE / Phases 6–7:** сверка/CSV, audit/timeline, все10 bounded Chaos experiments, RBAC, shutdown/stale recovery и инварианты проходят общий backend suite. Scope клиентских disconnect указан в UI/runbook.
7. **IN PROGRESS / Phase 8:** React13screens/unit28/lint/typecheck/build PASS; real product headless9PASS, headed acceptance завершается. Учесть реальный scheduler при проверке UNKNOWN. Проверить9PNG/WebM/trace в каждом режиме.
8. **IN PROGRESS / Phase 9:** logs/correlation/OTel/Grafana и committed cross-process metrics реализованы; backend181PASS, coverage83%/ledger90%, DB26PASS, metrics9PASS, recovery2PASS. Далее: после headed обновить runtime image, bounded Locust30s1→5→10users, затем итоговый полный coverage и product CI. Fault tests не запускать параллельно load/E2E.
9. **IN PROGRESS / Phase 10:** English/Russian README, diagrams/runbooks,13 interview documents и55QA подготовлены. Сверить факты с итоговыми проверками, commit/push, дождаться product CI, подготовить PR без merge при доступной basebranch.

Зависимости: финансовые шаги2–6 выполняются последовательно через утверждённые DB/API-контракты; infra/frontend независимы. У каждого файла один владелец, root управляет интеграцией и Git. Каждый завершённый блок проверяется, документируется, commit/push; затем работа продолжается без запроса обычных подтверждений.

## Правила исполнения

- Главный агент отвечает за интеграцию, общие конфигурации и Git.
- У каждого изменяемого файла один назначенный исполнитель; независимые задачи можно выполнять параллельно.
- В текущей среде работают максимум три субагента одновременно плюс главный агент.
- Завершённый блок проходит уместные проверки до commit/push.
- Ошибка проверки остаётся ошибкой до исправления и подтверждённого повторного запуска.

## Возобновление в новой сессии

1. Прочитать `AGENTS.md`, `README.md`, `GOALS.md`, `ROADMAP.md`, этот файл и `PROGRESS.md`.
2. Проверить `git status --short --branch` и `git log -8 --oneline`; до первого commit отсутствие истории ожидаемо.
3. Сверить последний завершённый этап и фактические команды в `PROGRESS.md`.
4. Проверить наличие новых инструкций пользователя и ТЗ; сохранить существующие изменения.
5. Продолжить первый незавершённый пункт. При изменении среды повторить preflight.
6. После значимого результата обновить план, прогресс и changelog.
