# SuperCool Finances — Account Balance Service

A service that owns customer account balances and the movements between them.

Everything here is subordinate to one goal: **no money is ever created, destroyed, or duplicated.**
Where a decision made the service simpler but that goal weaker, the decision was rejected — and the
rejection is written down, because the reasoning is the deliverable, not the code.

**Where to look**

| Document | What it is |
| --- | --- |
| [`docs/prd.md`](docs/prd.md) | The product contract. Every rule, with its rejected alternative |
| [`docs/coding-conventions.md`](docs/coding-conventions.md) | How code is written, independent of feature — e.g. Tell, Don't Ask |
| [`openspec/changes/account-balance-domain/proposal.md`](openspec/changes/account-balance-domain/proposal.md) | The domain model design: aggregates, invariants, twelve decisions |
| [`docs/decision-log.md`](docs/decision-log.md) | Chronological record of what was decided and why |
| [`docs/ai-transcript.md`](docs/ai-transcript.md) | Every prompt and every response, verbatim (see [AI usage](#ai-usage)) |

---

## Status — read this first

The design is written down; the implementation is partial. This section says where the line is.

| Area | State |
| --- | --- |
| Product contract, domain model, decisions | **Done** — `docs/prd.md`, domain proposal |
| `Money` / `Currency` value objects | **Built**, 35 unit tests |
| Id generation (UUIDv7) | **Built** |
| Toolchain: Python 3.14 + uv, ruff, mypy strict, pre-commit, commit-msg gate | **Built and enforcing** |
| `Account`, `Transfer`, `Entry` aggregates | **Designed, not written** |
| Use cases, HTTP API, PostgreSQL adapters, migrations | **Designed at the contract level, not written** |
| Containers, IaC | **Planned** — approach described [below](#running-it-and-deploying-it), not yet committed |

---

## Why a microservice, and why this shape inside it

**A single service, one bounded context.** Balances and the movements between them are one
consistency boundary — a transfer must debit and credit atomically. Splitting that across services
would mean distributed transactions or eventual consistency, and eventual consistency in a ledger
means a window where money exists nowhere. The boundary is drawn where the invariants are, not where
an org chart is.

Everything that is *not* balance correctness — fraud scoring, authentication, FX rates — sits behind
a port, so it can become someone else's service without touching the core.

**Ports and Adapters, with a rich domain.** The invariants live in the domain and are enforced by
behaviour on objects, not by validation in a controller or a constraint in a table. A `Transfer`
cannot be constructed unbalanced; `Money` cannot be built from a float; an `Account` refuses a debit
that would take a customer below zero. The database constraints exist as a *second* line of defence,
never the first.

This costs indirection. It buys the thing that matters here: the rules that protect money are in one
place, readable, and testable without infrastructure.

**Rejected — a modular monolith with balances as one module.** Honest, and probably right for a real
early-stage company. Rejected because the exercise asks for a service and because the isolation is
easier to demonstrate than to argue.

**Rejected — separate ledger and balance services.** Attractive on paper: a write-optimized ledger
and a read-optimized balance view. It puts a network hop inside the invariant, which is exactly the
wrong place for one.

---

## How the money is kept safe

> This section describes the **design**. Of it, only `Money` and id generation are implemented
> today; see [Status](#status--read-this-first).

### Double-entry, so the ledger balances by construction

Every movement writes entries that net to zero per currency. The balance is not a number someone
updates; it is the consequence of an immutable trail that explains it. A balance nobody can justify
is not an asset, it is a liability.

### Accounts are typed, and that is what makes the rules coherent

`USER` accounts hold customer money. `SYSTEM` accounts are the counterparty to the outside world.
The distinction is not cosmetic — it is what lets "customers cannot overdraft" coexist with
double-entry. **If no account could go negative, the first deposit in the system would be
impossible**, because the money has to come from somewhere.

### Concurrency: locks where an invariant needs them, and nowhere else

A row is locked so a balance cannot move under a rule about to read it. The only such rule is the
non-negative-balance invariant, which applies to `USER` accounts only.

| | `USER` | `SYSTEM` |
| --- | --- | --- |
| Locked while posting | Yes | **No** |
| Balance materialized | Yes | **No** — derived from entries |

That asymmetry follows from the invariant rather than from tuning. Every deposit debits the same
funding account, so locking it would serialize the deposits of every customer through a single row,
waiting on a lock taken to protect a rule that does not apply. Under this design a deposit locks one
row and a customer-to-customer transfer locks two.

Locks are to be taken in deterministic account-id order, because concurrent `A→B` and `B→A` transfers
otherwise deadlock.

### Idempotency, scoped to the operations that actually need it

An idempotency key exists to make a **non-idempotent** operation safe to retry. The condition is
precise: applying it twice differs from applying it once, *and nothing in the resulting data
distinguishes the two*. Money movements meet it. Lifecycle operations do not — opening an account is
guarded by a natural key, closing one by requiring it to be open.

The key is not a cache. It is a row written in the same transaction as the entries, so it cannot
exist for a transfer that did not happen. The replayed response is **rebuilt from the ledger**, not
stored as a blob that rots when the response shape changes.

**Safety that comes from the data is better than safety that comes from a stored key, because it
cannot expire.**

### Authorization stated so it can actually be implemented

The obvious rule — "the caller owns the source account" — is wrong, and instructively so: a deposit's
source is a `SYSTEM` account nobody owns, so it either rejects every deposit or is quietly skipped
for one case. **An authorization rule with a silent exception is how vaults get opened.**

> The caller must own every `USER` account the transfer **debits**. If it debits none — a deposit —
> the caller must own the one it **credits**.

Four movements, no exceptions. Receiving money is not a privilege the recipient grants.

---

## Decisions, and what was rejected

The full reasoning is in [`docs/prd.md`](docs/prd.md); this is the index.

| Decision | Rejected alternative | Why it lost |
| --- | --- | --- |
| Integer minor units for money | Decimal or float | Floats drift over repeated arithmetic; a ledger that drifts cannot be reconciled |
| Entries are the source of truth, balance is a materialized projection | Deriving the balance on read | Honest and simpler, but a `SUM` cannot be row-locked and its cost is unbounded |
| Transfers post atomically, no state machine | `pending → posted` states | Complexity with no client today; it needs held balances, timeouts and a sweeper for zombies |
| `SYSTEM` accounts are never locked | Lock both accounts uniformly | Serializes every deposit in the system through one row, for an invariant that does not apply |
| A reversal may take a customer negative | Absorb the shortfall into a `SYSTEM` receivable | Keeps balances clean but turns one customer's debt into an aggregate; the debt belongs on the account that owes it, where the next deposit clears it |
| ...and only through a named method | A `allow_overdraft=True` flag | A flag can be passed from anywhere; a second method must be named, so `grep` is a complete audit |
| `(owner, purpose, currency)` is unique | Idempotency key on account opening | The natural key removes a mechanism instead of adding one |
| Reversal is operator-authorized | Payer-initiated | Makes every completed payment one the payer can unilaterally claw back |
| Account type and purpose are separate axes | One enum | Collapsing them puts `SAVINGS` and `SYSTEM` in the same enum, and nothing says which governs overdraft |
| UUIDv7 for every id | UUIDv4 | Random ids scatter B-tree inserts; the ledger is append-only and write-heavy |
| Ordering on `Money` written by hand | `dataclass(order=True)` | The generated comparison answers "100 JPY < 5 USD" instead of refusing the question |

### Decisions that are not technical

| Choice | Reasoning |
| --- | --- |
| Fraud detection is out of scope | A bounded context of its own, and a large one. Emulating it would add noise, not signal |
| Transactional outbox is out of scope | Only justified once something downstream consumes the events — i.e. fraud |
| Authentication is a port with a simulated adapter | Not the core of the exercise; the seam is what matters, and a real IdP is a drop-in replacement |
| Multi-currency deliberately deferred | The model is shaped so it is an addition, not a rewrite — one account per currency already exists, and FX resolves via a quote obtained *outside* the transaction |
| Scope narrowed on purpose | The instructions say quality over quantity. A smaller surface, fully reasoned, beats a broad one that hand-waves the hard parts |

---

## Observability

Specified, not yet instrumented. Generic RED metrics say whether the service is up, not whether the
money is right, so the signals below are the ones specific to this service
([`docs/prd.md` §11](docs/prd.md)):

- **Correctness — should be flat; alert on the first occurrence, not a threshold.** Ledger imbalance,
  and `balance ≠ SUM(entries)` drift. The drift check is what makes materializing the balance a safe
  tradeoff rather than a hopeful one.
- **Behaviour.** Insufficient-funds rate; idempotency *collisions* (same key, different payload — a
  client defect, never normal) kept distinct from *replays* (healthy retries); lock wait time, which
  is the early warning for both contention and a lock-ordering regression.
- **Risk.** The total and the **age** of negative balances: this is credit exposure. One negative for
  a day is a collections case; one negative for a month is a write-off nobody decided on.

---

## Running it, and deploying it

> Not yet committed — the implementation has not reached the point where these are real. Described
> here because the approach is a decision, and decisions belong in this document.

**Locally.** `docker compose up` for PostgreSQL plus the service, so a reviewer needs Docker and
nothing else. Migrations run with Alembic on startup. Integration tests use `testcontainers`, so they
provision their own PostgreSQL and no developer has to remember to start one.

**In the cloud.** The service is stateless; all state is in PostgreSQL. That makes it a container on
ECS Fargate (or EKS) behind an ALB, with RDS PostgreSQL Multi-AZ, secrets in Secrets Manager, and
autoscaling on CPU plus request concurrency. Two properties earn their keep here:

- Because the service is stateless, scaling out is a number change. The correctness of concurrent
  writes is guaranteed by the database's row locks, not by anything in the application's memory —
  which is precisely why the locking design is not an implementation detail.
- Because deposits do not contend, throughput on the most common operation scales with the database
  rather than being pinned by a hot row.

**IaC.** Terraform, kept to the resources the service actually needs. The intent is to show the
deployment model is understood, not to ship a platform.

---

## Tech choices

| Choice | Why |
| --- | --- |
| Python 3.14 | Latest stable; dependency resolution verified against it before committing |
| uv | One tool for interpreter, virtualenv and dependencies, and it pins Python *in the project*, so the version is reproducible across laptop, container and CI |
| PostgreSQL (planned) | `SELECT ... FOR UPDATE` is the concurrency mechanism the design rests on |
| SQLAlchemy + Alembic (planned) | Declared as dependencies; no models or migrations written yet |
| ruff + mypy strict | Enforced in pre-commit, not suggested |
| pytest | Domain tested with no infrastructure. `testcontainers` is a declared dependency for the persistence and locking tests, which are not written yet |

### Getting started

```bash
uv sync                          # creates .venv, installs everything
uv run pytest                    # run the tests
uv run pre-commit install        # enable the gates
uv run pre-commit run --all-files
```

> Note: `pre-commit run --all-files` only inspects files **tracked by git**. A green run over
> untracked code proves nothing.

---

## AI usage

The instructions require every prompt and every response. Meeting that with a summary is not possible
— a summary omits, compresses, and silently drops turns — so it is met mechanically instead:

- **[`docs/ai-transcript.md`](docs/ai-transcript.md)** is generated, never written by hand. A hook
  rebuilds it from the session logs, so no turn can be dropped by anyone's judgement, including mine.
- Turns accumulate in an append-only store **inside the repository**, versioned by git. Session logs
  live outside the repo and are pruned on a retention schedule, so rendering directly from them would
  eventually overwrite a complete transcript with a shorter one. The transcript is reproducible from
  the repository alone.
- **[`docs/decision-log.md`](docs/decision-log.md)** is the curated layer: what was decided, why, and
  the verdict on each exchange. Two artifacts, two different questions.

The AI was directed rather than asked what to think, and its output was checked rather than
accepted. One instance is preserved in the log: asked to review the domain, it correctly found a
contradiction in the PRD, then proposed restating the authorization rule as "the caller must own
every `USER` leg" — a rule that would forbid sending money to another customer. The gap was real and
the proposed fix was not.

---

## What is not done, stated plainly

- `Account`, `Transfer` and `Entry` are specified but not implemented.
- No HTTP API, no persistence, no migrations, no containers, no IaC yet.
- The concurrency design is argued but not yet demonstrated under parallel load — and until it is, it
  is a claim. The success criteria in `docs/prd.md` §10 require that demonstration, not an assertion
  in a unit test.
- Open questions are listed in `docs/prd.md` §12 rather than quietly resolved.
