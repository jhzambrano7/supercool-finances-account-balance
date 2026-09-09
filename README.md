# SuperCool Finances — Account Balance Service

A service that owns customer account balances and the movements between them.

Everything here is subordinate to one goal: **no money is ever created, destroyed, or duplicated.**
Where a decision made the service simpler but that goal weaker, the decision was rejected — and the
rejection is written down, because the reasoning is the deliverable, not the code.

**Run it in one command**

```
docker compose up
```

Then open the **ops console at <http://localhost:5173>**, or the **API docs at
<http://localhost:8000/docs>**. PostgreSQL, the migrations, the API and the console all come up
together — see [Running it](#running-it-and-deploying-it) for what is going on inside.

**Where to look**

| Document | What it is |
| --- | --- |
| [`docs/prd.md`](docs/prd.md) | The product contract. Every rule, with its rejected alternative |
| [`docs/coding-conventions.md`](docs/coding-conventions.md) | How code is written, independent of feature — e.g. Tell, Don't Ask |
| [`openspec/changes/account-balance-domain/proposal.md`](openspec/changes/account-balance-domain/proposal.md) | The domain model design: aggregates, invariants, twelve decisions |
| [`docs/decision-log.md`](docs/decision-log.md) | Chronological record of what was decided and why |
| [`docs/ai-transcript.md`](docs/ai-transcript.md) | Every prompt and every response, verbatim (see [AI usage](#ai-usage)) |
| [`web/README.md`](web/README.md) | A React ops console for exercising the API in a browser — a demo aid, not a production deliverable |
| [`infra/README.md`](infra/README.md) | The AWS CDK stacks: what would be deployed, and the reasoning behind each choice |

---

## Status — read this first

The design is written down; the implementation is partial. This section says where the line is.

| Area | State |
| --- | --- |
| Product contract, domain model, decisions | **Done** — `docs/prd.md`, domain proposal |
| `Money` / `Currency` value objects | **Built**, 35 unit tests |
| Id generation (UUIDv7) | **Built** |
| Toolchain: Python 3.14 + uv, ruff, mypy strict, pre-commit, commit-msg gate | **Built and enforcing** |
| `Account`, `Transfer`, `Entry` aggregates | **Built** — see `openspec/specs/account-balance/spec.md` |
| Account opening: `AccountRegister`, `POST /accounts`, `SqlAccountRepository`, the `accounts` migration | **Built** — see `openspec/specs/account-opening/spec.md`. Unit tests (fake repository) and integration tests (real PostgreSQL via `testcontainers`) both pass locally |
| Transfers: `TransferMoney`, `POST /transfers`, `SqlTransferRepository`/`SqlIdempotencyRepository`, the transfer migration | **Built** — see `openspec/specs/transfer/spec.md`. Concurrent-locking tests run real parallel requests (`asyncio.gather`) against real PostgreSQL, not a mocked assertion |
| Deposits and withdrawals: `Deposit`/`Withdraw`, `POST /deposits`/`POST /withdrawals` | **Built** — thin wrappers over `TransferMoney` that resolve the platform's `FUNDING`/`SETTLEMENT` account server-side; the caller never supplies or hard-codes a `SYSTEM` account id |
| Reversal: `RevertTransfer`, `POST /transfers/{transfer_id}/reversals` | **Built** — see [below](#reversal-is-operator-only-simulated-by-one-fixed-admin-principal) |
| Reading accounts and movement history: `GET /accounts`, `GET /accounts/{id}`, `GET /accounts/{id}/movements` | **Built** — ownership-scoped, cursor-paginated |
| Closing an account: `CloseAccount`, `POST /accounts/{id}/close` | **Built** — the zero-balance check and the write happen under one lock (`AccountUnitOfWork`), so a deposit racing a close cannot slip between them |
| Demo web console (`web/`) | **Built** — a React ops UI exercising every capability above; see `web/README.md`. Deliberately untested: it is a presentation facility, not a deliverable |
| Observability | **Descoped** — designed, deliberately not built; see [below](#observability-designed-and-descoped) |
| Reconciliation check (`account.balance == SUM(entries)`) | **Built as a test** (`tests/integration/account_balance/test_reconciliation.py`), across deposit, withdrawal, transfer, reversal, replay and mixed sequences. The operational job is descoped with observability |
| Containers, local stack | **Built** — `docker compose up` runs PostgreSQL, the API and the console, with migrations applied and hot reload on both halves. `Dockerfile` also ships a non-root `runtime` target |
| IaC | **Built** — [`infra/`](infra/README.md), AWS CDK in Python: RDS, Secrets Manager, ECR, ECS/Fargate, ALB, autoscaling, and migrations applied during the deploy. `cdk synth` runs with no AWS account |

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

### Reversal is operator-only, simulated by one fixed admin principal

Reversing a transfer (`POST /transfers/{transfer_id}/reversals`) is not a customer operation — the
above authorization rule does not apply to it at all, deliberately: the use case never checks
whether the caller owns either leg (`openspec/specs/revert/spec.md`'s R1).

Instead, exactly one caller is authorized to reverse anything, checked by a real gateway/adapter
pair (`AuthorizationGateway` / `FixedAdminAuthorizationGateway`), not a role flag bolted onto the
existing caller-resolution mechanism. The one admin principal is:

```
00000000-0000-0000-0000-000000000003
```

Send it as `X-Caller-Id` to call the reversal endpoint. Any other caller gets `403`.

**This is a real, stated limitation, not a hidden one**: v1 has exactly one admin, hardcoded — not
a multi-operator system, not a real identity provider integration. Widening it to more than one
operator is a real design question this exercise does not ask for an answer to.

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
| Deposit/withdraw resolve the `SYSTEM` account server-side | Client supplies the `FUNDING`/`SETTLEMENT` account id | Hard-coding a platform id in every client duplicates knowledge the server already has, and turns a future re-seed into a client-side deploy |
| Movement history is cursor-paginated | Offset pagination | The ledger is append-only and read newest-first; an offset shifts under the reader every time anything posts |
| Reversal's operator check is a real `AuthorizationGateway`, not an override | Send the admin id automatically regardless of the caller | A caller that never has to prove it is the admin can never be shown a real `403` — the demo would fake the boundary it exists to demonstrate |

### Decisions that are not technical

| Choice | Reasoning |
| --- | --- |
| Fraud detection is out of scope | A bounded context of its own, and a large one. Emulating it would add noise, not signal |
| Transactional outbox is out of scope | Only justified once something downstream consumes the events — i.e. fraud |
| Authentication is a port with a simulated adapter | Not the core of the exercise; the seam is what matters, and a real IdP is a drop-in replacement |
| Multi-currency deliberately deferred | The model is shaped so it is an addition, not a rewrite — one account per currency already exists, and FX resolves via a quote obtained *outside* the transaction |
| Scope narrowed on purpose | The instructions say quality over quantity. A smaller surface, fully reasoned, beats a broad one that hand-waves the hard parts |

---

## Observability: designed, and descoped

**Decision: not built, on purpose.** The statement asks for correctness under concurrency, and does
not ask for instrumentation. Metrics, alert routing and health endpoints are a feature in their own
right — one large enough that building a shallow version of it would say less about how this service
was reasoned about than leaving the design visible and the scope honest.

What survives the cut is the part that carries the reasoning: [`docs/prd.md` §11](docs/prd.md) still
specifies the signals, because *which* signals a ledger needs is the interesting judgement, and it
does not depend on the exporter.

- **Correctness — should be flat; alert on the first occurrence, not a threshold.** Ledger imbalance,
  and `balance ≠ SUM(entries)` drift.
- **Behaviour.** Insufficient-funds rate; idempotency *collisions* (same key, different payload — a
  client defect, never normal) kept distinct from *replays* (healthy retries); lock wait time, which
  is the early warning for both contention and a lock-ordering regression.
- **Risk.** The total and the **age** of negative balances: this is credit exposure. One negative for
  a day is a collections case; one negative for a month is a write-off nobody decided on.

**One signal was not descoped with the rest.** Balance drift is not an observability nicety — it is
the mitigation §5.1 committed to when it chose to materialize the balance, and dropping it would
leave the cost of that tradeoff with nothing paying for it. So it exists as a test
(`tests/integration/account_balance/test_reconciliation.py`) rather than as a metric: the cheap half,
which catches the bug class in CI, kept; the expensive half — the periodic operational sweep and its
alerting — descoped with everything else here.

That distinction is the whole reason drift is worth checking at all. Double-entry integrity (I1)
cannot break by construction, and `SYSTEM` accounts cannot drift because they never materialize a
balance (§5.3). Only the denormalized `USER` column can lie, and only a write-path bug can make it —
`SqlAccountRepository.update()` shipped once with `status` silently missing from its `.values(...)`,
one column away from being exactly that bug.

---

## Running it, and deploying it

**Locally: `docker compose up`, and that is the whole command.** It brings up PostgreSQL, applies
the migrations, and starts both application services. Nothing else to install, no migration step to
remember.

| | |
| --- | --- |
| **Ops console** | **<http://localhost:5173>** — start here; every capability is exercisable from the browser |
| **API docs** (OpenAPI / Swagger UI) | **<http://localhost:8000/docs>** — the generated contract, and a place to issue requests directly |
| API | <http://localhost:8000> |
| PostgreSQL | `localhost:5432` (`postgres` / `postgres`, database `account_balance`) |

Both application services mount their source and reload on change, so editing a use case or a screen
on the host takes effect without a rebuild. `docker compose down -v` removes the stack and its data.

Three choices in there are worth naming, because each removes a class of "works on my machine":

- **Neither service mounts its dependency tree.** The API keeps its virtualenv at `/opt/venv` and the
  console keeps `node_modules` at `/`, both outside the mounted path. Node and Python both resolve
  upward, so this needs no configuration — and it means the mount can be the plain `./src:/app/src`
  it appears to be, with no anonymous volume that silently goes stale on the next lockfile change.
  Without it, a host `.venv` full of macOS binaries would shadow the image's Linux one.
- **Migrations run in the `dev` entrypoint, not the `runtime` one.** A developer who has to remember
  a separate `alembic upgrade head` eventually will not, and will then debug a missing relation that
  is not a bug. In production the opposite is true: a schema mutating as a side effect of a process
  booting is an incident waiting for its first bad rollout, so `runtime` never migrates.
- **File watching is forced to polling** (`WATCHFILES_FORCE_POLLING`). Docker Desktop does not
  forward inotify events reliably from a macOS host; without this the reloader stays silent and hot
  reload appears to work right up until it matters.

The `Dockerfile` also carries a `runtime` target — non-root, no dev dependencies, no reloader — off
the same base layers, so what runs locally is not a different lineage from what would ship.

**Running it without Docker** still works and is unchanged: `docker compose up -d postgres`,
`uv run alembic upgrade head`, then `uv run uvicorn`. Integration tests never touch this stack at
all — `testcontainers` provisions their own PostgreSQL, so the suite is not coupled to a running
compose project.

**In the cloud: [`infra/`](infra/README.md), AWS CDK in Python.** `npx cdk synth` runs with no AWS account,
so the templates can be read without deploying anything. Two stacks: RDS PostgreSQL Multi-AZ with
its generated, rotated Secrets Manager credentials; and ECR, ECS/Fargate behind an ALB, autoscaling,
and the migration task.

The service is stateless — all state is in PostgreSQL — so scaling out is a number change, and the
correctness of concurrent writes is guaranteed by the database's row locks rather than by anything
in the application's memory. That is precisely why the locking design is not an implementation
detail. Because deposits do not contend (§5.3), throughput on the most common operation scales with
the database rather than being pinned by a hot row.

Four decisions there are worth the click; `infra/README.md` argues each in full:

- **The scaling ceiling is derived from the database, not chosen.** Scaling out ECS does not scale
  RDS: past ~24 tasks the extra ones exhaust the connection pool, and the failure mode is refused
  connections on perfectly valid money movements. `maxCapacity` is computed from `max_connections`
  and the per-task pool size, so raising it forces the database sizing conversation it really is.
- **Scaling is driven by requests per target, not CPU.** A transfer spends its time waiting on a row
  lock, not burning CPU — under real contention the tasks look idle while latency climbs, so a
  CPU-first policy scales exactly when it is least useful.
- **Migrations run inside `cdk deploy`, and gate the service.** That is what the `runtime` image
  target refusing to migrate on boot was for — but a declared migration task that nothing invokes
  is the same as no migration, so a custom resource starts it, polls it, and fails the deployment
  if it exits non-zero.
- **Networking is imported, never created.** A VPC outlives the services in it; `cdk destroy` on
  something deployed daily must not be able to take the network with it.

**Not in `infra/`:** the ops console (a presentation facility, not something to operate), an HTTPS
listener (no certificate in a placeholder account), a pipeline, and metrics or alarms —
observability stays descoped, and `/health`/`/ready` exist because a target group cannot be created
without them.

---

## Tech choices

| Choice | Why |
| --- | --- |
| Python 3.14 | Latest stable; dependency resolution verified against it before committing |
| uv | One tool for interpreter, virtualenv and dependencies, and it pins Python *in the project*, so the version is reproducible across laptop, container and CI |
| PostgreSQL | `SELECT ... FOR UPDATE` is the concurrency mechanism the design rests on |
| SQLAlchemy + Alembic | Async ORM plus migrations; two revisions built (`accounts`, then the transfer tables) |
| dependency-injector | One process-wide `SharedDependencies` container (settings/engine/session factory/clock/id generator) composed into each module's own container, rather than one connection pool per module |
| ruff + mypy strict | Enforced in pre-commit, not suggested |
| pytest | Domain tested with no infrastructure; `testcontainers` provisions a real PostgreSQL for the persistence and locking tests |

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

- **Observability is descoped, not forgotten** — the signals are specified (`docs/prd.md` §11,
  [above](#observability-designed-and-descoped)); nothing exports them, and there is no
  `/health`/`/ready`. This is a scope decision, taken because the statement asks for correctness
  under concurrency and instrumentation is a feature of its own size.
- **The reconciliation *job* is descoped; the reconciliation *test* is not.** The invariant
  `account.balance == SUM(entries)` is asserted in CI across every money-movement path; the periodic
  operational sweep `docs/prd.md` §5.1 also names is not built.
- **The CDK stacks have never been deployed.** They synthesize, and the templates say exactly what would be created, but no AWS account has run them — so this is reviewed infrastructure, not proven infrastructure. No pipeline, no HTTPS listener, no alarms; `infra/README.md` lists what is deliberately absent and why.
- Open questions are listed in `docs/prd.md` §12 rather than quietly resolved.
