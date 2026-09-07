# Revert — Specification

## Purpose

This is the reference for reversing a posted transfer: a **new** compensating transfer pointing at
the original, never a mutation or deletion (I7) — operator-authorized, not customer-initiated (PRD
§7.1), and posted in full even when the recipient can no longer afford it (PRD §7.3). `revert()`
itself — the domain service — is already specified in `openspec/specs/account-balance/spec.md` and is
not repeated here. This is the last PR in the feature-branch-chain: it targets `feat/transfer` and
reuses that slice's persistence layer (`transfers`, `entries`, `idempotency_records`) wholesale —
nothing here is a parallel mechanism.

Source of truth: `docs/prd.md` §7.1 (operator-only), §7.2 (n/a here — closure, not reversal), §7.3
(the unaffordable reversal). Carries forward `openspec/changes/archive/2026-09-07-account-balance-domain/design.md`
§8's "at most one reversal per transfer" constraint, still unimplemented until this slice.

**Why this document exists without a full SDD change.** Same reasoning as `account-opening` and
`transfer`: every non-obvious question is already answered by the PRD or by precedent this repo
already established on the `transfer` slice (the idempotency mechanism, the lock-ordering mechanism,
the error-mapping discipline, and — found the hard way, by an independent review, on `transfer` — the
rule that a reservation guarding a race must be inserted *before* anything the race could invalidate).
This document applies those lessons up front rather than rediscovering them.

**Implemented today:** nothing yet.

## Scope

**In:** reversing a transfer via HTTP; the use case; the at-most-one-reversal constraint; reuse of
`transfer`'s idempotency mechanism for this operation too (D12: a reversal carries its own
`idempotency_key` and `requested_by`, same as any other transfer).

**Out:** who is or isn't a legitimate operator — PRD §7.1 explicitly defers this ("which operator
identities exist, and how they are proven, is the authentication adapter's problem"); FX;
reading transfer/reversal history.

## Decisions

Numbered `R1`–`R8`, continuing `AO1`–`AO6` and `T1`–`T9`.

- **R1 — "Operator-authorized" is expressed as a separate endpoint with no ownership check, not a
  role flag on the existing one.** `POST /transfers/{transfer_id}/reversals` requires the same
  simulated `X-Caller-Id` header `transfer` introduced (T2) — purely for attribution
  (`Transfer.requested_by`, PRD §7.1: "the domain records `requested_by` on every transfer, so a
  reversal is attributable") — but the use case never calls `Account.assert_owned_by` against either
  leg. There is deliberately no v1 mechanism distinguishing "an operator" from "any caller with a
  UUID" beyond this endpoint existing at all; PRD §7.1 defers that to a real authentication adapter,
  and the statement permits simulating authentication entirely. **This is a real, stated limitation,
  not a hidden one**: today, anyone who can reach this endpoint can reverse any transfer. A real
  deployment would put it behind an operator-only authentication boundary this exercise does not ask
  for. Rejected: a boolean `is_operator` flag on the shared caller-resolution mechanism — inventing
  role data with no backing authority would be exactly the kind of thing this project's own standing
  rule (no invented behavior) exists to prevent.

- **R2 — Source and destination are derived from the original transfer, never supplied by the
  client.** The request is just `transfer_id` (which transfer to reverse) plus the same
  `idempotency_key`. `revert()`'s own guard (`source.account_id == original.destination_account_id`,
  vice versa) exists in the domain as defense-in-depth (design.md: "Internal — the use case loaded the
  wrong rows"), but a client of this endpoint has no way to get it wrong in the first place — there is
  nothing to supply. Rejected: accepting source/destination in the request body and validating them
  against the original — that reintroduces an entire class of client error (`ReversalMismatchError`)
  this endpoint's own shape can make unreachable instead.

- **R3 — Reversal reuses `transfer`'s idempotency mechanism exactly, including T5's fix.** Same
  `idempotency_records` table, same `(caller_id, idempotency_key)` uniqueness, same reconstruct-from-
  ledger replay, same `201` on both first application and replay (T3, T4). **The reservation insert
  happens first**, before the original transfer is even loaded — not because the same underfunded-
  retry failure mode `transfer` hit is obviously possible here (`debit_for_reversal` cannot itself
  raise `InsufficientFundsError` — PRD §7.3 is explicit that it posts in full and may go negative —
  so there is no domain check on this path a losing retry could fail on the way `transfer`'s could).
  It is decided this way anyway, as the same discipline `transfer`'s T3 now states explicitly:
  nothing may run before the one real collision point has a chance to happen, so that this endpoint
  does not have to be re-audited for the same bug class it would otherwise be exposed to by
  construction the moment any future change adds a check between the load and the write.

- **R4 — At most one reversal per transfer, enforced by a partial unique index, not an application
  check.** `CREATE UNIQUE INDEX ... ON transfers (reverses) WHERE reverses IS NOT NULL` (design.md
  §8, previously specified, not yet built — this slice builds it). Two concurrent reversal requests
  for the *same* original, even under *different* idempotency keys (two different operators, say),
  must not both post: the second's insert violates this index, caught the same way `AO4`/`T5` narrow
  their own catches — checking the specific constraint name, not any `IntegrityError` — and mapped to
  `409 Conflict` (`TransferAlreadyReversedError`), distinct from an idempotency conflict. Reversing a
  *reversal* is not forbidden (design.md: "an ordinary correction") — the index is on `reverses`'
  *value*, so a transfer that is itself a reversal can still receive its own, separate one.

- **R5 — The reversal posts in full; a negative `USER` balance is the correct outcome, not an error
  (PRD §7.3).** No schema change was needed: `accounts.balance_amount` was never constrained to be
  non-negative (verified against migration `38ec8c622b3f` before writing this — there is no `CHECK`
  to loosen). `debit_for_reversal` is the only call site permitted to cross zero (already enforced
  structurally, `account-balance/spec.md`'s architecture tests); this use case reaches it only via
  `domain_posting.revert()`, never directly.

- **R6 — Locking and the `SYSTEM` asymmetry are exactly `T6`/`T7`, unchanged.** The reversal's two
  legs (the original's destination and source, swapped) are locked in ascending `AccountId` order
  when `USER`-typed, never locked when `SYSTEM`-typed, whose balance stays derived from
  `SUM(entries)`. Reversing a deposit or withdrawal is a real case (the original crossed a `SYSTEM`
  leg; the reversal does too, in the opposite direction) and must not be special-cased differently
  from a customer-to-customer reversal.

- **R7 — Reversing a nonexistent transfer is `404`, mirroring `AccountNotFoundError` (T's own
  precedent).** `TransferNotFoundError`, raised when `transfer_id` does not resolve to a persisted
  row.

- **R8 — HTTP error mapping extends `transfer`'s table**; see below. `AccountOwnershipError` does not
  appear here — R1 means this use case never raises it, since it never calls `assert_owned_by`.

## New Types

| Type | Kind | Responsibility |
| --- | --- | --- |
| `RevertTransfer` | Use-case request | `transfer_id`, `idempotency_key`, `requested_by` — nothing else (R2) |
| `RevertTransferUseCase` | Application service | loads the original, derives legs, reuses `TransferMoneyUseCase`'s idempotency-reservation-first shape, calls `domain_posting.revert()` |
| `TransferNotFoundError` | Use-case error | R7 |
| `TransferAlreadyReversedError` | Use-case error | R4 |

## Requirements

### Requirement: A Transfer Is Reversed By A New Compensating Transfer

`POST /transfers/{transfer_id}/reversals` MUST post a new `Transfer` with `reverses=transfer_id`,
mirroring the original's legs, and MUST NOT mutate or delete the original (I7).

#### Scenario: A completed transfer is reversed

- GIVEN a posted transfer from Ana to Bruno for 100
- WHEN an operator reverses it
- THEN a new transfer exists debiting Bruno 100 and crediting Ana 100, carrying
  `reverses=<the original's id>`, and the original transfer's own entries are unchanged

### Requirement: A Reversal That Would Leave The Recipient Negative Still Posts In Full

Reversing a transfer MUST NOT be refused because the recipient can no longer afford it. The debit
posts in full and the recipient's balance goes negative (PRD §7.3, R5).

#### Scenario: Ana-Bruno-100, Bruno spends 80, reversed — the PRD's own golden example

- GIVEN Ana transferred 100 to Bruno, and Bruno has since spent 80 of it elsewhere
- WHEN an operator reverses the original Ana→Bruno transfer
- THEN the reversal posts in full: Bruno's balance becomes -80, Ana's balance is restored by 100,
  and four entries exist across the two transfers

### Requirement: A Transfer Is Reversed At Most Once

A second reversal request for a transfer that already has one MUST be rejected, even under a
different idempotency key than the first (R4).

#### Scenario: A second reversal of the same transfer is rejected

- GIVEN a transfer that has already been reversed once
- WHEN a reversal of it is requested again, with a idempotency key never used before
- THEN no second reversal is posted and the response is `409 Conflict`

### Requirement: Reversing A Reversal Is Not Forbidden

A transfer that is itself a reversal of another MAY be reversed in turn — "at most one reversal" is
scoped to each transfer individually, not to a chain.

#### Scenario: A reversal of a reversal succeeds

- GIVEN transfer B, which reverses transfer A
- WHEN an operator reverses B
- THEN a new transfer C is posted with `reverses=B`'s id, and this does not conflict with A already
  having its one reversal (B)

### Requirement: Reversal Reuses The Idempotency Mechanism Exactly

Retrying `POST /transfers/{transfer_id}/reversals` with the same `Idempotency-Key` and caller MUST
replay the original reversal's result, `201`, without posting a second one (R3, reusing T3/T4/T5).

#### Scenario: A retried reversal replays the original result

- GIVEN a reversal already posted under idempotency key `K` for caller `C`
- WHEN caller `C` retries with key `K`
- THEN no second reversal is posted; the response is `201` with the original reversal's
  representation, reconstructed from the ledger

### Requirement: Domain And Use-Case Errors Map To Stable HTTP Statuses

| Error | HTTP status |
| --- | --- |
| `TransferNotFoundError` | 404 |
| `TransferAlreadyReversedError` | 409 |
| Idempotency key reused with a different payload | 409 |
| Missing/malformed `X-Caller-Id` | 401 |
| any other `DomainError` reaching this endpoint | 500 — defensive default |

`AccountOwnershipError`, `InsufficientFundsError`, `SelfTransferError`, `NonPositiveAmountError`,
`InvalidCurrencyError`, `InvalidIdempotencyKeyError` are all unreachable from this endpoint's own
input by construction (R1, R2, R5) and are **not** in its error-mapping table — listing them would
claim a reachability this endpoint does not have.

## Testing Strategy

- **Unit** (`tests/unit/account_balance/application/`): `RevertTransferUseCase` against the same fake
  infrastructure `transfer`'s tests already built — the negative-balance case (PRD §7.3's own
  example), the not-found case, the double-reversal-conflict case, idempotent replay.
- **Integration** (`tests/integration/account_balance/`): the full route against real PostgreSQL,
  including a real concurrent double-reversal race (two connections, same original, different keys)
  — the one claim here that needs a real transaction, mirroring `transfer`'s own precedent for why
  that particular test can't be a unit test.

## Out of Scope (explicitly, not by omission)

- Verifying the caller is a legitimate operator (R1 — a stated, real limitation of this slice).
- Partial reversal (D10, already settled: not a concept this domain has).
- FX / multi-currency.
- Reading transfer or reversal history.
