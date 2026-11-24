# LedgerFlow

LedgerFlow находится на этапе подготовки разработки. Полное техническое задание ожидается следующим сообщением пользователя; продуктовый стек, API, модель данных и финансовые сценарии пока не утверждены.

Репозиторий: [NekrasovTaras/LedgerFlow_FinTech](https://github.com/NekrasovTaras/LedgerFlow_FinTech). Рабочая ветка — `feat/ledgerflow-build`. Файл `main.py` сохранён как исходный шаблон PyCharm и не является приложением LedgerFlow.

## Подготовленная среда

Рабочая папка: `C:\Users\user\PycharmProjects\LedgerFlow_FinTech`. Основная среда — Windows 11, PyCharm и Codex Desktop. Зафиксированные версии и фактические результаты проверок находятся в [PROGRESS.md](PROGRESS.md).

В `.codex/agents/` определены восемь специализированных ролей. Главный агент выполняет интеграцию и управляет Git; одновременно здесь используются не более трёх субагентов вместе с главным агентом в четырёх доступных слотах. Распределение ответственности описано в [docs/TEAM.md](docs/TEAM.md).

## Запуск проверочного стенда

Из корня репозитория:

```powershell
npm ci
npm run browser:install
npm run preflight
npm run check
npm run test:e2e
npm run check:evidence
npm run test:e2e:headed
npm run check:evidence -- headed
```

`test:e2e:headed` запускает видимый Chromium с замедлением действий. Скриншоты, видео, trace и отчёты сохраняются локально в игнорируемых Git папках `artifacts/playwright/headed/` и `artifacts/playwright/headless/`. Стенд проверяет работу инфраструктуры браузерных тестов; финансовые операции LedgerFlow и Chaos Lab будут добавлены по ТЗ.

Для отдельного просмотра стенда используйте `npm run qa:serve`; для просмотра последнего отчёта — `npm run test:report`. Подробности и диагностика — в [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Продолжение разработки

Начните с [AGENTS.md](AGENTS.md), [GOALS.md](GOALS.md), [ROADMAP.md](ROADMAP.md), [PLANS.md](PLANS.md) и [PROGRESS.md](PROGRESS.md). Архитектурные решения находятся в [docs/architecture/README.md](docs/architecture/README.md), изменения — в [CHANGELOG.md](CHANGELOG.md).

После каждого завершённого и проверенного блока главный агент обновляет состояние, создаёт Conventional Commit и выполняет push рабочей ветки. Автоматический merge в `main` запрещён.
