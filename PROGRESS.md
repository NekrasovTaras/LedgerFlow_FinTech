# Прогресс разработки

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

### Доставка — IN PROGRESS

Локальная интеграция проверена. Следующий шаг: staged review, первый Conventional Commit, push и проверка GitHub Actions. Результат доставки будет добавлен отдельной записью после фактического завершения.

### Передача следующей сессии

До окончания доставки продолжить M0 по `PLANS.md`. Затем получить полное ТЗ перед выбором продуктового стека и реализацией финансовых сценариев или Chaos Lab. Проверочный HTML-стенд подтверждает только инфраструктуру QA.
