# ADR-0002: Неизменяемый RUB-ledger и сериализация финансовых изменений

Дата: 2026-11-24. Статус: принято Technical Lead для реализации; продуктовые проверки ещё должны подтвердить исполнение решения.

## Контекст

[ТЗ](../requirements/MASTER_SPEC.md) требует double-entry accounting, запрет отрицательного доступного баланса, идемпотентность и проверку конкурентности на настоящем PostgreSQL. Вариант, где Redis-lock или ORM-соглашение являются единственным барьером, не выполняет эти требования.

## Деньги и семантика счетов

Деньги хранятся в `BIGINT` минимальных единицах; `100` означает 1 RUB. Все проводки содержат строго положительный `amount_minor`, сторону `DEBIT` или `CREDIT` и account ID; валюта определяется связанными journal/account и проверяется на совпадение. Для виртуальных asset accounts: `posted_minor = SUM(debit) - SUM(credit)`. Пользовательский и merchant-кошелёк имеют обычный дебетовый остаток. Системный funding account может иметь отрицательный net balance: он является противосчётом виртуального выпуска, а не реальным банковским источником средств.

- Top-up `x`: Dr user wallet `x`, Cr sandbox funding `x`.
- Transfer `x`: Dr destination wallet `x`, Cr source wallet `x`.
- Capture `x`: Dr merchant/settlement destination `x`, Cr payer wallet `x`.
- Refund `x`: новые обратные проводки относительно исходного capture. Исходный журнал не меняется.

Commission не может возникать только в UI: если виртуальная комиссия меняет финансовые остатки, ей нужны отдельные сбалансированные проводки и явный контракт того, кто её оплачивает. Метаданные mock-провайдера сами по себе не дают права списать дополнительные средства.

JSON-контракт передаёт `*_minor` как integers. Поэтому суммы и выдаваемые проекции должны оставаться в безопасном диапазоне JavaScript `Number` (`abs(value) <= 9007199254740991`); проверяется и получатель перевода. Technical Lead установил максимум одной операции `100000000000` minor и границы DB balance/reserved не шире безопасного диапазона. Агрегаты API также проверяются на точную представимость. Расширение диапазона потребует отдельного решения о decimal strings.

## Неизменяемость и атомарность

Technical Lead выбрал sealing через идентификатор создающей PostgreSQL-транзакции:

1. `BEFORE INSERT` journal принудительно присваивает `created_txid = txid_current()`, игнорируя присланное клиентом/ORM значение.
2. `UPDATE`/`DELETE` journal и posting запрещены trigger-ами.
3. `INSERT` posting допустим только для journal с `created_txid`, равным текущей транзакции. Добавить поздние «ещё две сбалансированные проводки» к старому journal нельзя.
4. `DEFERRABLE INITIALLY DEFERRED` constraint triggers на вставку journal и posting проверяют в конце транзакции не менее двух проводок, единую валюту и равенство дебета кредиту. Даже пустой journal не может сохраниться.
5. Изменения balance/reserved projection разрешены только вложенными известными posting/hold triggers. Первоначальная projection равна нулю; произвольный direct DML отклоняется.
6. Currency, account identity, сторона и сумма проведённой проводки неизменяемы. Коррекция создаёт compensating journal с ссылкой на исходный факт.

Проверка в конце транзакции нужна потому, что в процессе вставки первой и второй проводки временная несбалансированность нормальна. Нельзя отключать проверку в тестах или для seed. Защита рассчитана на обычную runtime-роль без DDL/superuser-полномочий; владелец схемы способен менять механизм защиты и находится за этой границей доверия.

`Hold` не является расходной проводкой. Для активных резервов:

```text
reserved_minor = SUM(amount_minor WHERE hold.status = ACTIVE)
available_minor = posted_minor - reserved_minor
```

Создание hold проверяет доступность средств под теми же locks, что списание. Capture в одной транзакции закрывает ровно один hold и создаёт ровно один spending journal. Release меняет только reserved projection. После финализации hold повторная обработка не меняет финансы. `UNKNOWN` не освобождает резерв до подтверждённого отсутствия provider effect.

## Блокировки и идемпотентность

Для финансовых use cases используется `READ COMMITTED` с `SELECT ... FOR UPDATE`. Перед изменением средств все затронутые ledger accounts блокируются по возрастанию UUID. Если дополнительно блокируется payment/hold, каждый путь, включая refund/cancel/recovery, соблюдает один задокументированный порядок классов ресурсов; сортировка accounts сама по себе не исключает inter-table deadlock.

`IdempotencyRecord` имеет уникальность `(actor_id, operation_scope, key)` и fingerprint канонической бизнес-команды. Денежные integers, resource IDs и семантические defaults нормализуются до хеширования; correlation ID, access token и случайные transport-поля не включаются. Идентичный повтор возвращает сохранённый ответ/тот же operation ID; другое содержимое — `409 IDEMPOTENCY_CONFLICT`. Запись и финансовый эффект сохраняются одним commit, без TTL, способного разрешить повторное списание. `operation_scope` включает вид действия и целевой ресурс, например `payment:{id}:refund`.

Конфликт уникальности обрабатывается через `ON CONFLICT`/savepoint или полный rollback; продолжать работу в aborted SQLAlchemy transaction нельзя. Для `40P01`/`40001` повторяется вся команда в новой сессии с ограниченным retry. Business decline не повторяется автоматически. Все внешние HTTP-вызовы выполняются за пределами row locks.

Для capture и refund необходима дополнительная уникальность financial effect, а не только клиентского ключа. Два разных ключа не могут создать два capture для одного payment. Конкурентные refunds блокируют payment и проверяют `sum(refunds) + requested <= captured_amount` вместе с доступностью финансирующего refund счёта.

## Финансовые инварианты

- Для каждого сохранённого journal: как минимум две проводки, одна валюта, `sum(DEBIT) == sum(CREDIT)`.
- Для закрытой валютной системы: `sum(account.posted_minor) == 0`, включая funding и settlement accounts.
- Для каждого account: projection равна replay неизменяемых postings; reserved projection равна сумме active holds.
- Для пользовательского/merchant-кошелька: `posted_minor >= 0`, `reserved_minor >= 0`, `available_minor >= 0`. Разрешение system overdraft не переносится на пользовательский account.
- Capture/transfer/top-up/refund имеют один business effect; повтор HTTP, provider callback или broker delivery не создаёт его заново.
- Сумма проведённых refunds не превышает capture; hold не может одновременно быть captured и released.
- Баланс, journal, операция, idempotency result и outbox либо commit вместе, либо полностью откатываются.

## Приёмка и последствия

Raw SQL тесты должны пытаться сохранить пустой, одиночный, несбалансированный и разновалютный journal; изменить/удалить старые строки; добавить поздние postings; подделать `created_txid`; произвольно изменить projection. Отказ проверяется на `COMMIT`, а не только на `flush`.

На PostgreSQL нужны 100 параллельных identical-key запросов (одна операция), 20 списаний по 10000 minor из 100000 minor (10 success, 10 insufficient funds, final 0 без antifraud/лимитов), встречные transfers, конкурентные refunds и cancel-vs-capture. Hypothesis генерирует последовательности top-up/transfer/hold/release/capture/refund и сверяет replay. Для каждого fault тест проверяет отсутствие частично сохранённых финансовых фактов.

Цена решения — явные locks, несколько SQL triggers и ограничение размера транзакций. Это оправдано критичностью денег; Redis остаётся вспомогательным. DRAFT/POSTED sealing также рассматривался, но выбран transaction-ID seal, чтобы исключить отдельное изменение journal при finalization.

## Первичные источники

- [PostgreSQL: CREATE TRIGGER](https://www.postgresql.org/docs/current/sql-createtrigger.html) — механизм deferred constraint triggers.
- [PostgreSQL: Explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html) — row locks и deadlock.
- [PostgreSQL: transaction ID functions](https://www.postgresql.org/docs/current/functions-info.html#FUNCTIONS-PG-SNAPSHOT) — идентификатор текущей транзакции. Совместимость конкретной функции проверяется на выбранном Docker image PostgreSQL.
