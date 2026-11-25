# LedgerFlow dashboard

React/TypeScript interface for the local LedgerFlow API. The application uses live
API responses for balances, payment timelines, operational counters and experiments.
It does not generate financial activity in the browser.

## Local development

Use Node.js 24 and run these commands from `frontend/`:

```powershell
npm ci
npm run dev
```

The interface listens on `http://127.0.0.1:5173`. Vite proxies `/api` and `/health`
to `http://127.0.0.1:8000`; `API_URL` can override the development target. Start
the backend and its project dependencies before exercising financial scenarios.

Docker serves the production build through Nginx on container port `5173`, with
same-origin API requests proxied to `backend:8000`.

## Verification

```powershell
npm run lint
npm run typecheck
npm run test
npm run build
```

Unit/component tests verify monetary conversion, request retry boundaries,
operational states, manual reservation controls and UNKNOWN payment investigation.
They do not substitute for the root project's PostgreSQL integration tests or
headed Playwright scenarios against the live application.

## Financial and authentication boundaries

- Amounts cross the API boundary as exact integer minor units. Decimal RUB input
  is parsed through `BigInt`; the backend enforces operation and balance limits.
- A financial submission keeps its idempotency key when its response is uncertain.
  The browser does not automatically repeat a failed financial transport request.
- Access tokens remain in memory. Refresh tokens stay in server-managed HttpOnly
  cookies and rotate through the authentication API.
- UNKNOWN payments use `/payments/{id}/resolve` to inspect the original provider.
  Their screen offers neither another processing attempt nor cancellation.
- Manual reservations can be released after confirmation. Payment reservations
  remain controlled by the payment state machine.
- Role-aware navigation is a convenience; server-side RBAC and object authorization
  remain authoritative.
