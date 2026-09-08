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
   `/accounts` and `/transfers` to `http://localhost:8000` (`vite.config.ts`) — there is no CORS
   middleware on the backend by design (see `docs/web-ui-plan.md` §1.5), so the browser must only
   ever see same-origin requests.

Open the URL Vite prints (typically `http://localhost:5173`). Two simulated identities are seeded on
first run — every interesting flow here needs two people.

## What's here (Phase 1)

- **Identity bar** — a persistent, non-dismissible bar showing the simulated `X-Caller-Id`. There is
  no login; this app never pretends otherwise.
- **Open account** — `POST /accounts`.
- **Move money** — one screen, three tabs (Transfer / Deposit / Withdraw), all backed by the same
  `POST /transfers` (there is no dedicated deposit/withdraw endpoint yet — see
  `openspec/specs/transfer/spec.md`, "Known gap"). The two `SYSTEM` account ids are hard-coded in
  `src/api/seededAccounts.ts`, mirroring the backend's own `seeded_accounts.py`; delete that file the
  day `POST /deposits`/`POST /withdrawals` exist.
- **Receipt** — the double-entry, rendered from the transfer response. No balance is fabricated here.
- **Accounts** — a `localStorage` registry (there's no `GET /accounts` yet), with a "refresh via
  account-open replay" hack for reading a balance, labelled as the hack it is.
- **Movement history** and **Reversal** — placeholder screens; both need backend endpoints that
  don't exist on this branch yet.

## Layout

`src/api/` (fetch client, error mapper, seeded constants, DTO types), `src/money/` (minor-units
parsing/formatting — string arithmetic only, never `parseFloat(x) * 100`), `src/identity/`
(simulated-identity store), `src/accounts/` (client-side account registry), `src/screens/`,
`src/components/`. No router, no state library, no UI component library — four screens, `useState`
and `fetch`.

## Deliberately not built here

Vitest, component tests, e2e tests. The money-parsing and error-mapping modules are the two places a
bug would actually corrupt something, and they're written narrowly and reviewed by hand instead of
tested — this surface's whole point is being cheap to throw away and rebuild, not to accrete a test
suite.
