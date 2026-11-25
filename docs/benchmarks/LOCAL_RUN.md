# Локальное измерение — 2026-11-25

Фактический короткий Locust прогон завершился успешно: **314 HTTP-запросов,
0 ошибок, 9.99 requests/s**, p50 **160 ms**, p95 **1000 ms**, p99 **1600 ms**.
Это результаты одного смешанного sandbox workload, включая setup и финальные
проверки, а не измерение предельной пропускной способности.

## Конфигурация и воспроизводимость

Команда из корня репозитория:

```powershell
pwsh -NoProfile -File scripts/run-benchmarks.ps1
```

- Время workload: **2026-11-25 18:24:49–18:25:21 Europe/Moscow**
  (15:24:49–15:25:21 UTC), включая завершение текущих операций и проверки.
  Shape ограничен 30 секундами: до 10 s — 1 user, до 20 s — 5, до 30 s — 10;
  spawn rate 1 user/s. Все десять пользователей действительно запущены.
- Locust **2.47.0**, Python **3.13.5**, random seed `20261125`.
  Между операциями каждый пользователь ждёт 0.2–0.5 s. Отдельного warm-up нет.
- Git HEAD во время запуска: `6052663849030d8c7d3996424c7abc4c48433f43`.
  Рабочее дерево было **dirty**; этот хеш не описывает весь измеренный source.
  SHA256 workload:
  `f6ded4857e3be5386060c9d6e4a7268a2c4b5d1ea0ed159802b156df4ce7439e`.
- Windows 11 Pro `10.0.26200`, Intel Core i7-7700 3.60 GHz,
  4 физических / 8 логических CPU, 21,398,724,608 bytes RAM (~19.93 GiB).
  Docker Engine **29.0.1**; Docker memory limit в `stats` — **9.676 GiB**.
- Полный Compose sandbox с PostgreSQL 17.12, Redis 7.4.12, RabbitMQ 4.1.8,
  двумя локальными providers, receiver, API, frontend, outbox, Celery worker/beat.
  Optional Prometheus/Grafana/OTel также работали. Один Uvicorn API process;
  Celery concurrency 2; SQLAlchemy pool size 20 / max overflow 30 на процесс.
- Каждый из 10 отдельных synthetic customers получил два новых кошелька
  по `10_000_000` minor units виртуальных RUB. Бизнес-операция равна
  `100` minor units. Destination wallet отдельный для каждого пользователя;
  платежи идут опубликованному sandbox merchant. Это не shared-wallet hotspot
  тест; соответствующие гонки проверяются отдельно в PostgreSQL acceptance.

## Измеренные операции

Всего **224 business HTTP-запроса** (~**7.13 requests/s** на том же интервале),
70 setup-запросов и 20 финальных чтений балансов. Базовая и финальная сверки
выполняются отдельно и не входят в Locust HTTP latency/RPS samples.

- История платежей: 70 GET; p50/p95/p99 **110/470/860 ms**.
- История ledger кошелька: 30 GET; **85/540/650 ms**.
- Создание платежа с успешным локальным provider: 34 POST;
  **860/1900/2400 ms**. Все 34 платежа завершились `SUCCEEDED`.
- Обычный перевод: 34 POST; **240/890/940 ms**.
- Первое идемпотентное списание: 28 POST; **220/520/570 ms**.
- Повтор с тем же ключом: 28 POST; **99/470/1200 ms**.
  Повторы вернули те же transfer ID и journal ID и не создали второе списание.

Во всех группах **0 HTTP errors** и **0 неожиданных business outcomes**.
Процентили округлены встроенной статистикой Locust; p99 в небольших группах
опирается на очень мало наблюдений.

## Финансовый результат

`summary.json` содержит `valid: true`; все **10/10** actor checks совпали
с рассчитанными точными целочисленными балансами. Получено 34 payment effects
и **62 transfer effects** (34 обычных + 28 первых идемпотентных команд).
28 повторов финансовый эффект не увеличили. Активных остатков резервирования
в этих кошельках после теста нет.

Сверка до (`e726ff9a-bfa4-498e-bb40-6111af40b574`) и после
(`fe1cadc2-11fc-432c-a018-46cde084f583`) завершилась `COMPLETED`.
В обоих снимках `invariants.valid: true`; все шесть счётчиков равны нулю:
unbalanced journals, projection mismatches, hold mismatches,
negative available, closed-system signed total и invalid refunds.

## Ресурсы и пределы вывода

Во время workload сохранено **7 resource samples на сервис**.
Максимум среди этих снимков: API **131.71% CPU**, до **112.5 MiB** RAM;
PostgreSQL **35.45% CPU**, до **125.3 MiB** RAM; RabbitMQ **157.93% CPU**.
Docker CPU 100% соответствует примерно одному логическому CPU, а не всему
хосту. Это выборочные значения, не непрерывно измеренные абсолютные пики.
IO, network и остальные сервисы присутствуют в исходном JSONL.

Измерение выполнялось на рабочей Windows-машине с существующей историей
sandbox и активным мониторингом. Длительного steady state, capacity ceiling,
DB lock-wait профиля и сравнения оптимизаций этот запуск не устанавливает.
Сгенерированные финансовые записи сохранены, поэтому повторный запуск
начинается с другой общей историей БД.

## Локальные исходные артефакты

Каталог **`artifacts/benchmarks/20261125-182418/`** исключён из Git:

- `summary.json` — outcomes, точные actor balances и обе сверки;
- `locust_stats.csv`, `locust_stats_history.csv`, `locust_failures.csv`,
  `locust_exceptions.csv`, `report.html`, `locust.log` — реальные HTTP samples;
- `environment.json` — время, HEAD/dirty, workload hash, hardware и shape;
- `compose-before.json`, `compose-after.json` — проектные контейнеры;
- `resources-before.jsonl`, `resources-during.jsonl`, `resources-after.jsonl`
  и производный `resources-summary.json` — ресурсы только LedgerFlow;
- `runtime-supplement.json` — версия Docker и image IDs, запрошенные после
  запуска для тех же container IDs, которые записаны до него.

Нагрузку следует запускать отдельно от Chaos/fault tests: controls локальных
providers общие. Здесь использовались только виртуальные средства, обычное
provider behavior и локальный allowlisted API.
