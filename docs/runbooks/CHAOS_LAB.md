# Chaos Lab: реальные ограниченные эксперименты

Chaos Lab использует виртуальные RUB, отдельных синтетических пользователей и те же PostgreSQL-сервисы, что и финансовый API. Это средство демонстрации отказоустойчивости локального sandbox, а не инструмент воздействия на внешние системы.

## Доступ и запуск

Нужен явно включённый `SANDBOX_MODE=true` и роль `administrator` или `operator`. Остальные роли не могут читать каталог или запускать эксперименты. Пользователи экспериментов имеют `is_sandbox=true` и случайный нераскрываемый пароль; публичных учётных данных для них нет.

API:

```text
GET  /api/v1/chaos/scenarios
POST /api/v1/chaos/experiments {"scenario_id":"provider_lost_response"}
GET  /api/v1/chaos/experiments/{id}
```

POST возвращает `202` и сохранённый `RUNNING` experiment. UI опрашивает detail. Идентификатор, ожидаемое поведение, фактические шаги, результат, инварианты и инструкция восстановления хранятся в PostgreSQL.

Одновременно выполняется не более одного эксперимента на базу: отдельное соединение удерживает session advisory lock. Рабочий бюджет — 90 секунд, сетевые запросы имеют собственные timeout. Число конкурентных команд ограничено сценарием и не превышает 100. База тестов `*_test` использует отдельную RabbitMQ topology от runtime.

## Каталог и проверяемые факты

- `provider_unavailable`: первый provider явно подтверждает отсутствие эффекта. Проверяются записанный safe fallback, `SUCCEEDED` и один capture journal.
- `provider_lost_response`: provider сохраняет receipt и задерживает ответ дольше client timeout. Проверяются `UNKNOWN`, удержанный резерв, отсутствие fallback и однократный capture после lookup у исходного provider.
- `broker_outage`: собственный loopback TCP listener обрывает publisher до AMQP handshake. Проверяются сохранённая неудачная попытка outbox, последующий broker confirm и реальное получение сообщения после восстановления маршрута.
- `worker_before_ack`: в отдельной очереди consumer connection закрывается после commit inbox/delivery, до ack. Новый consumer должен увидеть `redelivered=true`; inbox и бизнес-доставка остаются в единственном экземпляре.
- `duplicate_event`: один envelope фактически публикуется в RabbitMQ дважды; проверяются две полученные копии и один бизнес-эффект.
- `duplicate_requests`: 100 реальных application commands с одним PostgreSQL idempotency key возвращают один payment ID; затем проверяется ровно один capture. Это проверка application-entrypoint contention, не benchmark 100 TCP-запросов. HTTP-повторы проверяются отдельно в финансовых API-тестах.
- `concurrent_debits`: 20 команд по 100 RUB конкурируют за 1 000 RUB. Дополнительные risk rules обходятся только внутри изолированного эксперимента. Ожидаются 10 принятых операций, 10 отказов `INSUFFICIENT_FUNDS`, 10 capture и нулевой итоговый баланс.
- `webhook_failure`: сохранённый consumer event передаётся реальному подписанному webhook sender. Receiver кратко возвращает HTTP 500, затем retry должен перейти в `DELIVERED` с прежним event ID. Исходный fault control восстанавливается в `finally`.
- `reconciliation_discrepancy`: меняется только копия суммы собственного receipt в provider report, выбранного по operation ID. Reconciliation должен сохранить `PROVIDER_AMOUNT`; ссылка на CSV указывает на фактический run. Исходный control восстанавливается, финансовый баланс не редактируется.
- `redis_outage`: собственный TCP listener прерывает Redis client. Реальный ledger transfer выполняется через PostgreSQL, после чего проверяется подключение к штатному Redis.

AMQP/Redis fault — клиентский транспортный разрыв в принадлежащем эксперименту listener. Общий RabbitMQ или Redis daemon не останавливается. Consumer crash window воспроизводится закрытием соединения без ack; ОС-процесс не завершается. Эти границы явно возвращаются в результатах, чтобы демонстрация не приписывала себе действия, которых не было.

## Признак успешного эксперимента

`PASSED` появляется только при выполнении условий конкретного сценария **и** `invariants.valid=true`. В конце одним PostgreSQL snapshot проверяются:

- баланс debit/credit и наличие минимум двух postings в journal;
- соответствие balance projection сумме postings;
- соответствие reserved projection активным holds;
- отсутствие отрицательного available у пользовательских счетов;
- нулевая суммарная величина закрытой системы, включая funding account;
- ограничения суммы возвратов.

Результаты `FAILED` и `INTERRUPTED` сохраняются. Текст произвольной SQL/сетевой ошибки не публикуется: он может содержать параметры или URL с учётными данными. Сохраняется категория ошибки и безопасный application error code.

## Прерывание и восстановление

При штатном shutdown сначала вызывается `shutdown_experiments()`, затем закрывается engine. Активные задания отменяются, сохраняют `INTERRUPTED` и освобождают advisory lock. При аварийном завершении процесса PostgreSQL освобождает session lock сам.

`recover_stale_experiments()` проверяет только `RUNNING` старше 120 секунд и только при свободном advisory lock. Такие задания помечаются `INTERRUPTED` с актуальными инвариантами, а не придуманным успешным результатом. Если PostgreSQL недоступен, результат невозможно безопасно сохранить: сначала восстановите БД, затем выполните recovery.

Синтетические финансовые записи сохраняются для расследования. Их нельзя удалять или исправлять прямым UPDATE баланса. Временные очереди имеют имя `ledgerflow.chaos.<experiment UUID>`, автоматически истекают и удаляются только в рамках своего эксперимента. Чужие контейнеры, очереди, порты и файлы не затрагиваются.

Перед повтором после provider timeout выясните результат по исходному operation ID. Не освобождайте резерв и не выбирайте другого provider, пока финансовый эффект неизвестен. При webhook HTTP 500 восстановите receiver; дождитесь сохранённого retry либо выполните разрешённый replay после разбора `DEAD`.

## Код и проверка

- [Сценарии и пояснения](../../backend/app/modules/chaos/scenarios.py).
- [Координатор, lifecycle и фактические действия](../../backend/app/modules/chaos/application.py).
- [HTTP и sandbox/RBAC](../../backend/app/modules/chaos/presentation.py).
- [Регрессионные и интеграционные проверки](../../backend/tests/test_chaos.py).

Из каталога `backend`, с явно выбранной disposable базой `ledgerflow_test`:

```powershell
$env:SANDBOX_MODE='true'
$env:DATABASE_URL='postgresql+asyncpg://ledgerflow:ledgerflow@127.0.0.1:55432/ledgerflow_test'
..\.tools\uv\bin\uv.exe run --no-sync python -m pytest tests/test_chaos.py -q
```

Фактические результаты конкретных запусков записываются в `PROGRESS.md` и JUnit. Наличие этого runbook само по себе не означает, что все сценарии прошли на текущей машине.
