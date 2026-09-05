# PRD — Account Balance Service

**Status:** Draft v1
**Owner:** Jhon Zambrano
**Context:** SuperCool Finances tech challenge (see [statement.md](./statement.md))

---

## 1. Problem

SuperCool Finances needs a service that owns customer account balances and the movements
between them. Money correctness is the product: a balance that is wrong, even briefly, is a
business-ending defect. Everything in this document is subordinate to one goal — **no money is
ever created, destroyed, or duplicated by this service.**

## 2. Goals

| # | Goal | Why it matters |
| --- | --- | --- |
| G1 | Every balance is explainable by an immutable trail of accounting entries | Auditability; a balance nobody can justify is not an asset, it's a liability |
| G2 | Concurrent operations on the same account never corrupt a balance | Two simultaneous transfers must not both pass a stale balance check |
| G3 | A retried request never moves money twice | Networks fail mid-flight; clients retry; money must not |
| G4 | A customer account can never go negative | Overdraft is not a supported product |
| G5 | A customer can only move money out of accounts they own, and can only fund their own | Authentication without authorization is an open vault |

## 3. Non-Goals (explicitly out of scope for v1)

| Out of scope | Rationale | Where it would live |
| --- | --- | --- |
| Fraud detection | A bounded context of its own, and a large one. Emulating it here would add noise, not signal | Independent microservice, fed by events |
| Transactional outbox | Only justified once something downstream consumes our events (i.e. fraud) | Outbound adapter + relay, same service |
| Real authentication provider | Not the core of the exercise. We model the *port*, so a real IdP is a drop-in replacement | Inbound gateway adapter |
| Multi-currency transfers | Deliberately deferred. See §8 — the model is shaped so this is an addition, not a rewrite | New use case + FX rate gateway |
| Interest, fees, statements | Product surface, not balance correctness | Separate contexts |

---

## 4. Domain model

### 4.1 Account types — and why the "no negative balance" rule is typed

Double-entry means every debit has a matching credit. This has a consequence that must be stated
plainly: **if no account may go negative, the first deposit in the system is impossible** — the
money has to come *from* somewhere.

So accounts are typed, and the invariant is scoped to the type:

| Type | Meaning | Negative balance allowed |
| --- | --- | --- |
| `USER` | A customer's money. A liability of SuperCool towards a person | **No** — invariant enforced in the domain |
| `SYSTEM` | The counterparty representing the outside world (funding, settlement) | **Yes** — its negative balance *is* the money owed/held externally |

This is the single most important modelling decision in the service, and it is the reason the
ledger balances by construction while customers still cannot overdraft.

### 4.2 Core aggregates

- **Account** — identity, owner, `type`, `currency`, `balance`, `status`, `version`.
  A user holds **one account per currency**. This is what makes §8 cheap.
- **Transfer** — the intent: source account, destination account, amount, idempotency key,
  requested-by. Immutable once accepted.
- **Entry** — one leg of the double-entry posting: account, direction (`DEBIT`/`CREDIT`),
  amount, transfer reference, timestamp. **Append-only. Never updated, never deleted.**

### 4.3 Money

`Money` is a Value Object: **integer amount in minor units + currency code**. Floating point is
forbidden anywhere in the money path. Arithmetic between different currencies raises a domain
error — currency mismatch is a domain rule, not an API validation.

### 4.4 Domain invariants

| ID | Invariant | Enforced where |
| --- | --- | --- |
| I1 | For every transfer, `sum(debits) == sum(credits)` | Domain — a transfer cannot be constructed unbalanced |
| I2 | A `USER` account balance is never `< 0` | Domain — `Account.debit()` raises `InsufficientFundsError` |
| I3 | Transfer amount is strictly positive | Domain — `Money` / transfer construction |
| I4 | Source and destination currencies match | Domain — v1 rejects cross-currency |
| I5 | Source and destination are different accounts | Domain — self-transfer is meaningless |
| I6 | Entries are immutable | Persistence — no UPDATE/DELETE path exists |
| I7 | Corrections happen by compensating entry, never by mutation | Domain — reversal is a new transfer |

Invariants live in the **domain**, not in the database constraints and not in the API layer.
The database constraints exist as a *second* line of defence, not as the primary one.

---

## 5. The critical path: executing a transfer

Chosen model: **atomic synchronous posting**. A transfer is born already confirmed — there is no
observable intermediate state, and therefore no way to leave money suspended in limbo.

Sequence, all inside a single database transaction:

1. **Authorize** — resolve the caller from the session token, then assert G5 as stated in §9.1.
   Note this is *not* simply "owns the source": a deposit's source is a `SYSTEM` account nobody
   owns, so a naive source check would block every deposit.
2. **Idempotency check** — see §6. Short-circuit and return the cached result on replay.
3. **Lock both accounts** with `SELECT ... FOR UPDATE`, **ordered deterministically by account id**.
   Ordering is not an optimization — without it, concurrent `A→B` and `B→A` transfers deadlock.
4. **Load the domain aggregates** from the locked rows.
5. **Apply the transfer in the domain** — this is where I2 (no negative user balance) is enforced,
   on a balance that cannot change underneath us because we hold the lock.
6. **Persist**: append the two entries, update both materialized balances, record the idempotency
   result.
7. **Commit.** Any failure at any step rolls back everything — there is no partial transfer.

### 5.1 Balance: materialized, with entries as the source of truth

The accounting truth is `SUM(entries)`. But you cannot take a row lock on an aggregate, and
recomputing a sum over a growing ledger on every transfer degrades without bound.

**Decision:** entries are the source of truth; `accounts.balance` is a materialized projection
updated *inside the same transaction* that writes the entries. It is therefore never stale, never
eventually-consistent, and always lockable.

**Cost:** the balance can, in principle, drift from the ledger due to a bug. **Mitigation:** a
reconciliation check asserting `account.balance == SUM(entries)` — run in tests, and available as
an operational job.

**Rejected alternative:** deriving the balance on read. Honest and simpler, but unlockable and
unbounded in cost. Rejected on the strength of the locking requirement.

### 5.2 Concurrency guarantees

- Isolation: `READ COMMITTED` + explicit row locks. The lock, not the isolation level, is what
  provides the guarantee — this keeps the behaviour easy to reason about.
- Two concurrent debits on the same account serialize on the row lock; the second one re-reads a
  balance that already reflects the first, so I2 cannot be bypassed by a race.
- Deadlock avoidance via deterministic lock ordering (§5, step 3).
- The `version` column on accounts is kept for diagnostics and for future optimistic paths.

---

## 6. Idempotency

The client generates the key (`Idempotency-Key`). That alone is not enough. The contract is:

| Concern | Rule |
| --- | --- |
| **Scope** | The key is unique **per caller**, not globally. A global namespace lets one client collide with another's key |
| **Payload binding** | We store a hash of the request payload. Same key + **different** payload ⇒ `409 Conflict`. Silently returning the old result would let a client bug replace a $10 transfer with a $10,000 one |
| **Atomicity** | The idempotency record is written in the *same* transaction as the entries. It cannot exist for a transfer that did not happen, nor be missing for one that did |
| **In-flight concurrency** | Two simultaneous requests with the same key: one wins on the unique constraint, the other is rejected/retried — never both applied |
| **Replay result** | A replay returns the result of the original operation — never an empty `200`. See §6.1 for where that result comes from |
| **Retention** | Records are retained for a bounded window that **must exceed the client's maximum retry horizon**. See §6.2 |

### 6.1 Where the replayed response comes from — it is not a cache

This is durable state, not a cache. The idempotency record is a **row in PostgreSQL**, written in
the same transaction as the entries. There is no TTL-based eviction, no memory pressure, and no
external cache in the critical path: either the row and the transfer both exist, or neither does.

What the row stores is deliberately minimal:

```
caller_id | idempotency_key | request_hash | transfer_id | status | created_at
```

**It does not store the serialized response body.** On replay, the response is **reconstructed by
reading the transfer and its entries** — the same immutable ledger that answers every other read.

**Rejected alternative:** persisting the response payload verbatim. It guarantees a byte-identical
replay, but it duplicates data that already lives in the ledger and it rots — the day the response
schema changes, old rows still carry the old shape. Rebuilding from the ledger keeps exactly one
source of truth and cannot drift from it.

### 6.2 The one real failure mode: retention expiry

If the retention window expires and the client retries *afterwards*, the key is gone and the
transfer would be applied a second time. There is no clever way around this — the mitigation is
that the **retention window is chosen to be longer than the maximum retry horizon of any client**,
which makes it a product decision, not an infrastructure one. Expired records are pruned by an
operational job.

---

## 7. Capabilities (v1 surface)

| Capability | Notes |
| --- | --- |
| Open an account | For a given owner and currency |
| Transfer between accounts | The critical path (§5). Same currency in v1. **Includes sending to another customer's account**, which is why authorization is stated over the debited leg (§9.1) |
| Deposit | Transfer from a `SYSTEM` funding account into a `USER` account |
| Withdraw | Transfer from a `USER` account into a `SYSTEM` settlement account. Subject to I2 |
| Read balance | Own accounts only |
| Read movement history | The entry trail for an account — the auditable answer to "why is my balance this?" |
| Reverse a transfer | A **new** compensating transfer referencing the original. Never a mutation or deletion (I7). **Operator-authorized only** (§7.1) |
| Close an account | `USER` accounts only, and only at a zero balance (§7.2) |

All mutating capabilities are idempotent per §6.

### 7.1 Reversal is operator-authorized, not customer-initiated

Letting the payer reverse their own transfer creates a perverse incentive: it turns every completed
payment into one the payer can unilaterally take back, which is indistinguishable from theft on the
receiving side. Reversal exists to correct *our* errors and adjudicated disputes, so the authority to
issue one sits with an operator.

The domain records `requested_by` on every transfer, so a reversal is attributable. Which operator
identities exist, and how they are proven, is the authentication adapter's problem (§9).

### 7.2 Closing an account requires a zero balance

`Account.close()` is domain behaviour, not a status field an adapter flips. It refuses unless the
balance is exactly zero, which is the only closure that leaves no unexplained money: a closed account
holding funds is either a liability nobody is watching or money quietly taken from a customer.
Emptying the account first is a transfer like any other, and therefore leaves its own entries.

A closed account can still be read — history does not disappear — but cannot be debited or credited.
Only `USER` accounts close; `SYSTEM` accounts are infrastructure and outlive any customer.

---

## 8. Designed-for extension: multi-currency

Deferred, but the model is deliberately shaped so it is an *addition*:

- One account per user **per currency** already exists in v1 — no schema migration needed.
- I4 (currency match) is a single explicit domain rule. Cross-currency becomes: debit source in
  currency A, credit destination in currency B, at a given FX rate.
- The double-entry stays balanced because the FX spread becomes an explicit entry against a
  `SYSTEM` FX account — the difference does not vanish, it is *accounted for*.

### 8.1 Quote / execute — FX without a state machine

**Hard rule: no third-party network call is ever made while holding a database lock.** A slow rate
provider would not merely degrade a request — it would pin the row lock, queue every other
operation on those accounts behind it, and exhaust the connection pool. A 3s provider timeout
becomes an outage.

So the rate is resolved **before** the transaction, never inside it:

1. **Quote (outside the transaction).** The caller requests a quote. This is the only step that
   talks to the **rate provider gateway** (an outbound port), under an aggressive deadline — and in
   practice it reads a locally maintained rate store fed by polling/streaming, rather than hitting
   the provider per request.
2. **The quote is a resource with a rate and an expiry.** Seconds to minutes, depending on the pair
   and its volatility. This mirrors how consumer FX products actually work: a rate is shown, held
   for a stated window, and the user confirms against it.
3. **Execute (inside the transaction).** With the locks held, the use case only verifies that the
   quote **has not expired** and posts the entries at the already-frozen rate. Zero external I/O.
   An expired quote is rejected and the client re-quotes.

The posting transaction therefore stays exactly as tight as it is in v1: lock, validate, append,
commit. **Multi-currency does not require the state machine deferred in §5.**

### 8.2 When a state machine *would* actually be required

Not for FX — for **external settlement**: money leaving SuperCool towards a third-party
institution. There the funds are committed but not yet settled, the outcome arrives
asynchronously, and an intermediate state must be modelled honestly. That is a different problem
from currency conversion between two accounts we own, and it is out of scope.

---

## 9. Security

### 9.1 The authorization rule (G5), stated so it is implementable

> **The caller must own every `USER` account the transfer debits. If the transfer debits no `USER`
> account — a deposit — the caller must own the `USER` account it credits.**

The obvious phrasing, "the caller owns the source account", is wrong, and the way it is wrong
matters: a deposit's source is a `SYSTEM` funding account that nobody owns, so the naive rule either
rejects every deposit or is quietly skipped for it. **An authorization rule with a silent exception
is how vaults get opened.**

Stating it over the *debited* leg resolves all four movements without exceptions:

| Movement | Legs | Caller must own |
| --- | --- | --- |
| Withdraw | `USER` → `SYSTEM` | the source (debited `USER`) |
| Transfer to another customer | `USER` → `USER` | the source only — the recipient's account is not theirs, and need not be |
| Deposit | `SYSTEM` → `USER` | the destination (no `USER` is debited) |
| System movement | `SYSTEM` → `SYSTEM` | operator authority; no customer may request it |

**Rejected alternative:** "the caller owns every `USER` leg". It reads tighter and is in fact
broken — it would require owning the *recipient's* account, forbidding customer-to-customer
transfers entirely.

Receiving money is not a privilege the recipient grants; the sender is the only party who needs to
be entitled. Validating that the destination exists and is operable must not disclose anything about
an account the caller does not own.

- **Authentication**: session token validated through a gateway port. v1 ships a simulated
  adapter; a real IdP replaces the adapter without touching the core.
- **Authorization**: see §9.1. Authenticated ≠ entitled.
- Balances and history are readable only by the account owner.
- No sensitive data in logs. Errors returned to clients do not leak the existence or state of
  accounts the caller does not own.

## 10. Success criteria

The service is done when:

1. The ledger balances: for every transfer, debits equal credits (property-tested).
2. `SUM(entries) == account.balance` for every account after any sequence of operations.
3. Concurrent transfers against the same account never produce a negative `USER` balance —
   demonstrated under actual parallel load, not just asserted in a unit test.
4. Replaying any mutating request moves money exactly once and returns the result of the original
   operation, reconstructed from the ledger.
5. A transfer can be explained end-to-end from the entry trail.
6. Domain invariants are covered by unit tests with no infrastructure; persistence and locking
   behaviour is covered by integration tests against a real PostgreSQL.

---

## 11. Open questions

- Retention window for idempotency records. Not a free parameter: it must exceed the maximum
  retry horizon of any client (§6.2). Needs the client retry policy to be pinned down first.
- What happens when a reversal cannot be afforded, because the original recipient already spent the
  funds. Posting it would drive a `USER` account below zero and breach I2. See §7.3.
