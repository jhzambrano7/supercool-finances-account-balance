# Proposal: Account Balance — Domain Model

The domain layer of the account balance service: `Account`, `Transfer`, `Entry`, their value objects
and their errors. The load-bearing idea is that **a balance cannot move without an entry that explains
it, and an entry cannot exist outside a transfer that nets to zero** — G1 and I1 become structural
properties of the types, not assertions someone remembers to write.

Everything below is subordinate to `docs/prd.md`. Where this document disagrees with the PRD, it says
so explicitly (see §7).

---

## 1. Intent

The PRD settles *what* must be true. Nothing yet expresses it in code beyond `Money`/`Currency`. Until
the domain exists, every invariant I1–I7 is a paragraph, and paragraphs do not fail a test run.

This change defines the types, behaviours and enforcement points so that:

- I1–I5 and I7 are enforced by construction or by a single named method, testable with zero infrastructure (PRD §10.6).
- The use case layer (out of scope) has nothing left to decide about correctness — only about orchestration, locking and idempotency.
- The FX extension of PRD §8 is an addition of legs, not a reshaping of the ledger.

## 2. Scope

**In scope** — `src/modules/account_balance/domain/`: aggregates, value objects, identifiers, domain
errors, the invariant enforcement map, aggregate/consistency boundaries, reversal semantics.

**Out of scope** — use cases, HTTP/SQL adapters, migrations, locking and transaction management
(PRD §5), idempotency storage (PRD §6), FX (PRD §8), authn/authz wiring (PRD §9). Referenced only
where a domain decision constrains them (§7).

## 3. The shape

| Type | Kind | Lives in |
| --- | --- | --- |
| `Account` | Aggregate root (mutable entity) | `domain/account.py` |
| `Transfer` | Aggregate root (frozen), owns its `Entry` legs | `domain/transfer.py` |
| `Entry` | Entity inside `Transfer`, references `Account` by id | `domain/entry.py` |
| `AccountId`, `TransferId`, `EntryId`, `OwnerId` | Value objects over `UUID` | `domain/identifiers.py` |
| `AccountType`, `AccountStatus`, `EntryDirection`, `OverdraftPolicy` | Enums (with behaviour) | with their owner |
| `IdempotencyKey` | Value object | `domain/identifiers.py` |
| `post_transfer`, `post_reversal` | Domain service (module-level functions) | `domain/posting.py` |

`Money` and `Currency` are reused from `shared` unchanged.

## 4. Decisions

Each decision states the alternative it beat. A decision without a loser was not a decision.

### D1 — USER vs SYSTEM is a classification; the negative-balance rule is a *policy* derived from it

`Account.account_type: AccountType` (`USER` | `SYSTEM`) is descriptive data the API must expose anyway.
I2 is enforced by `AccountType.overdraft_policy -> OverdraftPolicy`, an enum with one behaviour:

```python
class OverdraftPolicy(Enum):
    FORBIDDEN = auto()
    UNLIMITED = auto()

    def assert_allows(self, resulting_balance: Money) -> None: ...  # raises InsufficientFundsError
```

`Account.debit()` computes the resulting balance and asks the policy. One call site, one rule, and the
rule is unit-testable without constructing an `Account`.

- **Rejected — `if self.account_type is AccountType.USER: ...` inside `debit()`.** Welds the invariant
  to the classification. The day a second overdraft-capable type appears (an FX settlement account,
  PRD §8), the fix is an edit inside the most dangerous method in the service rather than a new mapping.
- **Rejected — `UserAccount` / `SystemAccount` subclasses with a polymorphic `debit()`.** The
  type-level guarantee evaporates at the boundary: every repository and use case signature is
  `Account` anyway, and reconstitution needs a factory switch on a persisted discriminator — i.e. the
  enum, reintroduced, plus a class hierarchy on top of it.
- **Extension path:** an authorized overdraft limit becomes a third policy carrying a floor, not a
  change to `debit()`.

### D2 — Sign convention: CREDIT increases the account balance, DEBIT decreases it, for *every* account type

Uniform, holder's-perspective signing. A `SYSTEM` funding account therefore goes negative as it funds
customers, and that negative balance *is* the external obligation (PRD §4.1).

- **Rejected — strict accounting-equation signing** (debits increase assets, credits increase
  liabilities, so a customer's deposit is a credit that *increases* a liability). Correct for producing
  financial statements, which this service does not produce. It would make the sign of a balance change
  depend on the account's type — a permanent, silent bug factory for zero benefit here.

This convention is the single most dangerous thing to get wrong silently, so it is asserted directly:
`Entry.signed_amount` is `+amount` for `CREDIT` and `-amount` for `DEBIT`, and `Account.apply(entry)`
dispatches on direction. There is no second place where a sign is chosen.

### D3 — `Entry` carries a strictly positive `Money` plus a `direction`, not a signed `Money`

Direction is a first-class term for anyone reading the ledger (PRD §7, "read movement history"), and a
positive-only amount makes a zero or negative entry unrepresentable.

- **Rejected — signed amount only, direction derived.** Forces every consumer to re-derive the term the
  domain language already has, and admits an entry of amount `0` that means nothing.

### D4 — I1 is generalized to *per-currency netting*, and it is a `__post_init__` guard on `Transfer`

```python
def __post_init__(self) -> None:
    # I1: for every currency c present, sum(signed_amount of entries in c) == Money.zero(c)
```

In v1 (single currency, two legs) this degenerates exactly to `sum(debits) == sum(credits)`. It is
stated in the general form because it is the form that survives PRD §8: a cross-currency transfer is
four legs — `-100 USD` user / `+100 USD` FX system, `-92 EUR` FX system / `+92 EUR` user — and each
currency nets to zero only once the spread is booked against the `SYSTEM` FX account. The invariant that
already holds is the one that makes the PRD's "the difference does not vanish, it is *accounted for*"
mechanically true instead of aspirational.

- **Rejected — validating `debit_total == credit_total` on a single amount field.** Passes in v1 and
  becomes meaningless the moment two currencies appear.

### D5 — `Transfer` owns its entries and is frozen; posting is a domain service, not a classmethod

```python
def post_transfer(
    *, transfer_id: TransferId, source: Account, destination: Account, amount: Money,
    requested_by: OwnerId, idempotency_key: IdempotencyKey, occurred_at: datetime,
    entry_ids: tuple[EntryId, EntryId],
) -> Transfer: ...
```

It validates I3/I4/I5, builds the two `Entry` legs, calls `source.apply(debit_leg)` and
`destination.apply(credit_leg)` — which is where I2 fires — and returns the `Transfer`. Because the
balances are moved *inside* the same function that produces the entries, an entry without a balance
change (or the reverse) cannot be produced by any code path the use case can reach.

- **Rejected — `Transfer.post(...)` as a `@classmethod`.** It mutates two of its arguments while
  presenting itself as a constructor. A named module-level function that says "post to the ledger"
  does not lie about its effects.
- **Rejected — the use case assembling the entries and calling `account.debit()` itself.** Moves the
  coupling between "money moved" and "entry written" out of the domain and into orchestration code,
  which is exactly the seam G1 exists to close.
- `Transfer.__post_init__` still guards I1/I3/I5 unconditionally, so even a hand-constructed or
  repository-reconstituted `Transfer` cannot exist unbalanced. The factory is the ergonomic path; the
  guard is the guarantee.

### D6 — The domain takes no ambient dependencies: ids and time are arguments

`entry_ids` and `occurred_at` are passed in by the caller. No `datetime.now()`, no `IdGenerator` call
inside the domain.

- **Why:** identity generation is already an application service (`shared/application/services/id_generator.py`,
  UUIDv7 by decision), and a domain that reaches for a clock is a domain whose tests need fakes to
  assert arithmetic.
- **Rejected — injecting `Clock` and `IdGenerator` ports into the factory.** Correct but heavier: two
  ports wired into every unit test to supply what are, at the call site, two values.
- `entry_ids: tuple[EntryId, EntryId]` is deliberately arity-typed. The FX posting function of PRD §8
  will be a *different* function with its own leg count and its own tuple; a `Sequence` with a runtime
  length check would trade a compile-time guarantee for a generality we do not need yet.

### D7 — Identifiers are frozen dataclasses over `UUID`, not bare `UUID` and not `NewType`

A shared `EntityId` base with `value: UUID`; `AccountId` additionally `order=True`.

- **Why ordered:** PRD §5 step 3 requires deterministic lock ordering *by account id*. That ordering is
  a property of the identifier, so it lives on the identifier.
- **Rejected — bare `UUID`.** mypy strict cannot stop `repo.get(transfer_id)` on an account repository
  when every id is the same type.
- **Rejected — `NewType`.** Checked by mypy, but erased at runtime, so a wrong id crossing an adapter
  boundary from deserialized input is never caught, and there is nowhere to hang ordering or `__str__`.
  Dataclass `__eq__` compares `other.__class__ is self.__class__`, so `AccountId(u) != TransferId(u)` at
  runtime as well as at type-check time.

### D8 — `Account.open()` always starts at zero; reconstitution is a separate, named path

```python
@classmethod
def open(cls, *, account_id: AccountId, owner_id: OwnerId,
         account_type: AccountType, currency: Currency) -> Account: ...      # balance = Money.zero
@classmethod
def reconstitute(cls, *, ..., balance: Money, status: AccountStatus, version: int) -> Account: ...
```

An account cannot be born holding money — money only ever arrives through an entry (G1). This applies
to `SYSTEM` accounts too: a funding account starts at zero and goes negative by funding, which is what
makes its balance an auditable obligation rather than an opening assumption.

`reconstitute` exists because the repository must rebuild a non-zero account and the raw constructor
should not be the documented way to do it. Naming it makes "created" and "loaded" visibly different at
the call site.

### D9 — `Transfer` has no status field

PRD §5: a transfer is born confirmed, with no observable intermediate state. A `TransferStatus` enum
whose only reachable value is `POSTED` is a state machine that invites handling of states the design
forbids. The `status` column in the PRD §6.1 idempotency record is the status of the *request*, and it
belongs to that record, not to the ledger.

- **Rejected — `PENDING | POSTED | FAILED`.** Would model the state machine PRD §5 and §8.2 explicitly
  defer to external settlement.

### D10 — Reversal is an ordinary transfer that points at its original, and is not privileged

```python
def post_reversal(original: Transfer, *, transfer_id: TransferId, source: Account,
                  destination: Account, requested_by: OwnerId, idempotency_key: IdempotencyKey,
                  occurred_at: datetime, entry_ids: tuple[EntryId, EntryId]) -> Transfer: ...
```

Mirror legs, same amount and currency, `reverses=original.id` (I7). The original is never touched. Full
reversal only — a partial reversal is just another transfer and does not need a concept.

Consequence, stated plainly because it is a product-visible behaviour and the PRD does not cover it:
**a reversal is subject to I2 like anything else.** If the original destination is a `USER` account that
has since spent the funds, the reversal is refused with `InsufficientFundsError`. Exempting reversals
would mean punching a hole in I2, and an invariant with an exemption is not an invariant. See §8, Q3.

### D11 — `Entry` does not store `balance_after`

Tempting for audit, and it would give a per-entry reconciliation anchor. Rejected: PRD §5.1 already
accepts exactly one materialized projection (`accounts.balance`) as a controlled, reconciled risk. A
second projection multiplies the drift surface without adding a check that `SUM(entries)` does not
already provide, and it is only meaningful under a total per-account ordering. Additive later if the
audit surface demands it.

### D12 — `Transfer` carries `idempotency_key` and `requested_by`

PRD §4.2 says so, and PRD §6.1 stores the key again on the idempotency record — a real duplication
worth justifying rather than inheriting. It stays on the `Transfer` because §6.2 *prunes* idempotency
records on a retention schedule: if the key lived only there, every transfer would lose the record of
which client intent produced it the moment its record expired. Provenance belongs in the append-only
ledger, not in the table with a delete policy.

## 5. Aggregate and consistency boundaries

| Boundary | Rule |
| --- | --- |
| `Account` | Root. Owns `balance`, `status`, `version`. References `owner_id` by id. |
| `Transfer` | Root. Owns its `Entry` legs (created only by `post_transfer` / `post_reversal`). References accounts by `AccountId`, never by object, once constructed. |
| `Entry` | Never a root. No repository of its own for writes; the account movement history is a **read model** over entries, not aggregate traversal. |

**Deliberate deviation:** one transaction mutates two `Account` aggregates and creates one `Transfer`.
That breaks the usual "one aggregate per transaction" guideline, knowingly. The alternative — a saga or
process manager across accounts — buys eventual consistency, which is precisely what PRD §5 rejects, and
would reintroduce the suspended-money state the design exists to avoid. The row locks of PRD §5 step 3
make the multi-aggregate transaction safe; the guideline is a heuristic for availability, and here
correctness wins.

## 6. Invariant enforcement map

| ID | Enforced by | Raises |
| --- | --- | --- |
| I1 | `Transfer.__post_init__` — per-currency signed sum is zero (D4) | `UnbalancedTransferError` |
| I2 | `Account.debit()` → `OverdraftPolicy.assert_allows(resulting)` | `InsufficientFundsError` |
| I3 | `Transfer.__post_init__` (`amount.is_positive`) and `Entry.__post_init__` | `NonPositiveAmountError` |
| I4 | `post_transfer` (source/destination/amount currencies) + `Account.debit`/`credit` (leg vs account currency) | `CurrencyMismatchError` *(shared)* |
| I5 | `Transfer.__post_init__` (source ≠ destination) | `SelfTransferError` |
| I6 | Structural — `Entry` is `frozen=True, slots=True`; `Transfer.entries` is a `tuple`. No mutator exists. Persistence is the second line (PRD §4.4) | — |
| I7 | Structural — `post_reversal` produces a new `Transfer`; nothing can mutate an existing one | — |
| G5 | `Account.assert_owned_by(owner_id)` — the *fact*; the *policy* of when to call it is the use case's (see §7) | `AccountOwnershipError` |

Additional guards: `Account.assert_operable()` (status) and `Entry`/`Account` id agreement in
`Account.apply()`.

## 7. Domain errors

All derive from `shared.domain.DomainError`, `Error` suffix per ruff N818.

| Error | Meaning | Class |
| --- | --- | --- |
| `InsufficientFundsError` | I2 — the debit would take a `USER` account below zero | Client |
| `NonPositiveAmountError` | I3 — a transfer or entry amount is ≤ 0. `Money` permits negatives by design (SYSTEM balances); this is the separate rule that transfer *amounts* are positive | Client |
| `SelfTransferError` | I5 — source and destination are the same account | Client |
| `AccountNotOperableError` | The account's status forbids movement | Client |
| `AccountOwnershipError` | G5 — the requester does not own the account | Client |
| `UnbalancedTransferError` | I1 violated at construction. **If this ever raises in production it is our bug, not the caller's** | Internal |
| `EntryAccountMismatchError` | `apply()` was handed an entry for another account. Internal guard | Internal |
| `CurrencyMismatchError` | I4 — reused from `shared`, not redefined | Client |

The Client/Internal column is a domain fact (whose fault is it?), not an HTTP concern. Mapping it to
status codes is the inbound adapter's job and is out of scope.

## 8. What this model cannot enforce — and one PRD gap

**(a) PRD §5 step 1 is false for deposits.** It says "assert the caller owns the *source* account". A
deposit is a transfer from a `SYSTEM` funding account into a `USER` account (PRD §7); the caller does not
own the source, so the rule as written either blocks every deposit or must be silently skipped for it —
and a rule with a silent exception is how vaults get opened. The enforceable restatement of G5 is:

> **The caller must own every `USER`-typed account leg of the transfer.**

Withdrawal and P2P collapse to "owns the source"; deposit resolves to "owns the destination"; a
hypothetical SYSTEM→SYSTEM movement is operator-only. The domain supplies `Account.assert_owned_by`;
choosing the legs is a use-case policy. **Needs the user's verdict (Q1).**

**(b) Double reversal is not preventable inside an aggregate.** "Has this transfer already been
reversed?" is a question about *other* `Transfer` instances. No aggregate can answer it. Enforcement
must be a partial unique index on `transfers.reverses` plus a use-case check — a one-line constraint on
the out-of-scope persistence work, recorded here so it is not discovered later.

**(c) I6 is only in-process immutability here.** Frozen dataclasses stop our code; append-only is a
persistence guarantee (no UPDATE/DELETE path), exactly as PRD §4.4 states.

**(d) `SYSTEM` accounts have no human owner.** Decided: they carry a reserved platform `OwnerId`
constant, keeping `owner_id` total and `assert_owned_by` uniform. Rejected `OwnerId | None`, which would
push an optional through every USER path to describe two rows.

## 9. Open questions — need the user's decision

| # | Question | Recommendation |
| --- | --- | --- |
| Q1 | G5 restated as "owns every `USER` leg" (§8a)? | Adopt — the PRD rule as written cannot be implemented for deposits |
| Q2 | Is account closure in v1 (PRD §11)? If yes, `AccountStatus = {ACTIVE, CLOSED}` and `Account.close()` requires a zero balance | Include, with the zero-balance rule — it answers the PRD's own question cheaply |
| Q3 | A reversal that would overdraft the original destination: refuse (D10), or allow an operator-authorized correction to breach I2? | Refuse. Some reversals then become impossible and must be resolved commercially, not by a ledger exception |
| Q4 | Reversal authorization — payer or operator (PRD §11)? Does not block the domain model, but decides which `requested_by` is legal | Defer to the use-case phase |
| Q5 | Module name `account_balance` (matches the repo and the PRD title) | Adopt |

## 10. Capabilities

### New Capabilities
- `account`: the `Account` aggregate — opening, crediting, debiting under an overdraft policy, ownership assertion, status, version.
- `transfer-posting`: balanced transfer construction (I1/I3/I4/I5), the posting domain service, and reversal as a compensating transfer (I7).
- `ledger-entry`: the immutable `Entry` — direction, sign convention, positivity, transfer/account references.

### Modified Capabilities
None. `shared`'s `Money`/`Currency` are consumed unchanged.

## 11. Affected areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/modules/account_balance/` | New | Module created from the `_blank` scaffold |
| `src/modules/account_balance/domain/{account,transfer,entry,posting,identifiers,errors}.py` | New | This proposal |
| `src/modules/shared/domain/errors.py` | Modified | Possibly hosts `EntityId`-related shared errors; `DomainError` and `CurrencyMismatchError` reused unchanged |
| `tests/unit/account_balance/domain/` | New | Invariant tests, infrastructure-free (PRD §10.6) |
| Persistence, use cases, adapters | Constrained, not changed | Partial unique index on `transfers.reverses` (§8b); no UPDATE/DELETE on entries (I6); use case must call `assert_owned_by` per Q1 |

## 12. Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Sign convention (D2) applied inconsistently in a later adapter | Med | One derivation point (`Entry.signed_amount`); property test asserting a round-trip transfer leaves the pair's total unchanged |
| `post_transfer` mutating its `Account` arguments surprises a caller | Med | Named domain service rather than a classmethod (D5); `Transfer.__post_init__` guarantees balance regardless |
| `OverdraftPolicy` judged over-engineered for two cases | Low | It is an enum with one method; the fallback (a property on `AccountType`) is a mechanical downgrade |
| I1 generalized form is untested until FX exists | Low | Property-test the netting rule with synthetic multi-currency leg sets now (PRD §10.1) |
| Q1 unresolved blocks the deposit use case | High if unanswered | Flagged as a decision, not assumed |

## 13. Rollback

The module is new and imported by nothing. Deleting `src/modules/account_balance/` and its tests
restores the current state; `shared` is touched additively only. No migrations, no data.

## 14. Success criteria

- [ ] Every row of §6 has at least one unit test that fails when the guard is removed.
- [ ] `Transfer` cannot be instantiated unbalanced by any path, including the raw constructor (property-tested, PRD §10.1).
- [ ] No domain module imports from `application` or `adapters`; no `datetime.now()` and no id generation inside `domain/` (D6).
- [ ] `mypy --strict` and `ruff` pass on the new module.
- [ ] A `USER` account cannot be driven below zero through any public domain method; a `SYSTEM` account can (I2, PRD §4.1).
- [ ] Q1–Q3 answered before the use-case phase begins.
