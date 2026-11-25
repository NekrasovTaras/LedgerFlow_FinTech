# Product browser acceptance

Start the actual sandbox API and frontend using the project launcher. This suite
connects to `http://127.0.0.1:5173`; it never starts or tests the bootstrap fixture.
Install project Chromium with `npm run browser:install` first.

```powershell
node scripts/playwright.mjs test --config playwright.product.config.mjs
node tests/product/verify-evidence.mjs headless
node scripts/playwright.mjs test --config playwright.product.config.mjs --headed
node tests/product/verify-evidence.mjs headed
```

Headed actions default to 600 ms slow motion. One worker avoids overlapping Chaos
experiments and keeps the visible demonstration readable. Run this suite without
parallel backend stress tests or load generators against the same database.
`LEDGERFLOW_UI_URL` optionally selects another loopback application URL;
`PLAYWRIGHT_SLOW_MO` accepts 0–5000 ms.

Each browser test retains full-page PNG, WebM, trace and HTML/JSON report under
`artifacts/product-playwright/{headless,headed}/`. Mobile checks add four 375px
screenshots. Generated evidence is ignored by Git. Traces contain ephemeral local
sandbox sessions and must stay local; never use real accounts or personal data.

The suite creates uniquely named synthetic customers, wallets, ledger entries and
payments. Financial history is deliberately preserved instead of deleting posted
records. It verifies actual balances, balanced postings, causal timeline order,
compensating refunds, UNKNOWN recovery, authorization, CSV export and an actual
Chaos experiment. It does not claim to replace PostgreSQL contention, HMAC,
outbox/inbox or worker integration tests.
