# Локальное окружение LedgerFlow

## Запуск и остановка

Из корня проекта:

```powershell
pwsh -File scripts/start.ps1
pwsh -File scripts/start.ps1 -Monitoring
pwsh -File scripts/stop.ps1
```

Linux/macOS:

```sh
sh scripts/start.sh
sh scripts/start.sh --monitoring
docker compose --project-name ledgerflow --file compose.yaml --profile monitoring down
```

Скрипты явно выбирают проект `ledgerflow`, ожидают health checks и сохраняют
постоянные volumes при остановке. Не используйте `down --volumes`, `docker prune`
или команды удаления общих ресурсов. Файл `.env.example` содержит только
публичные синтетические значения; `.env` исключён из Git.

UI: `http://localhost:5173`; API: `http://localhost:8000/docs`.
Мониторинг: Prometheus `9090`, Grafana `3000`, OTLP/HTTP `4318`.
Все опубликованные порты привязаны к `127.0.0.1`.

## Роли PostgreSQL

Одноразовый сервис `migrate` использует владельца `ledgerflow`: применяет
миграции, создаёт sandbox-данные и запускает
`infrastructure/postgres/runtime_role.py`. Приложение, worker, scheduler и outbox
используют отдельный `ledgerflow_app`; URL владельца им не передаётся.

Runtime-роль не является superuser, не создаёт роли/БД/схемы/временные объекты,
не владеет таблицами, не отключает триггеры и не имеет TRUNCATE. Для журнала,
проводок, счетов, аудита и timeline доступны только SELECT/INSERT. UPDATE
проекции разрешён на два денежных столбца для SQL-блокировок и вложенных
триггеров; прямое изменение дополнительно запрещает DB guard. UPDATE резерва
ограничен полем `status` и допустимыми переходами его триггера.

`RUNTIME_DB_PASSWORD` применяется helper-скриптом идемпотентно. После изменения
этого значения выполните обычный запуск проекта, чтобы сначала обновить роль,
а затем пересоздать runtime-процессы. Не выводите полные конфигурации контейнеров
или URL с пользовательскими секретами при диагностике.

Для host-разработки `.env.example` содержит отдельный
`MIGRATION_DATABASE_URL`. Применение миграций с host требует явного временного
выбора этого owner URL; не запускайте bootstrap с runtime-ролью.

## Проверка и диагностика

```powershell
docker compose ps
docker compose logs --tail 100 backend worker scheduler outbox migrate
docker compose exec -T postgres pg_isready -U ledgerflow -d ledgerflow
docker compose exec -T redis redis-cli ping
docker compose exec -T rabbitmq rabbitmq-diagnostics -q ping
```

`/health/ready` проверяет PostgreSQL и применённую миграцию; `/health/live`
показывает жизнеспособность API-процесса. Отсутствие брокера или Redis не означает
утрату финансового журнала: PostgreSQL остаётся источником истины, а события
сохраняются в outbox для повторной доставки.

Если порт занят, сначала определите процесс-владелец. Остановите только свой
LedgerFlow dev-server или контейнер; не завершайте чужие процессы. Скрипт старта
не освобождает порты принудительно.

Если runtime получает `permission denied`, проверьте успешное завершение
`migrate` и актуальность образа. Не выдавайте приложению superuser ради обхода
ошибки. Новые таблицы и необходимые операции должны быть явно добавлены в
allowlist helper-скрипта вместе с миграцией и проверкой прав.

## Зафиксированные проверки DevOps

2026-11-25: `uv lock --check` успешно разрешил 136 пакетов; Compose с monitoring
профилем прошёл проверку; PowerShell start/stop прошли синтаксический разбор.
На существующей проектной БД helper выполнен дважды; runtime-финансовая цепочка
wallet → topup → hold/release → transfer прошла внутри откатываемой транзакции.
Шесть попыток прямого изменения/DDL/TRUNCATE были отклонены; TEMP и CREATE
проверены как запрещённые. Полная готовность образов и приложения подтверждается
отдельными актуальными результатами сборки и интеграции; этот список её не
подменяет.
