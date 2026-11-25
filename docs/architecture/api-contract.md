# LedgerFlow API contract

Дата: 2026-11-24. Статус: контракт, переданный Technical Lead frontend-разработчику для реализации. Совместимость backend и frontend ещё должна быть проверена интеграционно. Источник требований — [MASTER_SPEC.md](../requirements/MASTER_SPEC.md); окончательный машинный контракт публикуется в OpenAPI работающего backend.

## Общие правила

Префикс: `/api/v1`. JSON использует `snake_case`, UUID — строки, timestamps — ISO 8601 UTC. Единственная валюта первой версии — `RUB`. Значения `*_minor` являются integers в копейках, не decimal RUB и не float. Один запрос финансового изменения допускает `1..100000000000` minor; DB balance/reserved bounds — до `9007199254740991` по модулю. Итоговые агрегаты API тоже должны оставаться точно представимыми, без округления или скрытого clamp.

Каждый список возвращает один envelope:

```json
{"items": [], "total": 0, "page": 1, "page_size": 20}
```

`page` начинается с 1; `page_size` ограничивается сервером. Фильтрация и сортировка используют allowlist полей. `total` считается после применения ownership и фильтров. Порядок страниц стабилизируется дополнительной сортировкой по ID. Параметры поиска и фильтров не интерполируются в SQL.

Ошибки имеют JSON-формат:

```json
{"error": {"code": "INSUFFICIENT_FUNDS", "message": "Insufficient available balance."}, "correlation_id": "request-correlation-id"}
```

`401` — отсутствующая/недействительная сессия; `403` — запрещённая функция; `404` — отсутствующий или недоступный объект; `409` — конфликт состояния, средств или идемпотентности; `422` — недопустимые входные данные; `429` — ограничение попыток/нагрузки. Необработанные SQL/HTTP exceptions не выдаются клиенту. Correlation ID возвращается также в `X-Correlation-ID`; сервер проверяет размер/формат переданного значения или генерирует своё.

Финансовые POST требуют `Idempotency-Key`. Frontend создаёт ключ один раз для команды и повторно использует его при неопределённом transport outcome, пока payload не изменился. Изменение payload с тем же scoped key получает `409`. Блокировка кнопки не заменяет server-side идемпотентность.

## Аутентификация и сессии

- `POST /auth/register`: `{email, password, display_name}`. Публичная регистрация не позволяет назначить privileged role.
- `POST /auth/login`: `{email, password}`.
- `POST /auth/refresh`: пустой JSON; сервер использует и ротирует HttpOnly refresh cookie.
- `POST /auth/logout`: пустой JSON; отзывает текущую сессию и удаляет cookie.
- Список/отзыв пользовательских сессий реализуется отдельными серверными маршрутами; frontend Settings не должен опираться на выдуманные сессии.

Login/refresh response:

```text
SessionResponse {
  access_token: string,
  token_type: "bearer",
  user: { id, email, display_name, role }
}
```

Роли: `customer`, `merchant`, `risk_analyst`, `operator`, `administrator`. Access token передаётся `Authorization: Bearer ...`; frontend держит его в памяти. Refresh token отсутствует в JSON и localStorage. После reload frontend пытается refresh, а после завершения сессии возвращает login. Cookie-запросы используют `credentials: include`; Origin/CORS разрешают только настроенные локальные интерфейсы. Наличие authenticated user не предоставляет доступ ко всем объектам.

## Кошельки и переводы

```text
Wallet {
  id, name, currency, status, owner_id,
  available_minor, reserved_minor, posted_minor
}
Transfer {
  id, source_wallet_id, destination_wallet_id, amount_minor,
  currency, description, created_at
}
```

- `GET /wallets`, `GET /wallets/{id}` — только разрешённые ресурсы.
- `POST /wallets`: `{name, currency: "RUB"}` — кошелёк текущего пользователя; привилегированное создание требует отдельной проверки и audit.
- `GET /wallets/{id}/transactions` — реальные финансовые факты в list envelope.
- `POST /wallets/{id}/sandbox-topup`: `{amount_minor}` — только при явно включённом sandbox; создаёт ledger journal.
- `POST /wallets/{id}/block`, `POST /wallets/{id}/unblock`: `{}` — только уполномоченная серверная роль, с audit.
- `POST /transfers`: `{source_wallet_id, destination_wallet_id, amount_minor, description}`.

Перевод проверяет право списывать source, совместимость валют и состояние обоих кошельков; идентификатор destination не даёт права читать чужую историю. Источник и назначение не могут совпадать. Success response ссылается на одну committed операцию. Резервы, release и capture проходят через финансовые use cases; ни один endpoint не принимает произвольный новый баланс.

## Платежи и возвраты

```text
Payment {
  id, wallet_id, merchant_wallet_id, amount_minor, currency, status,
  description, provider, provider_reference, risk_score, risk_decision,
  refunded_minor, created_at, version
}
```

- `POST /payments`: `{wallet_id, merchant_wallet_id, amount_minor, description, provider_behavior?: "success", auto_process?: true}`.
- `GET /payments`, `GET /payments/{id}` — списки/детали после object-level authorization.
- `POST /payments/{id}/process`: `{}` — разрешённое продолжение сохранённого процесса; не новый независимый debit.
- `POST /payments/{id}/cancel`: `{}` — допустимо только до внешнего эффекта или после подтверждения его отсутствия.
- `POST /payments/{id}/refund`: `{amount_minor}` — полный или частичный compensating journal. Проверяются роль, доступ к merchant/payment, captured amount и сумма конкурентных refunds.
- `GET /payments/{id}/timeline` — list envelope с реальными фактами жизненного цикла.

`provider_behavior` — только управляемый sandbox-параметр; его допустимые значения определяет серверный allowlist. При выключенном sandbox нельзя использовать его для fault injection. `auto_process=false` позволяет явно показать создание/авторизацию и последующее проведение.

Состояния: `CREATED`, `PENDING`, `REQUIRES_REVIEW`, `AUTHORIZED`, `PROCESSING`, `UNKNOWN`, `SUCCEEDED`, `FAILED`, `CANCELED`, `PARTIALLY_REFUNDED`, `REFUNDED`. Их наличие не разрешает произвольные переходы. `UNKNOWN` сохраняется до подтверждения исхода; клиент не должен превращать его в FAILED после собственного timeout.

```text
TimelineEvent {
  id, kind, title, detail, status, created_at,
  correlation_id, trace_id, data
}
```

`data` содержит только разрешённые диагностические поля. История включает риск, hold, provider attempts, ledger/outbox, webhook attempts и ошибки; frontend не синтезирует отсутствующие этапы и не генерирует фиктивные correlation/trace IDs.

## Ledger и risk

```text
Account { id, name, kind, currency, balance_minor, owner_id }
Posting { account_id, side, amount_minor }
Journal { id, kind, reference_id, created_at, postings, description }
RiskReview { id, payment_id, score, rules, explanation, status, created_at }
```

- `GET /ledger/accounts`, `GET /ledger/journal` — read-only; область выборки зависит от роли/ownership.
- `GET /risk/reviews` — очередь доступна risk analyst и другим явно разрешённым ролям.
- `POST /risk/reviews/{id}/decision`: `{decision: "ALLOW" | "DECLINE", reason}` — одна допустимая финализация review и audit.

`risk_score` находится в `0..100`. Результат risk engine содержит объяснение, rule IDs и версию правил; решение и его история сохраняются. Customer не может принять review или повысить свою роль передачей дополнительного JSON-поля.

## Providers, outbox и webhooks

```text
Provider {
  name, status, available, fee_bps, circuit_state, success_rate, latency_ms
}
OutboxEvent { id, event_type, status, attempts, created_at, last_error }
WebhookDelivery { id, event_id, event_type, status, attempts, last_error, created_at }
Subscription { id, receiver, event_types, active, created_at }
```

- `GET /providers` — локальные провайдеры и фактические/явно помеченные конфигурационные показатели.
- `GET /outbox` — операционный список для разрешённой роли.
- `GET /webhooks` — журнал доставок; подписки используют отдельно определённые маршруты управления, если они доступны в OpenAPI.
- `POST /webhooks/{id}/replay`: `{}` — audited replay исходного event ID, без нового финансового эффекта.

`receiver` — идентификатор из конфигурационного allowlist. Свободный URL пользователя, redirects и запросы к произвольным внутренним host/port запрещены. HMAC secret не возвращается в API. `success_rate` не заменяет реальные измерения: контракт и интерфейс должны различать настроенную вероятность mock-провайдера и наблюдаемую статистику.

## Reconciliation и Chaos Lab

- `GET /reconciliation/runs`, `POST /reconciliation/runs`, `GET /reconciliation/runs/{id}` — сохранённые статусы, summary и расхождения; CSV endpoint публикуется в OpenAPI.
- `GET /chaos/scenarios` — каталог сценариев, `POST /chaos/experiments`: `{scenario_id}`.
- `GET /chaos/experiments/{id}` — результат конкретного эксперимента, доступного вызывающему пользователю.

```text
ChaosScenario { id, name, description, expected_behavior, recovery }
Experiment {
  id, scenario_id, status, expected_behavior, result, invariants,
  timeline, recovery, created_at
}
```

Chaos требует одновременно sandbox mode и разрешённую роль. `result`, `invariants` и `timeline` содержат фактические измерения и ссылки на участвовавшие операции. PASS устанавливается после проверки ожидаемого поведения; исключение, timeout или провал инварианта не маскируются успешным статусом. Длительность и число запросов ограничены на сервере. Reconciliation не предоставляет команду произвольного изменения баланса.

## Overview, health и Settings

`GET /dashboard` выдаёт DB-derived `available_minor`, `reserved_minor`, `total_payments`, `succeeded_payments`, `failed_payments`, `pending_payments`, `outbox_pending`, `webhook_failures`, `reconciliation_discrepancies`, `recent_payments` и `volume_series[{date, amount_minor, count}]`. Метрики сначала ограничиваются областью доступа. Нет данных — честный ноль/empty state, ошибка запроса — error state, а не ранее зашитые KPI.

Health endpoint возвращает `{status, services, sandbox_mode}`; liveness не требует успешного broker ping, readiness отражает необходимые зависимости. Settings показывает `{sandbox_mode, currency, risk_rules, limits}` в разрешённом объёме и фактические сессии. Административные изменения не выполняются только переключателем в локальном React state.

## Обязательная интеграционная сверка

Перед фиксацией блока сравнить этот контракт, FastAPI OpenAPI и `frontend/src/api/types.ts`: поля денег/пагинации, тела create/transfer/refund, error envelope, nullability, timeline и роли. Проверить refresh rotation, repeated submit с тем же ключом, forbidden object IDs, cancellation UNKNOWN, CSV download и loading/empty/error/success states. Отсутствующие в OpenAPI вспомогательные маршруты не объявлять реализованными только потому, что они упомянуты в плане.
