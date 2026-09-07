# Transfer — Specification

## Purpose

This is the reference for the critical path: executing a transfer between two accounts, including
deposits and withdrawals (both are transfers with one `SYSTEM` leg — there is no separate domain
concept for them). It covers the use case, the simulated authentication this slice introduces, and
the persistence it requires. `transfer()` itself — the domain service, I1–I7 — is already specified
in `openspec/specs/account-balance/spec.md` and is not repeated here.

Source of truth: `docs/prd.md` §5 (the critical path), §6 (idempotency), §9.1 (authorization).
Carries forward the constraints `openspec/changes/archive/2026-09-07-account-balance-domain/design.md`
§5.3 and §8 already placed on this layer before any of it was written.

**Why this document exists without a full SDD change.** Same reasoning as
`openspec/specs/account-opening/spec.md`: every non-obvious question here is already answered by the
PRD, not invented by this document. This slice is larger and genuinely harder than account-opening —
locking, idempotency, and a persistence-layer asymmetry between `USER` and `SYSTEM` accounts are real
concurrency-shaped decisions, not mechanical wiring — so this document carries more decision weight
than account-opening's did, closer to what a `design.md` would hold, kept under `openspec/specs/` for
the same reason: uniformity, and because it is meant to remain accurate after the code exists.

**Implemented today:** nothing yet, same discipline as before — spec before code.

## Scope

**In:** transferring between two accounts via HTTP (covers transfer, deposit, withdraw — see above);
the use case; locking; idempotency; the simulated authentication adapter this slice introduces;
SQL persistence for `transfers` and `entries`; the schema decision for `SYSTEM` account balances.

**Out:** reversal (its own spec, `openspec/specs/revert/spec.md`, next PR in this chain — `revert`
loads `Transfer` rows this slice's persistence creates, so it is sequenced after, not parallel to,
this one); FX/multi-currency (PRD §3, deferred); reading balance/history (not asked for yet).

## Decisions

Numbered `T1`–`T9`, continuing the citation convention `AO1`–`AO6` established in
`account-opening/spec.md`.

- **T1 — Authorization is exactly PRD §9.1, stated over the debited leg(s), not "owns the source."**
  The use case determines which legs require ownership from the two accounts' *types*, not from
  which one is nominally "source": if the debited account is `USER`, the caller must own it; if no
  `USER` account is debited (a deposit), the caller must own the credited one. A `SYSTEM`-to-`SYSTEM`
  movement has no customer caller at all — out of scope for this HTTP-facing endpoint. Rejected (the
  PRD's own rejected alternative, restated because it is exactly the mistake this use case must not
  make): "the caller owns every `USER` leg" — breaks customer-to-customer transfer, since the
  recipient's account is not the caller's and must not need to be.

- **T2 — A simulated authentication adapter, introduced by this slice.** PRD §9.1: "v1 ships a
  simulated adapter" for the session-token gateway port the statement explicitly permits simulating.
  Mechanism: an `X-Caller-Id` header carrying the caller's `OwnerId` as a UUID. Missing or malformed
  → `401 Unauthorized`, before any other validation. This is deliberately the smallest possible
  stand-in — no token, no signature, no expiry — because the exercise asks for the *port* to be real
  and the adapter to be swappable, not for a real identity provider. `account-opening` did not need
  this (opening has no ownership check to make); this is the first slice that does.

- **T3 — Idempotency is a durable row, written in the same transaction as the entries (PRD §6.1).**
  Table `idempotency_records`: `caller_id, idempotency_key, request_hash, transfer_id, status,
  created_at`, unique on `(caller_id, idempotency_key)`. It does **not** store the response body —
  a replay reconstructs the response from the `Transfer` and its `Entries`, the same read path any
  other query would use. Same key + same `request_hash` ⇒ replay, `201` (see T4 for why 201 and not
  200). Same key + **different** `request_hash` ⇒ `409 Conflict`, per PRD §6, table row 2 — a client
  bug must not silently replace a different transfer. `request_hash` is a stable hash (e.g. SHA-256
  over a canonical encoding) of `(source_account_id, destination_account_id, amount, currency)`.

- **T4 — Idempotent replay returns the same status as the original creation (`201`), not `200`.**
  This is a deliberate departure from `AO3`'s `201`/`200` split for account-opening, and the two are
  not the same situation: `AO3`'s `200` marks "this call found something that already existed for a
  *different* reason (the natural key)" — a fact worth distinguishing from a fresh creation. An
  idempotency-key replay is not that; PRD §6 is explicit that the whole point of the key is that a
  retry is indistinguishable from the original attempt from the client's point of view. Giving it a
  different status code would leak that distinction back to the client the mechanism exists to hide.

- **T5 — The natural-key race pattern (`AO4`) generalizes to the idempotency race.** Two concurrent
  requests with the same key: the DB unique constraint on `(caller_id, idempotency_key)` is what
  actually decides the winner (PRD §6, "In-flight concurrency" row), not an application pre-check.
  The use case attempts the whole transaction (idempotency row insert + entries + balance updates);
  on a unique-violation specifically against `idempotency_records`' own constraint, it re-reads and
  replays rather than erroring — mirroring `AO4`'s "narrow the catch to the actual constraint"
  correction from the account-opening review.

- **T6 — Deterministic lock ordering, `USER` accounts only, `SYSTEM` accounts never locked (PRD §5
  step 3, §5.3).** The use case sorts the `USER` account ids among the two legs by `AccountId`'s
  ordering (already `order=True` for exactly this) and issues `SELECT ... FOR UPDATE` in that order.
  A `SYSTEM` leg is read without a lock — see T7 for what "read" means for a `SYSTEM` account.
  Concretely: a customer-to-customer transfer locks two rows; a deposit or withdrawal locks exactly
  one (the `USER` leg); a `SYSTEM`-to-`SYSTEM` movement (not reachable through this endpoint, T1)
  would lock none.

- **T7 — `SYSTEM` account balance is read as `SUM(entries)`, never materialized, and its row is
  never written during posting (PRD §5.3, design §8).** The `accounts` table already carries a
  `balance_amount` column for every row (account-opening's migration, uniform by construction, since
  nothing posted to it yet). This slice does not change that schema — it changes what the repository
  does with it: for a `USER` account, `balance_amount` is read and, after posting, updated in place
  under the row's lock (T6). For a `SYSTEM` account, `balance_amount` is **never read and never
  written** by this path; the balance used is `SUM(signed_amount)` over its entries, computed on
  demand. Writing it would require locking that row first, which is exactly the contention §5.3
  exists to avoid — a `SYSTEM` account's stored `balance_amount` column simply goes unused from here
  on, rather than being kept accurate. **Rejected: a migration splitting the column out for `SYSTEM`
  rows.** Correct in principle, but the column already being inert for `SYSTEM` rows costs nothing
  and does not block this slice; revisit if a later reader ever needs to distinguish "materialized"
  from "always zero" at the schema level.

- **T8 — Exactly one `SYSTEM` funding account and one `SYSTEM` settlement account exist, seeded by
  migration.** PRD §4.4: "the platform likewise holds exactly one USD funding account." Both use
  `PLATFORM_OWNER_ID` (already reserved in `domain/identifiers.py`) as their owner, `FUNDING`/
  `SETTLEMENT` purpose, USD currency — the natural key `(owner, purpose, currency)` from
  `account-balance/spec.md` already guarantees there can only be one of each. Seeded as data in the
  same migration that creates the `transfers`/`entries`/`idempotency_records` tables, since nothing
  else in this codebase yet has a path to create a `SYSTEM` account (account-opening's endpoint
  deliberately only opens `USER` accounts, `AO1`).

- **T9 — The repository port grows two methods beyond what `account-opening` needed, rather than
  gaining a second port.** `AccountRepository.get(account_id)` (already used for `SYSTEM` reads, no
  lock) gains a sibling `get_for_update(account_id)` (issues `SELECT ... FOR UPDATE`, `USER` accounts
  only) and `update(account)` (persists a `USER` account's new balance/version under that lock). This
  is the first real exercise of the domain/use-case boundary design.md §5.3 marked "open, to revisit
  once the code exists" — it holds up as written: the use case owns locking order and persistence
  timing, the domain service (`transfer()`) stays a pure function over already-loaded aggregates.

## New Types

| Type | Kind | Responsibility |
| --- | --- | --- |
| `TransferMoney` | Use-case request, `application/use_cases` | `amount`, `source_account_id`, `destination_account_id`, `idempotency_key`, `requested_by` — the client-facing shape design.md §5.3 already specified |
| `TransferMoneyUseCase` | Application service | orchestrates: authenticate → authorize → idempotency check → lock → load → `transfer()` → persist |
| `Clock` | Port, `application/gateways` (or `services`, mirroring `IdGenerator`'s home) | supplies `occurred_at`; the domain takes no ambient time (D6) |
| `IdempotencyRecord` | Persistence row, not a domain type | `caller_id, idempotency_key, request_hash, transfer_id, status, created_at` |
| `AuthenticatedCaller` / caller-id dependency | Inbound API, `adapters/inbound/api` | resolves `X-Caller-Id` into an `OwnerId`, or raises unauthorized (T2) |

## Requirements

### Requirement: A Transfer Moves Money Between Two Accounts And Is Authorized Over The Debited Leg

`POST /transfers` MUST succeed only if the caller owns every `USER` account the transfer debits, or,
if it debits no `USER` account, the one it credits (T1, PRD §9.1).

#### Scenario: A customer-to-customer transfer is authorized by the source only

- GIVEN a caller who owns `USER` account A (checking, USD) and does not own `USER` account B
- WHEN they request a transfer debiting A and crediting B
- THEN the transfer succeeds; ownership of B is never checked

#### Scenario: A deposit is authorized by the destination, since nothing owns the source

- GIVEN the `SYSTEM` funding account as source and a caller who owns `USER` account B as destination
- WHEN they request that deposit
- THEN the transfer succeeds without any ownership check on the `SYSTEM` account

#### Scenario: A transfer debiting an account the caller does not own is rejected

- GIVEN a caller who does not own `USER` account A
- WHEN they request a transfer debiting A
- THEN no entries are persisted and the response is `403` (`AccountOwnershipError`, mapped)

### Requirement: The Caller Is Resolved From A Simulated Authentication Header

Every `POST /transfers` request MUST resolve its caller from the `X-Caller-Id` header (T2). A
missing or malformed header MUST be rejected before any other check.

#### Scenario: A missing caller header is rejected

- GIVEN a request with no `X-Caller-Id` header
- WHEN it is submitted
- THEN no accounts are loaded and the response is `401 Unauthorized`

### Requirement: A Transfer Is Idempotent By Client-Supplied Key, Not By Its Own Content

Retrying `POST /transfers` with the same `Idempotency-Key` and the same request body MUST return the
result of the original transfer, unchanged, with the same `201` status the original creation used
(T3, T4). The same key with a **different** body MUST be rejected as a conflict, never silently
applied.

#### Scenario: A retried transfer with an unchanged body replays the original result

- GIVEN a transfer already posted under idempotency key `K` for caller `C`
- WHEN caller `C` retries `POST /transfers` with key `K` and the identical body
- THEN no second transfer is posted; the response is `201` with the original transfer's
  representation, reconstructed from the ledger, not from a stored response payload

#### Scenario: The same key with a different body is a conflict, not a silent replacement

- GIVEN a transfer already posted under idempotency key `K` for caller `C`
- WHEN caller `C` retries `POST /transfers` with key `K` but a different amount
- THEN no second transfer is posted and the response is `409 Conflict`

#### Scenario: Two concurrent requests with the same key never both apply

- GIVEN two concurrent `POST /transfers` requests from the same caller with the same idempotency key
  and the same body, neither yet committed
- WHEN both attempt to post
- THEN exactly one posts a transfer; the other's unique-violation on `idempotency_records` is caught
  and it returns the same `201` result as the winner, not an error

### Requirement: `USER` Accounts Are Locked In Deterministic Order; `SYSTEM` Accounts Are Never Locked

The two accounts a transfer touches MUST be locked (`SELECT ... FOR UPDATE`) in ascending `AccountId`
order when they are `USER` accounts, and MUST NOT be locked when they are `SYSTEM` accounts (T6, PRD
§5 step 3, §5.3).

*(Not independently testable at the unit level without a real database transaction — covered by
integration tests asserting lock behavior via two concurrent connections, per the account-balance
domain spec's own precedent for concurrency claims: G2 there is documented as requiring integration
coverage, not proven by a unit test.)*

#### Scenario: A concurrent conflicting transfer on the same account serializes instead of corrupting the balance

- GIVEN a `USER` account with balance 100, and two concurrent transfers each debiting 60 from it
- WHEN both are posted concurrently against a real database
- THEN one succeeds, the other observes the post-lock balance and is rejected by I2
  (`InsufficientFundsError`, mapped to `422`) rather than both succeeding

### Requirement: A `SYSTEM` Account's Balance Is Never Read From Or Written To Its Stored Column

A `SYSTEM` account's balance MUST be computed as the sum of its entries' signed amounts at read time,
and no write to its `accounts.balance_amount` column MUST occur during posting (T7, PRD §5.3).

#### Scenario: A `SYSTEM` account's balance reflects its entries, not a maintained column

- GIVEN the `SYSTEM` funding account has received no direct writes to `balance_amount` beyond its
  seeded zero, but has posted three deposits totaling 300 as its debited leg
- WHEN its balance is computed
- THEN it equals -300 (three debits, from the `SYSTEM` account's perspective as the source), not
  whatever `balance_amount` happens to still hold

### Requirement: Domain Errors Map To Stable HTTP Statuses

| Domain error | HTTP status |
| --- | --- |
| `AccountOwnershipError` | 403 |
| `InsufficientFundsError` | 422 |
| `AccountNotOperableError` | 422 |
| `SelfTransferError` | 422 |
| `CurrencyMismatchError` | 422 |
| Idempotency key reused with a different payload | 409 |
| Missing/malformed `X-Caller-Id` | 401 |
| any other `DomainError` reaching this endpoint | 500 — defensive default |

## Testing Strategy

- **Unit** (`tests/unit/account_balance/application/`): `TransferMoneyUseCase` against fake
  `AccountRepository`/`IdempotencyRepository`/`Clock`/`IdGenerator` — authorization for all four
  movement shapes (T1's table), idempotency replay and conflict, the idempotency race path.
- **Integration** (`tests/integration/account_balance/`): real PostgreSQL via the shared
  `tests/integration/conftest.py` fixtures — the concurrent-debit lock scenario above (this is the
  one claim in this document that cannot be demonstrated any other way), the `SYSTEM` balance
  computation, and the full `POST /transfers` route for each of the four movement shapes.

## Out of Scope (explicitly, not by omission)

- Reversal — next spec, next PR in this chain.
- Reading balance or movement history.
- Real authentication (T2's header is the whole mechanism for v1, per the statement's own allowance).
- FX / multi-currency.
