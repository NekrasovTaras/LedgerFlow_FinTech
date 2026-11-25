# Прогресс разработки

## 2026-11-25 — Стабильный core checkpoint

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
