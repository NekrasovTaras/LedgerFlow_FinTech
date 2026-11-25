# LedgerFlow engineering instructions

## Scope and authority
- Work only in LedgerFlow and its project containers. Preserve unrelated projects and personal files.
- Routine development, project dependency installation, tests, commits and pushes are authorized.
- Never disclose secrets, change global security settings, force-push or automatically merge into main.
- Communicate with the user in Russian. Code and technical identifiers use English.
- The full specification is saved in docs/requirements/MASTER_SPEC.md. Implement it in dependency order; never report the bootstrap test fixture as product functionality.

## Start and resume
1. Read README.md, GOALS.md, ROADMAP.md, PLANS.md and PROGRESS.md.
2. Inspect git status, current branch and recent history before edits. Preserve user changes.
3. Run the documented preflight when the environment changes; use recorded evidence for unchanged checks.
4. Continue the current plan; update persistent documents after each meaningful milestone.

## Team and file ownership
- The root agent is Technical Lead and owns Git, integration, shared configuration and final verification.
- Eight roles live in .codex/agents/*.toml: Architect, Backend Engineer, Database Engineer, Frontend Engineer, QA Engineer, Security Engineer, DevOps Engineer and Technical Writer.
- At most three subagents run concurrently here (four total agent slots including the lead); never exceed the user's limit of four subagents.
- Delegate independent bounded tasks with explicit paths, acceptance checks and return format.
- Assign one writer per file. Reviewers are read-only unless explicitly assigned a fix.
- Subagents must not commit, push, merge, change branches or delegate further without the lead's explicit assignment.
- The runtime's spawn tool may not select a saved custom role. In that case explicitly instruct the subagent to read its role TOML before work; do not claim native role selection occurred.

## Engineering and evidence
- Use exact decimal/integer representations for money when implementing the specified financial domain; never binary floating point for ledger amounts.
- Record significant architecture decisions under docs/architecture/decisions/ with status and rationale.
- Use targeted, meaningful tests; never fabricate tests, coverage, results or authorship.
- Use Python 3.13, FastAPI, SQLAlchemy 2, PostgreSQL and the approved modular monolith stack. PostgreSQL is the source of financial truth; Redis is never an authoritative balance store.
- Financial postings must balance, remain immutable at database level, and update balance projections atomically. Use deterministic account lock ordering and durable PostgreSQL idempotency.
- Provider UNKNOWN must be reconciled before fallback; never claim distributed exactly-once delivery. Use outbox/inbox for at-least-once transport and idempotent business effects.
- Sandbox only: synthetic funds, local providers and allowlisted webhook receivers; no real payments, external credentials or arbitrary network destinations.
- Run Ruff, mypy, Pytest and PostgreSQL integration/concurrency tests for backend changes; frontend lint/typecheck/build and relevant Playwright scenarios for UI changes. Record measured coverage, never invented percentages.
- README.md is professional English; README.ru.md and interview material are Russian.
- For visible scenarios use npm run test:e2e:headed, which records screenshots, video, trace and HTML reports.
- Use Computer Use for meaningful PyCharm navigation and UI inspection. Edit code through filesystem tools; do not simulate activity.
- Keep credentials, local databases and generated artifacts out of Git. Review staged changes before each commit.

## Delivery
- Work on feat/ledgerflow-build unless the user later specifies another branch.
- Use Conventional Commits. Commit and push each completed, verified functional block.
- Record actual checks and limitations in PROGRESS.md; use CHANGELOG.md for delivered changes.
- Only the lead performs Git mutations. Do not automatically merge into main.
