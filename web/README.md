# SuperCool Finances — Ops Console

A small React app for exercising the account-balance service in a browser — open an account, move
money, see the ledger — without reaching for `curl`. It is a demo/presentation surface, not a
production deliverable: **no automated tests**, no CI wiring, no test framework installed on
purpose. The full design is `../docs/web-ui-plan.md`; this app builds only its Phase 1 (whatever the
API on `main` serves today).

## Running it

1. Backend: from the repo root, `docker compose up -d` for PostgreSQL, then
   `uv run alembic upgrade head`, then `PYTHONPATH=src uv run uvicorn
   modules.shared.adapters.inbound.api.app:app --port 8000`.
2. Frontend: from this directory, `npm install` then `npm run dev`. The dev server proxies
   `/accounts`, `/transfers`, `/deposits` and `/withdrawals` to `http://localhost:8000`
   (`vite.config.ts`) — there is no CORS middleware on the backend by design (see
   `docs/web-ui-plan.md` §1.5), so the browser must only ever see same-origin requests.

Open the URL Vite prints (typically `http://localhost:5173`). Two simulated identities are seeded on
first run — every interesting flow here needs two people.

## What's here

Past Phase 1 now: the backend gained real `POST /deposits`/`POST /withdrawals` and real
`GET /accounts`/`GET /accounts/{id}`/`GET /accounts/{id}/movements` endpoints, and this app was
updated to use them as each landed on `main`, rather than waiting for a single big rewrite.

- **Identity bar** — a persistent, non-dismissible bar showing the simulated `X-Caller-Id`. There is
  no login; this app never pretends otherwise.
- **Open account** — `POST /accounts`.
- **Move money** — one screen, three tabs (Transfer / Deposit / Withdraw): Transfer is
  `POST /transfers`; Deposit and Withdraw are their own `POST /deposits`/`POST /withdrawals`, which
  resolve the platform's `FUNDING`/`SETTLEMENT` account server-side — the client never supplies or
  hard-codes a `SYSTEM` account id.
- **Receipt** — the double-entry, rendered from the transfer response. No balance is fabricated here.
- **Accounts** — real `GET /accounts`, the caller's own accounts with their real, current balances.
  No client-side registry, no replay hack.
- **Movement history** — real `GET /accounts/{id}/movements`: pick one of your own accounts, see its
  ledger entries (direction, counterparty, amount), cursor-paginated ("Load more" — there is no total
  count to build a numbered pager against). Each row also has a **Reverse (as admin)** action
  (`POST /transfers/{transfer_id}/reversals`) — no separate "Reversal" tab or operator-identity
  switch: that action always sends the platform's one fixed admin principal
  (`00000000-0000-0000-0000-000000000003`, see the backend's own `README.md`) as `X-Caller-Id`,
  regardless of whichever identity is active in the identity bar. Switching the whole app to
  "Admin" instead would not work here — the admin owns no customer accounts, so it could never see
  the movement it needs to reverse; overriding the caller id for just this one request is what
  actually lets an operator act on an account they don't own. A rejected non-admin attempt, an
  already-reversed transfer, and a negative resulting balance (correct per design, not an error)
  are all shown honestly, the same way every other screen here shows its real backend responses.

## Layout

`src/api/` (fetch client, error mapper, DTO types), `src/money/` (minor-units parsing/formatting —
string arithmetic only, never `parseFloat(x) * 100`), `src/identity/` (simulated-identity store, plus
the one fixed admin principal), `src/screens/`, `src/components/`. No router, no state library, no
UI component library — four screens, `useState` and `fetch`.

## Deliberately not built here

Vitest, component tests, e2e tests. The money-parsing and error-mapping modules are the two places a
bug would actually corrupt something, and they're written narrowly and reviewed by hand instead of
tested — this surface's whole point is being cheap to throw away and rebuild, not to accrete a test
suite.
