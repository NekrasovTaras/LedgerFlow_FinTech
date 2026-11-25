# Прогресс разработки

## 2026-11-25 — Метрики фоновых процессов и transaction retry

- **9 observability tests passed, 5.98s**: API `/metrics`, видимость committed/rollback фактов PostgreSQL, durable business counters, processing histogram, provider timeout/retry instrumentation и bounded labels. Evidence `artifacts/observability-tests.xml/.log`.
- Business metrics используют согласованный read-only PostgreSQL snapshot и видят работу worker/outbox/scheduler. Process-local HTTP histograms явно обозначены. Grafana дополнена processing/provider panels; ADR0004 фиксирует решение и ограничение O(history) scrape.
- **2 transaction recovery tests passed, 3.01s**: намеренный противоположный порядок row locks вызвал настоящий PostgreSQL `40P01`; вторая полная попытка создала один перевод, cached replay не создал повторного эффекта. Три явно инъецированных сервером `40001` исчерпали bounded retry без сохранённого idempotency result. Это два разных вида fault evidence, не заявления о естественном SSI-конфликте.
- Ruff63files/format и mypy44sourcefiles PASS. Новый backend image ещё должен быть пересобран после окончания headed E2E; итоговый coverage будет измерен повторно после новых source files.
- Chaos commit `4cd82ed` отправлен; его bootstrap GitHub Actions45642655960 success. Это по-прежнему отдельный baseline workflow, не новый product CI.

## 2026-11-25 — Общая backend-проверка и Chaos Lab

- **181 passed, 275.19s**, общий прогон всех текущих backend tests, включая все10 bounded Chaos experiments и PostgreSQL concurrency/property-based checks. `artifacts/backend-full-tests.xml`, `.log`, `backend-coverage.xml`.
- Измеренное branch coverage: **83% backend**, **90% ledger**, отдельный `coverage report --include='*/app/modules/ledger/*' --fail-under=90` exit0. Worker entrypoints в этом прогоне не покрыты; их реальный Compose запуск подтверждён отдельно. Поздние добавления требуют итогового повторного coverage-прогона.
- DB hardening commit **`a8d82da`** отправлен в `origin/feat/ledgerflow-build`. Без force push/merge.
- Chaos router/lifespan подключены; проверены реальные результаты, bounded duration, RBAC/sandbox, stale/shutdown recovery. Broker/Redis эксперименты разрывают собственное клиентское соединение, worker-before-ack разрывает consumer connection; остановка OS/container процесса не заявляется.
- Product headless E2E запущен на полном Compose. Первые финансовые сценарии подтвердили исправленный причинный порядок Timeline и immutable journal. Итоговый статус browser suite ещё ожидается.

## 2026-11-25 — Проверена защита счетов и полный Compose

- `ledgerflow` и `ledgerflow_test` последовательно обновлены до `0005`, без сброса существующих данных.
- DB review: **26 passed, 23.54s** — реальные restricted LOGIN-role probes и clean replay `0001 → 0005` в новой изолированной БД; повторный seed не дублирует пользователей/пополнения. Новая БД удалена после проверки, существующие volumes сохранены. Evidence: `artifacts/tests/database-role-migrations.xml` и `.log`.
- Подтверждены запреты overdraft пользовательского счёта, несовпадающего owner кошелька, изменения wallet identity и NULL-key системного funding/settlement счёта. Ruff check/format PASS; общий mypy PASS,43 source files.
- `docker compose build backend frontend` и `docker compose up -d` PASS. `migrate` exit0; API/UI/worker/scheduler healthy. Runtime SQL подтверждает `ledgerflow_app` без повышенных привилегий/DDL/TEMP. Outbox/inbox по6 записей; beat реально выполнил сверку: COMPLETED, инварианты true, расхождений0. Evidence: `artifacts/compose-*.log`, status/runtime-role JSON.
- Общий backend coverage-прогон продолжается; продуктовые E2E и нагрузка ждут завершения тестов с provider fault controls. Новый metrics review устраняет разрыв наблюдаемости между API и фоновыми процессами. Итоговый product CI ещё не заявляется успешным.

## 2026-11-25 — Текущая точка продолжения: интеграция Chaos и DB review

- Последний отправленный стабильный core: `81764f3`; baseline GitHub Actions45578522263 success. Продуктовый CI80%coverage пока не отправлен, его результаты не подменяются bootstrap-проверкой.
- Chaos Lab реализован:10реальных boundedэкспериментов,20/20тестов PASS28.20s, включая100/20 и shutdown/stale/RBAC. Router/lifespan уже подключены в рабочем дереве; требуется перезапуск runtime и UI/E2E.
- Database review:19permission/regressionтестов PASS под отдельной одноразовойLOGINролью; поздний набор20PASS добавилCREATE SCHEMA. Добавлены migration0004 account/walletpolicy и0005 strictNULLpolicy; их head/cleanreplay и новые регрессии требуют подтверждения после восстановления Docker.
- Initial migration0001 заморожена в version-local0001_schema.sql/0001_ledger.sql; не использует будущую ORMmetadata. Проверить replay на новой отдельной БД, не сбрасывая текущие volumes.
- Timeline/outbox/audit теперь получают фактический UTC timestamp при записи события, чтобы одинаковый transaction-start timestamp не менял причинный порядок Payment Inspector.
- Product Playwright:9реальныхсценариев+headedconfig готовы, ещё не запускались. Locust30s1→5→10users подготовлен; измерения отсутствуют.
- READMEEnglish/Russian,13interviewглав(55QA),runbooks готовы; финальные статусы/метрики ждут фактических проверок. Данные/изменения сохраняются, проект заново не создаётся.

## 2026-11-25 — Стабильный core checkpoint

- **Последний стабильный отправленный коммит: `81764f3`**, `feat(platform): implement authenticated financial core and dashboard`. Push в `origin/feat/ledgerflow-build` успешен. Незавершённый Chaos и итоговый product CI не включены в этот checkpoint.

- Общий выбранный прогон backend/auth/ledger/payments/authorization/events/operations/security/mock: **146 passed, 0 failed**,57.25s; `artifacts/core-checkpoint-tests.xml` и лог. Позднее добавленный targeted report-control regression отдельно входит в **9 passed** mock suite; результаты не суммируются как один запуск.
- Ruff core/tests/migrations/mock/infrastructure PASS; mypy38sourcefiles PASS. Незавершённый Chaos модуль исключён из core checkpoint и проверяется отдельно после интеграции.
- Frontend:28 tests/lint/typecheck/build PASS на Windows; чистый Linux `npm ci` и Docker frontend build PASS,0audit vulnerabilities. Optional npm lock зависимости восстановлены; версии манифеста сохранены, образы Node/Nginx закреплены digest.
- Docker Engine восстановлен,6coreinfrahealthy. Mock API дополнен адресной report-инъекцией по operation_id и чтением исходного receiver fail-control;9mocktests PASS.
- Runtime `ledgerflow_app` отделён от owner миграций. Фактические wallet/topup/hold/release/transfer проверены под runtime-role в rollback-транзакции; DDL/TEMP/TRUNCATE и изменение immutable journal/postings запрещены. Вызовы остаются SECURITY INVOKER.
- Security regression теперь проверяет запрет analyst resolve на реально существующем платеже, не только404для неизвестного UUID. Потоковый body limit, sandbox identities и provider-receipt validation также проходят общий прогон.
- Следующий шаг после commit/push этого блока: интегрировать10Chaosexperiments, запустить Compose worker/beat/API/UI целиком, UI/headed E2E, измерить coverage/Locust и завершить документацию. Продукт целиком ещё не готов.

## 2026-11-25 — Продолжение после лимитов: подтверждённые результаты

- Работа возобновлена с исходной ветки `feat/ledgerflow-build`; ранее созданные файлы сохранены, проект не пересоздавался. Последний отправленный коммит пока `3b599c6`; продуктовые изменения готовятся к первому checkpoint commit.
- Подтверждены сохранёнными JUnit: auth+ledger41 PASS; financial acceptance52 PASS; events23 PASS; operations11 PASS. Отдельный повтор auth/payments/security regression53 PASS35.43s (`artifacts/security-regression-tests.xml`). Это разные прогоны, их пересекающиеся тесты не суммируются в общий счёт.
- Frontend:28 unit/component PASS, lint/typecheck/build PASS на Windows. Чистая Linux/npm/Docker установка выявила optional-lock dependency issue; требуется подтвердить исправление Linux lockfile/build.
- PostgreSQL миграции0001–0003: ledgerconstraints, removal избыточного journal FOR SHARE, persistent sandbox user flag. Auth/current_user/refresh блокируют sandbox identities вне sandbox; startup с такими данными failclosed.
- Security review: исправлены analyst mutations, streaming1MiB input guard, provider receipt identity/amount/currency verification. TEMP-trigger обход projection guard подтверждён rollback-probe и закрыт nonsuperuser runtime role + REVOKE TEMP/CREATE; проверка фактической роли прошла.
- Outbox/inbox/Rabbit publisher confirms, leased retry, HMAC/webhook retry/DLQ и worker entrypoints реализованы и отдельно протестированы. Полная Compose интеграция runtime roles/worker/beat ещё требует финального прогона.
- При текущем возобновлении Docker Engine недоступен; запускается установленный Docker Desktop без изменения глобальных настроек.
- Следующий конкретный шаг: восстановить проектные контейнеры, пройти lint/mypy и релевантные регрессии, commit/push стабильного core-блока; затем завершить Chaos Lab10 сценариев, UI/E2E, coverage/benchmark и портфолио-документацию. Проект целиком не объявляется готовым.

## 2026-11-24 — Полное ТЗ принято, реализация начата

- Канонический текст: `docs/requirements/MASTER_SPEC.md`, 34 раздела. Ограничение «ТЗ ещё нет» снято; выбран заданный пользователем стек.
- Стабильная исходная ревизия: `3b599c6`, рабочая ветка `feat/ledgerflow-build`, рабочее дерево до начала чистое.
- Повторный preflight запущен; Python3.13 установлен наряду с системным3.14. Проект будет использовать3.13 через uv.
- Работа: root — backend/financial integration; Architect — ADR/API/invariants; DevOps — lock/Compose/mock services/CI; Frontend — только frontend/**.
- Следующая проверка: запуск PostgreSQL и миграций, строгие DB-инварианты, затем auth/ledger API тесты. Продукт пока не объявляется готовым.

## 2026-11-24 — Начальная подготовка

Полное техническое задание ещё не получено. Продуктовая реализация LedgerFlow не начата.

### Подтверждённые исходные факты

- Рабочая папка: `C:\Users\user\PycharmProjects\LedgerFlow_FinTech`.
- Локальный репозиторий и GitHub-репозиторий исходно не содержали коммитов; удалённых веток при проверке не было.
- Исходные файлы: `main.py` — шаблон PyCharm; `.idea/` — локальное состояние IDE, исключённое из Git.
- Git: `2.52.0.windows.1`.
- GitHub CLI: `2.105.0`, установлен по пути `C:\Program Files\GitHub CLI\gh.exe`; текущий PATH его не содержит. Авторизация аккаунта `NekrasovTaras` подтверждена без сохранения токенов в документации.
- Docker client/server: `29.0.1`; Engine отвечает.
- Docker Compose: `2.40.3-desktop.1`.
- Node.js: `v24.11.1`; npm: `11.6.2`; Python: `3.14.3`.
- Codex CLI: `0.172.0`; проектный формат `.codex/agents/*.toml` сверён с актуальным официальным руководством.
- `codex login status`: действующий вход через ChatGPT.
- PyCharm `2025.2.5`: окно проекта осмотрено через Computer Use.

### Подготовленные основы

- Созданы `.gitignore`, `.gitattributes`, `.editorconfig` и `AGENTS.md`.
- Создана рабочая ветка `feat/ledgerflow-build`.
- Созданы восемь профилей: Architect, Backend Engineer, Database Engineer, Frontend Engineer, QA Engineer, Security Engineer, DevOps Engineer, Technical Writer.
- В `.codex/config.toml` задан `agents.max_concurrent_threads_per_session = 3`: главный агент и максимум три субагента занимают четыре доступных слота.
- Реально выполнены задачи четырьмя специализированными субагентами: QA, DevOps, Technical Writer и Security Engineer. Одновременно работали максимум три; инструкции роли прочитаны из TOML. Нативный выбор сохранённой роли текущим инструментом запуска не подтверждён.
- `codex --strict-config app-server --print-startup-config` завершился с кодом `0`; это проверка принятия конфигурации, а не запуск или тест продукта.
- Все восемь TOML-профилей разобраны через Python `tomllib`; имена уникальны. `codex debug prompt-input` включает проектный `AGENTS.md` и все восемь имён профилей: обнаружение проектных инструкций и ролей клиентом подтверждено отдельно от их запуска.
- В `package.json` зафиксирован `@playwright/test` версии `1.65.1`; создан lockfile.

### Проверки интеграции — PASS

- `npm ci --ignore-scripts`: установлены 3 пакета, lockfile воспроизводим; `npm audit --audit-level=high`: 0 уязвимостей.
- `npm run browser:install`: Chromium 1255, headless shell, FFmpeg и Winldd установлены в проектный `.cache/ms-playwright/`.
- `npm run preflight`: все 17 проверок успешны; локальный отчёт `artifacts/preflight.json`.
- `npm run check`: 18 обязательных файлов, 8 профилей, lockfile и синтаксис 7 JavaScript-файлов проверены.
- `npm run test:e2e`: 4 passed, 0 unexpected, 0 skipped; итоговый прогон около 4,0 с.
- `npm run test:e2e:headed`: 4 passed, 0 unexpected, 0 skipped; итоговый прогон около 11,3 с, slowMo 600 мс.
- `npm run check:evidence` и `npm run check:evidence -- headed`: в каждом режиме 3 непустых PNG, 3 WebM, 4 trace и HTML/JSON-отчёты; все вложения находятся в каталоге своего режима.
- Отдельно проверены заголовки WebM и целостность ZIP всех восьми trace-архивов.
- Computer Use: открыт `AGENTS.md` в PyCharm, в видимом браузере открыт стенд `http://127.0.0.1:4173/`, проверено раскрытие сценария. Предпросмотр: `artifacts/bootstrap-preview.png`.
- Security Engineer не обнаружил блокирующих дефектов; две неточности инструкции просмотра отчёта и порта исправлены.

### Найденный и исправленный дефект QA

Первоначальные четыре теста проходили в обоих режимах, но проверка файлов выявила смешивание каталогов записей. Playwright workers повторно загружали конфигурацию с отличающимися аргументами процесса. Режим перенесён в наследуемую переменную `LEDGERFLOW_HEADED`; оба прогона повторены, записи разделены. Добавлена самостоятельная проверка `check:evidence` и её запуск в CI.

DevOps также встретил временный сбой проверки GitHub-auth; ограниченная диагностика и финальный preflight подтвердили действительную авторизацию. Неисправленные проверки не скрывались.

### Доставка — DONE

- Staged review: 37 файлов, локальные записи/зависимости/IDE исключены; поиск сигнатур секретов и проверка локальных Markdown-ссылок прошли. `git diff --cached --check` успешен.
- Первый коммит: [`3703946f869b2a152a7d16945b6b2383f02fefae`](https://github.com/NekrasovTaras/LedgerFlow_FinTech/commit/3703946f869b2a152a7d16945b6b2383f02fefae), `chore: bootstrap LedgerFlow autonomous development environment`.
- `git push --set-upstream origin feat/ledgerflow-build`: успешен; удалённая ветка указывает на тот же коммит.
- [GitHub Actions 45399174140](https://github.com/NekrasovTaras/LedgerFlow_FinTech/actions/runs/45399174140): `completed / success`. На Ubuntu прошли `npm ci`, статическая проверка, установка Chromium, четыре браузерных сценария, проверка записей и выгрузка артефактов.
- До начала работ история была пустой. GitHub назначил первую отправленную ветку `feat/ledgerflow-build` веткой по умолчанию. Ветка `main` не создавалась; merge и force push не выполнялись.
- Итоговые результаты сохранены также в `docs/reports/bootstrap-verification.md`; эта запись фиксируется отдельным документирующим коммитом после успешного первого CI. Актуальный HEAD смотрите через `git log -1`, чтобы не создавать самоссылочный хеш в файле.

### Состояние локального просмотра

Проверочный сервер оставлен для наблюдения на `http://127.0.0.1:4173/`; его процесс на момент завершения — PID `24160`, сессия запуска `89883`. Перед тестами остановить именно свой экземпляр сервера: Playwright не переиспользует занятый порт. PID может устареть — проверить команду и владельца перед остановкой. Браузерный стенд открыт, раскрытие сценария работает.

### Передача следующей сессии

M0 завершён. Следующий шаг — получить полное ТЗ и выполнить M1: требования, критерии приёмки, архитектура и конкретный план. Не начинать финансовые сценарии или Chaos Lab без ТЗ. Проверочный HTML-стенд подтверждает только инфраструктуру QA.
