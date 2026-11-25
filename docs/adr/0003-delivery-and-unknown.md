# ADR-0003: At-least-once delivery и запрет fallback при UNKNOWN

Дата: 2026-11-24. Статус: принято для реализации; recovery/Chaos-тесты должны подтвердить решение.

## Контекст и решение

PostgreSQL, RabbitMQ и HTTP-провайдер не участвуют в одной общей транзакции. Commit в базе, publisher confirm, consumer acknowledgement и ответ провайдера подтверждают разные факты. Поэтому система не обещает end-to-end exactly-once delivery. Выбран транспорт at-least-once с durable outbox/inbox и однократным финансовым эффектом, защищённым PostgreSQL.

## Outbox, publisher и consumer

1. Бизнес-транзакция сохраняет событие outbox вместе с payment/journal. Event ID не меняется при retry.
2. Publisher забирает готовые строки через `FOR UPDATE SKIP LOCKED` и короткий claim/lease. Он сохраняет owner, attempts, lease expiry и диагностический статус.
3. Публикация в durable RabbitMQ exchange/queue использует persistent message и publisher confirms. Длительный network wait не удерживает финансовые row locks.
4. После confirm publisher отмечает запись опубликованной, только если lease ещё принадлежит ему. Если процесс упал между confirm и DB update, публикация повторится с тем же event ID.
5. Просроченные leases восстанавливаются. Backoff ограничен, число попыток/last_error доступны оператору, исчерпанные события переходят в DLQ/failed state с возможностью audited replay.
6. Consumer в одной PostgreSQL-транзакции вставляет уникальный `(consumer_name, event_id)` в inbox и сохраняет свой business effect. Ack отправляется после commit. Конкурентный duplicate либо ожидает уникальность, либо видит уже обработанный event.

RabbitMQ publisher confirm не означает, что consumer успешно выполнил бизнес-логику. Crash до ack приводит к redelivery. Перенос inbox insert в отдельный ранний commit способен потерять business effect и запрещён. Внешняя доставка webhook также остаётся at-least-once: event ID/HMAC помогают получателю дедуплицировать её, но ответ мог потеряться после принятия.

## Provider operation и UNKNOWN

Оба локальных mock-провайдера должны иметь собственное durable хранилище accepted operations с уникальным provider idempotency key и endpoint проверки статуса. Один и тот же ключ с другим содержимым отвергается. Восстановление после restart не должно забывать, что провайдер уже принял операцию.

Ключ и выбранный provider сохраняются до HTTP-вызова. Сумма, валюта и бизнес-идентификатор неизменны при retry. Вызов находится вне financial transaction, с timeout и ограниченным exponential backoff/jitter.

- Отказ до отправки или подтверждённое terminal `NOT_APPLIED/FAILED` без финансового эффекта может разрешать fallback.
- Timeout, reset соединения, crash между HTTP и DB commit, нераспознанный ответ или неоднозначный provider status приводят к `UNKNOWN`.
- В `UNKNOWN` не отправляется новый платёж другому provider и не освобождаются средства. Повторяется status lookup того же provider/key, а не операция с новым ключом.
- Подтверждённый success приводит к capture с уникальным payment effect. Повторный success после crash возвращает уже существующий результат.
- Подтверждённое отсутствие эффекта позволяет перейти в FAILED, освободить hold либо выполнить отдельно зафиксированный safe fallback.
- Продолжительная недоступность lookup оставляет операцию UNKNOWN и создаёт наблюдаемую задачу сверки. Исчерпание retry не является доказательством, что платёж не проведён.

Circuit breaker предотвращает новые вызовы недоступному provider, но сам по себе ничего не доказывает об исходе уже отправленного запроса. Снятие hold по локальному timeout для PROCESSING/UNKNOWN может создать двойной расход и запрещено.

## Автомат платежа

Базовая нормальная цепочка: `CREATED -> PENDING -> AUTHORIZED -> PROCESSING -> SUCCEEDED`. Risk engine может перевести `PENDING -> REQUIRES_REVIEW`; одобрение возвращает в авторизацию, отклонение — в `FAILED`. Подтверждённые отказы допускают `FAILED`, разрешённая отмена до внешнего эффекта — `CANCELED`. При неоднозначном исходе `PROCESSING -> UNKNOWN`, затем только подтверждённое разрешение в success/failure либо безопасно зафиксированную повторную попытку.

`SUCCEEDED -> PARTIALLY_REFUNDED -> REFUNDED` отражает накопленные проведённые refunds; разрешён также полный refund напрямую. Terminal FAILED/CANCELED не превращается в SUCCEEDED произвольным PATCH. Изменение состояния использует row lock или optimistic version и всегда создаёт реальный timeline fact.

Разрешённые ребра должны быть явной domain-таблицей и проверяться тестами; упрощённая цепочка здесь не разрешает любые переходы между перечисленными состояниями. Cancel и capture конкурируют за один payment/hold; ровно один допустимый результат фиксируется атомарно.

## Webhook и безопасность сети

Подписка выбирает идентификатор заранее сконфигурированного mock receiver, а не URL пользователя. HTTP redirects отключены, destination host/port/path фиксированы конфигурацией. Это исключает превращение webhook API в средство доступа к произвольной локальной/внутренней сети.

Подпись HMAC-SHA256 вычисляется над timestamp и точными bytes payload; payload содержит устойчивый event ID. Receiver проверяет подпись constant-time comparison, допустимый возраст timestamp и повтор event ID. Signing secret не возвращается в списках подписок и не попадает в логи. Ошибки доставки фиксируются, получают ограниченный retry и DLQ; manual replay требует server-side роли и audit.

## Обязательные recovery-доказательства

- Брокер недоступен после commit: outbox остаётся в базе, после восстановления доставляется.
- Publisher упал после confirm: повтор публикации имеет тот же event ID, inbox сохраняет один business effect.
- Consumer упал до ack: redelivery не создаёт вторую проводку/второй результат.
- Provider принял запрос, ответ потерян: UNKNOWN, ни одного вызова второго provider, один capture после status lookup.
- Receiver возвращает 500: attempts/last_error/backoff/DLQ видны; replay после восстановления сохраняет event identity.
- Reconciliation обнаруживает искусственное расхождение: публикуется реальное discrepancy, нет прямого UPDATE баланса.

Chaos Lab сохраняет experiment ID, параметры fault, фактические timestamps, результаты запросов, инварианты и recovery. Нарисованный frontend timeline или заранее заданный PASS не являются доказательством. Fault scope ограничен ресурсами конкретного эксперимента и собственными проектными процессами.

## Источник

[RabbitMQ: Consumer acknowledgements and publisher confirms](https://www.rabbitmq.com/docs/confirms) объясняет независимость publisher confirm и consumer ack. Гарантия единственного финансового эффекта в LedgerFlow обеспечивается нашей PostgreSQL-моделью и проверяется приложением; broker сам по себе её не предоставляет.
