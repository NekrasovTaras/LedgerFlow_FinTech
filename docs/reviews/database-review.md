# Проверка PostgreSQL и финансовых транзакций

Дата: 2026-11-25. Область: текущие models, migrations `0001`–`0005`, SQL triggers,
ledger/payments, outbox/inbox и проектный helper runtime role. Production-файлы
этим review не изменялись. Это целевая инженерная проверка, а не доказательство
отсутствия всех возможных дефектов.

## Подтверждённые результаты

- Денежные поля используют `BIGINT` minor units; SQL aggregate проверки используют
  `numeric`, без binary floating point. Projection guard ограничивает balance и
  reserved безопасным диапазоном JSON integer. Обычные кошельки дополнительно
  имеют `reserved_minor >= 0` и `balance_minor >= reserved_minor` при
  `allow_overdraft = false`.
- `journal_stamp` заменяет переданный `created_txid` на `txid_current()`.
  `ledger_apply_posting()` разрешает новые postings только создающей journal
  транзакции. Поэтому сбалансированная пара дополнительных проводок к уже
  сохранённому journal также отклоняется: одной проверки итоговой суммы было бы
  недостаточно. Новая role regression подтверждает SQLSTATE `23514`.
- Deferred triggers на journal и postings проверяют минимум две проводки и
  нулевую сумму debit-minus-credit на границе транзакции. Header и postings
  защищены от UPDATE/DELETE, journal/postings — также от TRUNCATE. `0002` читает
  immutable header без `FOR SHARE`, поэтому роль не нуждается в UPDATE journal
  или `SECURITY DEFINER`.
- Projection INSERT должен быть нулевым и совпадать с политикой account.
  Прямой UPDATE balance/reserved под фактической runtime-подобной ролью получает
  `23514`, несмотря на выданный column-level UPDATE. Этот grant нужен для
  `SELECT FOR UPDATE` и invoker triggers. Hold INSERT создаёт только `ACTIVE`;
  разрешён единственный переход в `CAPTURED`/`RELEASED`, без изменения суммы,
  account или reference. Повторное закрытие и DELETE запрещены.
- Account policy CHECK запрещает overdraft у USER/MERCHANT wallet accounts,
  требует owner у таких accounts и фиксирует назначение privileged system
  accounts. `0005` явно требует non-NULL system key: SQL CHECK принимает NULL,
  поэтому одного сравнения `system_key = 'sandbox_funding'` недостаточно.
  INSERT для SYSTEM_FUNDING и SETTLEMENT с NULL key реально отклонены с `23514`
  под runtime-подобной LOGIN-ролью.
- Composite FK wallet `(account_id, owner_id, currency)` требует тот же owner и
  currency у ledger account; identity trigger запрещает последующее изменение
  wallet ID/owner/account/currency. Проверки чужого account и rebind отклонены
  с `23503` и `23514`; status/name остаются доступными бизнес-изменениями.
- `lock_accounts()` блокирует deduplicated account IDs через `ORDER BY account_id
  FOR UPDATE` и обновляет ORM identity map. Transfer берёт обе проекции в том же
  порядке. Capture/cancel/refund сначала сериализуются по payment, затем берут
  отсортированные проекции и hold; manual hold release берёт projection перед
  hold. Внешний provider HTTP выполняется после завершения DB-транзакции.
- Refund блокирует payment, проверяет оставшуюся сумму и доступный merchant
  balance, затем в одной транзакции добавляет compensating journal/refund,
  обновляет `refunded_minor` и сохраняет timeline/audit/outbox. Unique capture
  reference `payment:<id>` защищает повторный capture дополнительно к payment
  lock/state. Межтабличная сумма refunds проверяется приложением под payment
  lock; отдельного SQL aggregate constraint для неё нет.
- Durable idempotency использует unique `(actor_id, operation, key)`, canonical
  fingerprint, `ON CONFLICT DO NOTHING` и row lock. Savepoint откатывает бизнес-
  эффект при domain error, после чего сохраняется результат отказа. Успешные
  journal/hold/operation/outbox и результат idempotency commit вместе. Для
  `40P01`/`40001` wrapper делает максимум три попытки, каждый раз в новой
  транзакции; прочие DB errors не повторяются.
- Outbox claim использует `FOR UPDATE SKIP LOCKED`. Settlement сверяет lease и
  attempt, поэтому старый publisher не перезаписывает новый claim. Inbox unique
  consumer/event и создание webhook deliveries находятся в одной транзакции;
  broker ACK следует после commit. Это at-least-once delivery с идемпотентным
  эффектом, без обещания distributed exactly-once.

## Права фактической LOGIN-роли и устранённый TEMP bypass

Новые проверки подключаются отдельной ролью `lf_test_<random hex>`, созданной
тем же [runtime helper](../../infrastructure/postgres/runtime_role.py). Это
настоящее соединение этой роли, а не owner-соединение с условными mock grants.
Проверены `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`,
`NOREPLICATION`, `NOBYPASSRLS`, запреты TEMP и CREATE на database/schema.

Фактические SQL-попытки CREATE TEMP table, обычных table/schema/function/trigger,
отключения triggers, `session_replication_role`, перехода в owner role,
UPDATE/DELETE history, TRUNCATE и изменения account policy/hold amount
отклоняются с `42501`. Поэтому ранее найденный Security Engineer путь с
вложенным TEMP trigger не может создать нужные attacker-controlled объекты.
Depth guard остаётся зависимым от ограничения DDL: возвращать TEMP/CREATE/
TRIGGER runtime-роли без изменения этого механизма нельзя.

Обычные wallet creation, top-up, reserve, release и transfer под этой ролью
проходят; `SET CONSTRAINTS ALL IMMEDIATE` дополнительно проверяет deferred
financial constraints до rollback проверочного workflow.

## Исправленные замечания и оставшееся ограничение evidence

1. **Исправлено — историческая миграция зависела от текущего кода.** На момент чтения
   `0001_foundation.py` импортировала живой `Base.metadata` и читала изменяемый
   `app/infrastructure/ledger_constraints.sql`. Поэтому будущая модель/trigger
   могла изменить результат replay revision `0001` и нарушить последующие
   migrations. Technical Lead принял исправление через version-local frozen SQL
   assets. Реальный clean replay подтверждён новым regression test: отдельно
   применяет `0001`, проверяет отсутствие future policy, затем upgrade до
   `0005` и двукратный bootstrap. Получены 5 sandbox identities и 3 funding
   journals; повторный seed не удваивает средства, сумма projections равна
   нулю. Git history и существующая финансовая история не переписывались.
2. **Исправлено — политика account/wallet требовала DB-защиты.** Lead добавил
   forward migrations `0004`/`0005` и соответствующую ORM metadata. Обе
   существующие проектные БД (`ledgerflow`, `ledgerflow_test`) обновлены до
   `0005`; новые ограничения совместимы с сохранёнными данными. Регрессии
   проверяют overdraft, NULL system key, чужого owner и wallet rebind. Изменения
   не исправляют balance вручную и не переписывают journals. Account kind
   USER/MERCHANT дополнительно выбирается application service по владельцу;
   эта проверка не выдаётся за SQL enforcement роли пользователя.
3. **Пробел evidence для retry.** Исходники содержат bounded full-transaction
   retry для `40P01`/`40001`, а acceptance tests — встречные transfers. В этой
   работе не создавался принудительный deadlock и не проверялось исчерпание всех
   трёх попыток. Нельзя выдавать успешный обычный transfer за такую проверку.
   Worker finalize/recovery использует собственные транзакции вне idempotency
   wrapper; после transient DB failure требуется повторный recovery по исходному
   payment/provider, а не повторная отправка другому provider.

## Выполненные проверки и последствия для данных

Команды из `backend`:

```text
.venv\Scripts\python.exe -m ruff check tests/test_database_role.py tests/test_migrations.py
.venv\Scripts\python.exe -m ruff format --check tests/test_database_role.py tests/test_migrations.py
.venv\Scripts\python.exe -m pytest tests/test_database_role.py tests/test_migrations.py -q --junitxml=../artifacts/tests/database-role-migrations.xml
```

Перед запуском заданы `SANDBOX_MODE=true` и owner `DATABASE_URL` с IPv4
`127.0.0.1:55432/ledgerflow_test`; вывод не содержит паролей или токенов.

Итог: Ruff check/format PASS; **26 passed за 23.54s** (25 runtime-role/constraint
probes и один clean migration replay), настоящий PostgreSQL на проектном
`55432`. Evidence: `artifacts/tests/database-role-migrations.xml` и
`artifacts/tests/database-role-migrations.log` (generated, вне Git). Другие suite и coverage
в этом review не запускались; результаты других агентов сюда не суммируются.

Fixture требует суффикс `_test`, использует random role/password и не меняет
пароль `ledgerflow_app`. Валидные синтетические seed accounts/journal остаются
как append-only история. Все отрицательные probes и дополнительный workflow
откатываются даже при неожиданном успешном выполнении SQL. Cleanup закрывает
соединения роли, проверяет отсутствие принадлежащих ей объектов, затем снимает
только её grants через `DROP OWNED` и удаляет только эту random role. При
обнаружении принадлежащего роли объекта cleanup останавливается. Financial
DELETE/TRUNCATE, удаление существующих БД, volume reset и rollback migrations
не выполнялись. Migration regression сначала проверяет отсутствие случайно
сгенерированной `lf_migration_<uuid>_test`, создаёт её, прогоняет replay и
удаляет только эту успешно созданную им БД без FORCE и завершения чужих
соединений. Проверка после запуска не обнаружила оставшихся replay databases.

`0004`/`0005` — forward-only DDL: добавление constraints валидирует существующие
строки и требует table locks. В текущем небольшом sandbox обновление прошло;
в иной базе при нарушении политики migration завершится ошибкой вместо
переписывания финансовой истории. Для большого объёма перед rollout нужна
отдельная оценка lock duration и совместимости данных.

Изменены только [role regressions](../../backend/tests/test_database_role.py),
[migration replay regression](../../backend/tests/test_migrations.py) и этот
документ. Для recovery сохраняются immutable journals и original
provider identity; расхождение исправляется проверенным восстановлением или
компенсирующей проводкой, а не ручной записью balance. `UNKNOWN` и `NOT_FOUND`
не являются основанием освободить hold или выполнить fallback.
