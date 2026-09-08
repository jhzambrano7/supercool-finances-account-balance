# Account Opening — Specification

## Purpose

This is the reference for what must be true when a customer opens an account — the first vertical
slice built above the domain: application (use case), inbound HTTP, and outbound persistence.
`Account.open()` itself — purpose/type validity, I1–I7 — is already specified in
`openspec/specs/account-balance/spec.md` and is not repeated here.

Source of truth: `docs/prd.md` — §4.4 (the natural key), the idempotency table (§6.3), and the
capability list (§8).

**Why this document exists without an SDD change.** The account-balance domain went through the
full proposal → spec → design → tasks → apply → verify cycle because its rules were genuinely
disputed and revised several times before the code existed. This slice is not that: the shape is
mechanical — a use case and two adapters wired through the layout `_blank` already scaffolds, and
every non-obvious question (idempotency, HTTP status on retry, the race) is already answered by the
PRD or by precedent in this repo. This file is the decision record and contract that would otherwise
live in a `design.md`, kept under `openspec/specs/` for uniformity with the domain spec rather than
under `openspec/changes/`.

**Implemented today:** nothing yet — this document is written before the code, same order as the
domain slice (spec before implementation), and will be updated if implementation surfaces a
correction.

## Scope

**In:** opening a `USER` account via HTTP; the use case; the SQL repository adapter; the migration
that creates the `accounts` table.

**Out:** closing accounts, transfers/deposits/withdrawals (already specified for the domain in
`account-balance/spec.md`; their own use cases are future slices), authentication (out of scope
project-wide per `docs/statement.md` — the caller's identity is accepted as given).

## Decisions

Numbered `AO1`–`AO6` for citation in commits (`PRD:` trailer convention still applies for product-facing
choices; these are cited as `AO:` in the same trailer position when a commit's rationale is one of
these rather than a PRD id directly).

- **AO1 — Only `USER` accounts open through this endpoint; `account_type` is not a request field.**
  `SYSTEM` accounts are platform-seeded infrastructure (PRD §4.1), never opened by a customer
  request. Rejected: accepting `account_type` and guarding against `SYSTEM` — that adds a branch for
  a case that must never reach this door at all.

- **AO2 — No idempotency key.** Retry safety comes from the natural key `(owner_id, purpose,
  currency)` (PRD §4.4): "a retry finds the account that already exists and returns it." Rejected:
  giving this endpoint its own key like money movements — the PRD already draws this line and the
  reason (opening is distinguishable from its own retry by the data; a transfer in isolation is not).

- **AO3 — HTTP 201 on first open, HTTP 200 when the natural key already exists.** The existing
  account is returned unchanged, not an error. This is about a **sequential** retry — the natural-key
  lookup finds the row before any persistence is attempted, so nothing ever conflicts. HTTP 409 is
  reserved for the genuine race AO4 describes, not for this ordinary path.

- **AO4 — The natural-key race is resolved at the database, not the application; the loser is
  reported, not recovered.** A unique constraint on `(owner_id, purpose, currency)` is the actual
  guarantee against a duplicate row. Reviewed and revised after the code existed: the use case no
  longer catches the resulting `AccountAlreadyExistsError` to re-read and return the winner — it
  propagates, and the route reports `409 Conflict`. A client that hits this narrow window simply
  retries, which then succeeds via the same natural-key lookup AO3 already describes. Rejected (this
  document's own earlier answer, kept for the record): re-reading and returning the winner inside the
  use case — a working recovery path, but one whose only purpose was collapsing an already-rare race
  into a 200 that a second HTTP round-trip already gets for free. Rejected, unchanged from before:
  find-then-insert as the only guard — two concurrent requests can both pass the pre-check before
  either commits, which is the same shape of race this project already refuses elsewhere
  (deterministic lock ordering, I2 enforced at debit time rather than pre-checked and trusted).

- **AO5 — SQLAlchemy async engine, psycopg3's async driver.** Matches FastAPI's async request
  handling; both are already-chosen dependencies (`pyproject.toml`), so this adds no new one.

- **AO6 — Layout follows the `_blank` scaffold exactly:** the repository port lives in
  `application/gateways`, its SQL implementation in `adapters/outbound/repositories/sql`, the use
  case in `application/use_cases`, the HTTP route in `adapters/inbound/api`. No new top-level
  concept is introduced.

## Domain Types Referenced (not redefined here)

`Account`, `AccountId`, `OwnerId`, `AccountType`, `AccountPurpose`, `Currency`, `AccountStatus` —
all from `openspec/specs/account-balance/spec.md`.

## New Types

| Type | Kind | Responsibility |
| --- | --- | --- |
| `AccountRepository` | Port (ABC), `application/gateways` | collection-like — `find(criteria)`, `get(criteria)` (find-or-raise, comes free from `find`), `add` — no SQL leaks through the signature |
| `FindAccountCriteria` | Value objects, `application/gateways/models` | `FindAccountByOwnerAndPurposeAndCurrency`, `FindAccountByAccountId` — extensible instead of one dedicated method per lookup shape |
| `AccountRegister` | Application service, `application/use_cases` | orchestrates: check natural key via `find` → open or propagate the conflict → persist |
| `OpenAccountRequestDto` / `AccountResponseDto` | Pydantic, `adapters/inbound/api` | HTTP request/response shape; not passed into the use case or domain. `AccountResponseDto.from_result` is the DTO's own mapping logic, unit-tested the same way a DBO's `from_domain`/`as_domain` is |

## Requirements

### Requirement: Opening Creates A New Account When None Exists

`POST /accounts` with an `owner_id`, `purpose`, and `currency` for which no account exists yet MUST
create a `USER` account via `Account.open()`, persist it, and respond `201 Created` with its
representation.

*(Supports PRD §8 — "Open an account".)*

#### Scenario: First open for a natural key succeeds

- GIVEN no account exists for `(owner_id=U, purpose=CHECKING, currency=USD)`
- WHEN a client opens an account for that owner, purpose and currency
- THEN a new `USER` `Account` is persisted and the response is `201 Created` with its id, owner,
  purpose, currency, a zero balance, and `ACTIVE` status

### Requirement: Opening Is Idempotent By Natural Key, Not By A Key Header

`POST /accounts` for a `(owner_id, purpose, currency)` that already has an account MUST NOT create a
second one. It MUST respond `200 OK` with the existing account's representation.

*(Supports PRD §4.4, §6.3 — natural-key retry safety, distinct from the idempotency-key mechanism
that money movements use.)*

#### Scenario: Retrying an open returns the existing account

- GIVEN an account already exists for `(owner_id=U, purpose=CHECKING, currency=USD)`
- WHEN a client opens an account for that same owner, purpose and currency again
- THEN no new account is created, and the response is `200 OK` with the existing account's id
  unchanged

### Requirement: Concurrent Opens For The Same Natural Key Never Duplicate

When two requests for the same `(owner_id, purpose, currency)` race, at most one `Account` MUST be
persisted for that natural key, enforced by a database uniqueness constraint rather than an
application-level check alone. The loser is reported as a conflict, not silently recovered: a
sequential retry already succeeds via the natural-key lookup `AccountRegister` tries before ever
attempting to persist, so a second recovery path for the narrow concurrent case is redundant.

*(AO4.)*

#### Scenario: A losing concurrent insert is reported as a conflict, not recovered

- GIVEN two concurrent open requests for the same `(owner_id, purpose, currency)`, neither yet
  committed
- WHEN both attempt to persist a new account
- THEN exactly one insert succeeds, that request responds `201 Created`; the other's
  unique-violation surfaces as `AccountAlreadyExistsError`, and that request responds
  `409 Conflict`

#### Scenario: A sequential retry after a completed open still succeeds, unaffected

- GIVEN an account already exists for `(owner_id=U, purpose=CHECKING, currency=USD)`
- WHEN a client opens an account for that same owner, purpose and currency again, not concurrently
  with any other request
- THEN the natural-key lookup finds it before any persistence is attempted, and the response is
  `200 OK` with the existing account — this is the ordinary retry path (the requirement above), not
  the race one

### Requirement: An Invalid Purpose For The Implied Account Type Is Rejected

Since this endpoint only opens `USER` accounts (AO1), a `purpose` that is not one of `USER`'s valid
purposes (`CHECKING`, `SAVINGS`) MUST be rejected before any persistence is attempted.

*(Domain guard: `InvalidAccountPurposeError`, specified in `account-balance/spec.md`.)*

#### Scenario: A SYSTEM-only purpose is rejected

- GIVEN a request with `purpose=FUNDING`
- WHEN it is submitted to open an account
- THEN no account is persisted and the response is `422 Unprocessable Entity`

### Requirement: Domain Errors Map To Stable HTTP Statuses

| Domain error | HTTP status |
| --- | --- |
| `InvalidAccountPurposeError` | 422 |
| `InvalidCurrencyError` | 422 |
| `AccountAlreadyExistsError` (AO4's race) | 409 |
| any other `DomainError` reaching this endpoint | 400 — a business-rule violation over the request/system state, reported to the caller rather than treated as an internal fault |

*(Only `InvalidAccountPurposeError`, `InvalidCurrencyError` and `AccountAlreadyExistsError` are
reachable from this endpoint's own input: `Account.open()` raises no other error, and this endpoint
never calls `debit`/`credit`/`close`.)*

## Testing Strategy

- **Unit** (`tests/unit/account_balance/application/`): `AccountRegister` against a fake in-memory
  `AccountRepository` — covers first-open, existing-natural-key return, and the unique-violation-race
  path propagating `AccountAlreadyExistsError` rather than recovering it (fake raises the same
  conflict the SQL adapter would). `tests/unit/account_balance/adapters/inbound/api/`: the DTOs' own
  mapping logic (`AccountResponseDto.from_result`), same convention as a DBO's `from_domain`/
  `as_domain`.
- **Integration** (`tests/integration/account_balance/`): the SQL `AccountRepository` adapter and the
  `POST /accounts` route against a real PostgreSQL (PRD §10 — "behaviour is covered by integration
  tests against a real PostgreSQL"), run via `docker compose`, including a genuinely concurrent
  double-open proving the 201/409 split against real database contention, not a sequential illusion.

## Out of Scope (explicitly, not by omission)

- Listing/searching accounts, closing accounts, any money-movement endpoint — future slices.
- Authentication/authorization of the caller — accepted as given, per `docs/statement.md`.
- Multi-currency in one request, batch opening — not asked for, not built.
