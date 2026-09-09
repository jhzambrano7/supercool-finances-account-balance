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
| G4 | No customer action can drive their own account negative. Only a reversal may (§7.3) | Overdraft is not a product; a clawback is a debt, and a debt should be visible on the account that owes it |
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
| `USER` | A customer's money. A liability of SuperCool towards a person | **Only via reversal** (§7.3) — never through the customer's own actions |
| `SYSTEM` | The counterparty representing the outside world. Its `purpose` (§4.4) says which: funding, settlement | **Yes** — its negative balance *is* the money owed/held externally |

This is the single most important modelling decision in the service, and it is the reason the
ledger balances by construction while customers still cannot overdraft.

### 4.2 Core aggregates

- **Account** — identity, owner, `type`, `purpose`, `currency`, `balance`, `status`, `version`.
  Each account holds exactly one currency; **an owner may hold many accounts, including several in
  the same currency**, one per purpose. Currency is a property of the account, never of the owner —
  which is what makes §8 cheap. See §4.4 for `purpose` and the uniqueness it creates.
- **Transfer** — the intent: source account, destination account, amount, idempotency key,
  requested-by. Immutable once accepted.
- **Entry** — one leg of the double-entry posting: account, direction (`DEBIT`/`CREDIT`),
  amount, transfer reference, timestamp. **Append-only. Never updated, never deleted.**

### 4.3 Money

`Money` is a Value Object: **integer amount in minor units + currency code**. Floating point is
forbidden anywhere in the money path. Arithmetic between different currencies raises a domain
error — currency mismatch is a domain rule, not an API validation.

### 4.4 Purpose, and the natural key it gives us

`type` (§4.1) says what an account *is* to the ledger — `USER` or `SYSTEM` — and drives the overdraft
policy. `purpose` says what it is *for*. They are separate axes and deserve separate names: a
checking account and a savings account are both `USER`, and a funding account and a settlement
account are both `SYSTEM`.

| `type` | Valid `purpose` values |
| --- | --- |
| `USER` | `CHECKING`, `SAVINGS` |
| `SYSTEM` | `FUNDING`, `SETTLEMENT` |

Each purpose belongs to exactly one type, so the pair is validated on opening and an invalid
combination cannot be constructed.

**This yields a natural key: `(owner, purpose, currency)` is unique.** An owner has at most one
checking account in USD, and may separately hold savings in USD and checking in EUR. The platform
likewise holds exactly one USD funding account.

The key earns its place by removing a mechanism (§6.3): account opening no longer needs an
idempotency key, because retrying it is answerable from the data itself. It is enforced by a unique
constraint, not only by a check-then-insert — two simultaneous opens would both read "none exists"
and both insert, the same race that forces a constraint for double reversal.

**Rejected — a free-form account label:** more flexible, and it destroys the uniqueness that makes
opening retry-safe. Flexibility nobody asked for, paid for with a mechanism we would then need.

### 4.5 Domain invariants

| ID | Invariant | Enforced where |
| --- | --- | --- |
| I1 | For every transfer, `sum(debits) == sum(credits)` | Domain — a transfer cannot be constructed unbalanced |
| I2 | A `USER` account balance is never driven `< 0` by a customer-initiated operation. Reversal is the sole exception, and it is a *named path*, not a flag | Domain — `Account.debit()` raises `InsufficientFundsError`; `Account.debit_for_reversal()` is the only method that may go below zero |
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
3. **Lock the `USER` accounts involved** with `SELECT ... FOR UPDATE`, **ordered deterministically
   by account id**. Ordering is not an optimization — without it, concurrent `A→B` and `B→A`
   transfers deadlock. **`SYSTEM` accounts are never locked** (§5.3): a deposit therefore locks
   exactly one row, and concurrent deposits do not contend with each other at all.
4. **Load the domain aggregates** from the locked rows.
5. **Apply the transfer in the domain** — this is where I2 (no negative user balance) is enforced,
   on a balance that cannot change underneath us because we hold the lock.
6. **Persist**: append the entries, update the materialized balance of each `USER` account touched,
   record the idempotency result.
7. **Commit.** Any failure at any step rolls back everything — there is no partial transfer.

### 5.1 Balance: materialized, with entries as the source of truth

The accounting truth is `SUM(entries)`. But you cannot take a row lock on an aggregate, and
recomputing a sum over a growing ledger on every transfer degrades without bound.

**Decision:** entries are the source of truth; `accounts.balance` is a materialized projection
updated *inside the same transaction* that writes the entries. It is therefore never stale, never
eventually-consistent, and always lockable. **This applies to `USER` accounts only** — see §5.3 for
why `SYSTEM` balances are derived instead.

**Cost:** the balance can, in principle, drift from the ledger due to a bug. Not a hypothetical
one: `SqlAccountRepository.update()` shipped with `status` missing from its `.values(...)` — the
same `UPDATE`, one column away from writing a stale balance while the ledger stayed perfectly
balanced. No domain invariant can see that, because the domain never reads the column back.

**Mitigation:** a reconciliation check asserting `account.balance == SUM(entries)`, implemented in
`tests/integration/account_balance/test_reconciliation.py` and run in CI across every money-movement
path — deposit, withdrawal, transfer, reversal, idempotent replay, and mixed sequences. §11 also
names this as an operational job; **that half is descoped** (see §11).

**Rejected alternative:** deriving the balance on read. Honest and simpler, but unlockable and
unbounded in cost. Rejected on the strength of the locking requirement.

### 5.2 Concurrency guarantees

- Isolation: `READ COMMITTED` + explicit row locks. The lock, not the isolation level, is what
  provides the guarantee — this keeps the behaviour easy to reason about.
- Two concurrent debits on the same `USER` account serialize on that row's lock; the second re-reads
  a balance already reflecting the first, so I2 cannot be bypassed by a race.
- Deposits do not contend: their only lock is the destination `USER` account (§5.3).
- Deadlock avoidance via deterministic lock ordering (§5, step 3).
- The `version` column on accounts is kept for diagnostics and for future optimistic paths.

### 5.2.1 Connection pool: sized to the database, with no overflow

The service is I/O-bound on row locks, and it autoscales horizontally against a database that does
not. That makes the connection pool a capacity decision, not a tuning detail: the number of
connections one process holds, multiplied by the task ceiling, must fit inside the database's
`max_connections` with room left for operators.

**Decision:** every pool parameter is explicit, and the deployment **injects** the pool size into
the container *and* divides that same number into the connection budget to derive the task ceiling
(`infra/stacks/service_stack.py`). One number, used for both. On `db.t4g.medium` that is
`(450 − 90 reserved) ÷ 10 = 36 tasks`.

| Parameter | Value | Why |
| --- | --- | --- |
| `pool_size` | 10 | One engine per process (AO5), so this is the whole footprint of a task |
| `max_overflow` | **0** | See below — this is the load-bearing choice |
| `pool_timeout` | 5s | Thirty seconds is a request whose caller has already retried; §6's idempotency makes failing fast safe |
| `pool_pre_ping` | on | Multi-AZ was chosen deliberately; a failover severs every established connection, and without this the first request to pick up each stale one returns a 500 on a money movement |
| `pool_recycle` | 1800s | Half-open connections left behind by NAT and load-balancer idle timeouts |

**Why `max_overflow` is zero.** Overflow exists to absorb a burst shorter than the time capacity
takes to arrive. It does not help a ledger, for reasons that compound:

- **The bottleneck is the row lock, not the connection.** A second transfer against the same account
  blocks on `SELECT ... FOR UPDATE` whether or not it holds a connection. Handing it one only moves
  the wait *inside* PostgreSQL, where it occupies a backend process, a snapshot and a lock-manager
  slot. Waiting in the application pool occupies nothing and is visible to the application.
- **PostgreSQL throughput is not monotonic in connection count.** Past a small multiple of the cores,
  more concurrent backends *reduce* total throughput. Overflow connections therefore degrade the
  requests that already had one.
- **Their cost lands at the worst moment.** They are opened on demand — TCP, TLS, a forked backend —
  so the price is paid as added latency during the burst.
- **They make capacity undecidable.** The fleet would have a steady connection count and a peak one,
  forcing every margin to be sized for the peak while only the steady state is enjoyed. Overflow
  only saves money if the database is sized for the steady state, which for a ledger is exactly what
  must not be done. At zero, peak equals steady state and the budget is a real bound.

The failure mode is also the better one. A saturated task fails fast on `pool_timeout` with a local,
observable signal, and the caller retries safely because every mutating request carries an
idempotency key (§6). The alternative is connection refusal *at the database*, which hits every task
including the healthy ones and locks operators out during the incident.

**Cost:** at full scale the fleet holds 360 connections open whether or not it needs them, and the
task ceiling cannot rise without resizing the database. Both are intended: the ceiling is where the
database sizing conversation is supposed to happen.

**Rejected alternative — SQLAlchemy's defaults (`pool_size` 5, `max_overflow` 10).** This is what
ran before, implicitly. It is a reasonable general-purpose web-app default and a poor fit here: it
triples the peak footprint exactly during a burst, and it left the deployment's task ceiling derived
from a library default the service had never declared — so production capacity rested on a number no
module owned, and changing it locally would have invalidated the ceiling in silence.

**Rejected alternative — RDS Proxy.** The right answer at real scale: it multiplexes many client
connections onto few database ones, decoupling the task ceiling from `max_connections` entirely.
Rejected on scope, not on merit, and named here so the omission is a decision rather than an
oversight.

### 5.3 `SYSTEM` accounts are never locked, and their balance is not materialized

We lock a row to keep a balance from changing under a rule that is about to read it. The only such
rule is I2, and I2 does not apply to `SYSTEM` accounts — their balance is unconstrained by design
(§4.1). Locking them buys nothing, and costs everything:

**Every deposit debits the same funding account.** Locking it would serialize the deposits of every
customer in the system through a single row. A thousand concurrent deposits would queue one behind
another, waiting on a lock taken to protect an invariant that does not exist. That is not a
throughput problem to tune later; it is the wrong design.

So the asymmetry is deliberate and follows from the invariant:

| | `USER` account | `SYSTEM` account |
| --- | --- | --- |
| Row locked during posting | Yes — I2 must read a stable balance | **No** |
| Balance materialized | Yes — see §5.1 | **No** — derived from entries when needed |
| Reconciliation `balance == SUM(entries)` | Applies | Not applicable; the sum *is* the balance |

`SYSTEM` accounts still receive their entries: the ledger balances exactly as before (I1), and their
position is always recoverable as `SUM(entries)`. What disappears is a maintained column that nothing
reads on the critical path, and the global contention that maintaining it would have required.

**Consequence:** deposits are the cheapest operation in the system — one row locked. A
customer-to-customer transfer locks two. Nothing locks more.
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
| **Scope of use** | Money movements only. Lifecycle operations are made retry-safe by their own data — see §6.3 |

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


### 6.3 Idempotency keys are for money movements only

An idempotency key exists to make a **non-idempotent** operation safe to retry. Applying it to every
mutating endpoint is cargo cult: it adds a key, a payload hash and a retention policy to operations
that already have a natural answer.

| Operation | Retry-safe because |
| --- | --- |
| Transfer, deposit, withdraw, reversal | **Idempotency key.** Each application moves money again; nothing in the data says it already happened |
| Open an account | **Natural key.** `(owner, purpose, currency)` is unique (§4.4), so a retry finds the account that already exists and returns it |
| Close an account | **State.** Closure requires `ACTIVE`; a second attempt finds `CLOSED` and is rejected |

Only money movements carry a key, and the reason is precise: **applying a transfer twice produces a
different result than applying it once, and nothing in the resulting data distinguishes the two.**
That is the condition an idempotency key exists to solve. Lifecycle operations do not meet it —
opening the same account twice is prevented by a constraint, and closing a closed account is answered
by its own state.

Safety that comes from the data is strictly better than safety that comes from a stored key,
because **it cannot expire** (§6.2). Every operation moved out of the key mechanism is one fewer
thing depending on a retention window being long enough.

**Rejected — idempotency keys everywhere:** uniform, and it hides which operations are actually
unsafe to retry. Uniformity that obscures the distinction is not simplicity.

---

## 7. Capabilities (v1 surface)

| Capability | Notes |
| --- | --- |
| Open an account | For a given owner, purpose and currency; the three are unique together (§4.4). Needs no idempotency key |
| Transfer between accounts | The critical path (§5). Same currency in v1. **Includes sending to another customer's account**, which is why authorization is stated over the debited leg (§9.1) |
| Deposit | Transfer from a `SYSTEM` funding account into a `USER` account |
| Withdraw | Transfer from a `USER` account into a `SYSTEM` settlement account. Subject to I2 |
| Read balance | Own accounts only |
| Read movement history | The entry trail for an account — the auditable answer to "why is my balance this?" |
| Reverse a transfer | A **new** compensating transfer referencing the original. Never a mutation or deletion (I7). **Operator-authorized only** (§7.1) |
| Close an account | `USER` accounts only, and only at a zero balance (§7.2) |

Money movements carry an idempotency key per §6. Lifecycle operations do not need one: their own
data makes retrying them safe — see §6.3.

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

### 7.3 A reversal the recipient can no longer afford

A reversal debits the account that originally received the money. If that account already spent it,
the debit takes the balance below zero. The compensating-entry rule (I7) says *how* to reverse; this
says what happens when the recipient cannot cover it.

**Decision: the reversal posts in full, and the recipient's balance goes negative.**

Ana sends 100 to Bruno by mistake, Bruno spends 80, an operator reverses:

| Leg | Account | Direction | Amount |
| --- | --- | --- | --- |
| 1 | Bruno (`USER`) | DEBIT | 100 |
| 2 | Ana (`USER`) | CREDIT | 100 |

Bruno's balance becomes `-80`. Ana is made whole. **The debt sits on the account that owes it**,
attributed to a specific customer, in a currency, as a number — which is exactly the input a
collections process needs (out of scope, but this is what makes starting one cheap). It also settles
itself: Bruno's next deposit brings him back to zero and beyond, with no operator intervention and no
special-case code.

This is the standard clawback behaviour of real deposit accounts, and it is why G4 is stated as *no
customer action drives an account negative* rather than *accounts are never negative*.

**Why this is not an invariant with an exception.** I2 does not become "non-negative, except
sometimes". It becomes a rule about **which path** may cross zero: `Account.debit()` refuses below
zero and is what every customer-initiated movement calls; `Account.debit_for_reversal()` is the only
method permitted to cross, and only the reversal service can reach it. The rule stays total and
mechanically checkable — a grep for the second method is an audit of every place a balance can go
negative.

**Rejected — refuse the reversal:** leaves Ana without her money *and* without any record of what she
is owed. A claim with no trace in the ledger is a claim that gets lost.

**Rejected — absorb the shortfall into a `SYSTEM` receivable** (debit Bruno 20, debit the receivable
80, credit Ana 100). It keeps `USER` balances non-negative, and was the earlier decision here. It
loses the property that matters: the debt stops being *Bruno's* and becomes an aggregate the business
holds, so recovering it needs an explicit operator-driven transfer instead of simply happening on his
next deposit. Attribution is still recoverable by walking the entries, but a balance you can read
beats a join you must remember to run.

**Consequence:** a negative `USER` balance is now a meaningful business signal — the total across
accounts is credit exposure, and its growth is a risk indicator (§11.3).

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
2. `SUM(entries) == account.balance` for every `USER` account after any sequence of operations;
   `SYSTEM` positions reconcile as `SUM(entries)` by construction.
3. Concurrent transfers against the same account never produce a negative `USER` balance —
   demonstrated under actual parallel load, not just asserted in a unit test. Only
   `debit_for_reversal` can cross zero, and a test asserts no other path can.
4. Concurrent deposits do not contend: no `SYSTEM` account is ever locked (§5.3).
5. Replaying any mutating request moves money exactly once and returns the result of the original
   operation, reconstructed from the ledger.
6. A transfer can be explained end-to-end from the entry trail.
7. Domain invariants are covered by unit tests with no infrastructure; persistence and locking
   behaviour is covered by integration tests against a real PostgreSQL.
8. ~~The correctness signals of §11.1 are exported and are zero.~~ **Descoped** with §11. The one
   signal that could not be dropped — balance drift — is met by criterion 2's test instead of by an
   exported metric: it is §5.1's mitigation, not an observability nicety.

---

## 11. Observability — specified, descoped

**Status: designed, deliberately not built.** The statement asks for correctness under concurrency,
not for instrumentation, and a shallow exporter would demonstrate less than an honest scope
boundary. This section stays because *which* signals a ledger needs is the judgement worth
recording, and that judgement does not depend on the exporter.

**One exception.** §11.1's balance-drift signal is the mitigation §5.1 committed to when it chose to
materialize the balance; descoping it would leave that tradeoff unpaid for. It is met as a test
(`tests/integration/account_balance/test_reconciliation.py`), not as a metric — the periodic
operational sweep and its alerting are descoped with the rest.

Generic RED metrics — request rate, error rate, duration — say whether the service is up. They do not
say whether the money is right. The signals below are the ones specific to *this* service, and each
exists because some failure mode is invisible without it.

### 11.1 Correctness signals — these should be flat, and any movement is an incident

| Signal | Why it matters | Expected |
| --- | --- | --- |
| **Ledger imbalance** — transfers where per-currency debits ≠ credits | I1 is enforced in the domain, so a non-zero count means the enforcement itself is broken | Always `0`. Alert on the first occurrence, not on a threshold |
| **Balance drift** — `USER` accounts where `balance ≠ SUM(entries)` | The cost we accepted when materializing the balance (§5.1). This is the check that makes that tradeoff safe rather than hopeful | Always `0` |
| **`UnbalancedTransferError` raised** | It is classified Internal: if it ever fires in production it is our bug, not a caller's | Always `0` |

### 11.2 Behaviour signals — these move, and their shape is the information

| Signal | What a change in it tells you |
| --- | --- |
| **`InsufficientFundsError` rate** | A normal background level is customers spending to their limit. A spike is either an attack probing balances or, worse, balances that are wrong |
| **Idempotency key collisions** — same key, *different* payload (`409`) | Never normal. It means a client is reusing keys across distinct requests, which is exactly the bug the payload hash exists to catch (§6). Each one is a client integration defect worth chasing |
| **Idempotency replays** — same key, same payload | Healthy and expected; it is retries working. A sudden rise points at timeouts or instability *upstream* of us |
| **Lock wait time on `USER` accounts** | The direct measure of contention on the critical path. Rising p99 means accounts are getting hot; it is also the early warning for deadlock and for a lock-ordering regression (§5) |
| **Deposits per second vs. lock waits** | Deposits should show near-zero contention by construction (§5.3). If they ever correlate, someone has reintroduced a lock on a `SYSTEM` account |

### 11.3 Risk signals — the business ones

| Signal | Why |
| --- | --- |
| **Count and total of negative `USER` balances** | This *is* the credit exposure created by reversals (§7.3). It is a number the business owns, not an engineering curiosity |
| **Reversal rate, and reversals that land negative** | Growth here means either an upstream defect generating bad transfers, or abuse. Both need a human |
| **Age of negative balances** | A balance negative for a day is a collections case; one negative for a month is a write-off nobody decided on |

**The first and third rows are built, as an operator-facing read endpoint, not a metrics exporter.**
`GET /collections` (`GetCollectionsReport`, `SqlCollectionsRepository`) answers exactly these two
rows today: every negative `USER` account, its owner, the total owed per currency, and the start of
its *current* negative episode — computed by a SQL window function over `entries`, so an account
that recovered and later went negative again reports the second episode's start, not the first (see
`queries/negative_balances.py`, the module that builds the query — `sql_collections_repository.py`
only executes it). The join from "negative accounts" to "their episode start" is a `LEFT JOIN`,
deliberately: if `accounts.balance_amount` is ever negative with no entry history that explains it
— the balance-drift condition this same §11.1 exists to catch — that account is still returned,
with `negative_since = null`, rather than silently dropped out of the report and its exposure
total. Age is surfaced as descriptive buckets (under a day / 1–7 days / 7–30 days / over 30 days),
not as a write-off decision — §12 is still open. This is a collections screen, not the
alerting/exporter this section otherwise descopes: it answers "who is negative right now", on
demand, from an operator-authorized caller, the same authorization mechanism §7.1 already
established for reversal — it does not export a time series or
page anyone. **Reversal rate** (the second row) remains descoped with the rest of this section.

### 11.4 What every money movement must carry

Structured logs and traces on the posting path carry the `transfer_id`, the `idempotency_key`, the
account ids, the currency and the outcome — never the amounts of accounts the caller does not own,
and never anything that would let a log reader enumerate accounts (§9). A movement that cannot be
reconstructed from its trace is a movement that cannot be explained to a customer, and G1 says every
balance must be explainable.

Health checks distinguish *liveness* from *readiness*: readiness must fail when the database is
unreachable, because a balance service that answers while blind to its ledger is worse than one that
admits it is down.

---

## 12. Open questions

- Retention window for idempotency records. Not a free parameter: it must exceed the maximum
  retry horizon of any client (§6.2). Needs the client retry policy to be pinned down first.
- How long a negative `USER` balance stays open before it is written off, and whether write-off is
  modelled as a transfer against a `SYSTEM` loss account. Accounting policy, not a domain rule, but
  §11.3 makes the exposure visible in the meantime.
- Whether a customer holding a negative balance may still receive incoming transfers. Assumed yes —
  refusing them would block the very deposits that clear the debt.
