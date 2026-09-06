# Design: Account Balance — Domain Layer

How to build `src/modules/account_balance/domain/`. The proposal settles *what* the types are and
*why*; this settles the exact modules, signatures, enforcement points and tests, at a level where the
only thing left is to type it out — test first, since the project runs in strict TDD mode.

Scope is the domain layer only. Use cases, HTTP adapters, SQL persistence, migrations and DI wiring
are out of scope and are named here **only** where a domain decision constrains them (§8).

What exists today: `Money`, `Currency`, `DomainError` + three shared errors, and `IdGenerator`.
Nothing else. Every type below is new.

---

## 1. Module layout

Import direction is strictly downward — no module imports one below it, so there are no cycles.

| File | Contains | Imports from |
| --- | --- | --- |
| `domain/errors.py` | the domain error taxonomy (§7) | `shared.domain.errors` |
| `domain/identifiers.py` | `EntityId`, `AccountId`, `TransferId`, `EntryId`, `OwnerId`, `IdempotencyKey`, `PLATFORM_OWNER_ID` | `errors` |
| `domain/entry.py` | `EntryDirection`, `Entry` | `identifiers`, `errors`, `shared.domain.money` |
| `domain/account.py` | `AccountType`, `AccountPurpose`, `AccountStatus`, `OverdraftPolicy`, `PendingApplication`, `Account` | `entry`, `identifiers`, `errors`, `money` |
| `domain/transfer.py` | `Transfer` | `entry`, `identifiers`, `errors`, `money` |
| `domain/posting.py` | `post_transfer`, `post_reversal` | `account`, `transfer`, `entry`, `identifiers`, `errors` |

Plus `src/modules/account_balance/__init__.py` and `domain/__init__.py` (the project uses explicit
`__init__.py` everywhere — no namespace packages). Tests mirror the layout under
`tests/unit/account_balance/domain/` with **no** `__init__.py`, matching `tests/unit/shared/`.

No `from __future__ import annotations` anywhere: on 3.14 PEP 649 already defers evaluation, and the
future import would downgrade annotations to strings.

---

## 2. Identifiers (D7)

```python
@dataclass(frozen=True, slots=True)
class EntityId:
    value: UUID
    def __post_init__(self) -> None: ...      # rejects non-UUID
    def __str__(self) -> str: return str(self.value)

@dataclass(frozen=True, slots=True, order=True)
class AccountId(EntityId): ...                # order=True for PRD §5 lock ordering

@dataclass(frozen=True, slots=True)
class TransferId(EntityId): ...
class EntryId(EntityId): ...
class OwnerId(EntityId): ...

PLATFORM_OWNER_ID: Final = OwnerId(UUID(int=0))
```

Notes that decide real behaviour:

- Subclasses declare **no** new fields, so `order=True` on `AccountId` generates comparisons over the
  inherited `value`; `UUID` is orderable, so this is total.
- Dataclass `__eq__` compares `other.__class__ is self.__class__`, so `AccountId(u) != TransferId(u)`
  at runtime as well as under mypy. This is the concrete reason D7 rejected `NewType`.
- `frozen` must be consistent across the hierarchy — a non-frozen subclass of a frozen dataclass is a
  `TypeError` at class creation. All are frozen.
- Identifiers deliberately do **not** validate the UUID version. UUIDv7 is the `IdGenerator`'s policy
  (application layer); the domain must also accept ids that were minted elsewhere, which is what makes
  `PLATFORM_OWNER_ID` (the nil UUID, never issued by any generator) safe to reserve for `SYSTEM`
  accounts (proposal §8d).
- `IdempotencyKey` is a frozen dataclass over `str`, rejecting empty/blank and anything over 255
  characters with `InvalidIdempotencyKeyError`.

---

## 3. `Entry` — the only place a sign is chosen (D2, D3)

```python
class EntryDirection(Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"

@dataclass(frozen=True, slots=True)
class Entry:
    entry_id: EntryId
    transfer_id: TransferId
    account_id: AccountId
    direction: EntryDirection
    amount: Money                 # strictly positive (I3)
    occurred_at: datetime         # timezone-aware

    def __post_init__(self) -> None: ...
    @property
    def signed_amount(self) -> Money: ...   # +amount for CREDIT, -amount for DEBIT
```

`__post_init__` raises `NonPositiveAmountError` unless `amount.is_positive`, and
`NaiveTimestampError` when `occurred_at.tzinfo is None`. Ruff's `DTZ` rules police *calls* such as
`datetime.now()`; they cannot police a naive value handed in from an adapter, so the runtime guard is
not redundant with the linter.

`signed_amount` is the sole derivation of sign in the service. `Account` never inspects `direction`
to decide a sign — it adds `signed_amount` — so D2 has exactly one enforcement point.

**Field naming**: every identifier field is domain-qualified (`entry_id`, not `id`). This avoids
depending on how ruff's `A` rules currently treat class attributes shadowing builtins, and keeps
constructor keywords identical to field names.

---

## 4. `Account` — an entity, not a dataclass

`Account` is written as an explicit class with `__slots__`, **not** a dataclass, for one reason that
matters: a dataclass generates value equality, and two loads of the same account with different
balances are the *same account*. Entity equality is by id.

```python
class Account:
    __slots__ = ("_account_id", "_owner_id", "_account_type", "_purpose",
                 "_currency", "_balance", "_status", "_version")

    def __init__(self, *, account_id: AccountId, owner_id: OwnerId,
                 account_type: AccountType, purpose: AccountPurpose,
                 currency: Currency, balance: Money,
                 status: AccountStatus, version: int) -> None: ...

    @classmethod
    def open(cls, *, account_id: AccountId, owner_id: OwnerId,
             account_type: AccountType, purpose: AccountPurpose,
             currency: Currency) -> Self: ...

    @classmethod
    def reconstitute(cls, *, account_id: AccountId, owner_id: OwnerId,
                     account_type: AccountType, purpose: AccountPurpose,
                     currency: Currency, balance: Money,
                     status: AccountStatus, version: int) -> Self: ...

    # read-only projections
    @property
    def account_id(self) -> AccountId: ...
    @property
    def balance(self) -> Money: ...
    @property
    def status(self) -> AccountStatus: ...
    @property
    def version(self) -> int: ...

    # the only three ways a balance can move
    def credit(self, entry: Entry) -> PendingApplication: ...
    def debit(self, entry: Entry) -> PendingApplication: ...
    def debit_for_reversal(self, entry: Entry) -> PendingApplication: ...

    # guards the use case calls; the *fact*, not the policy
    def assert_owned_by(self, owner_id: OwnerId) -> None: ...     # AccountOwnershipError
    def assert_operable(self) -> None: ...                        # AccountNotOperableError
    def close(self) -> None: ...

    def __eq__(self, other: object) -> bool: ...   # class + account_id
    def __hash__(self) -> int: ...                 # hash(account_id)
```

`__eq__` must be typed `(self, other: object) -> bool` under mypy strict, and defining `__eq__`
without `__hash__` sets `__hash__` to `None` — both are stated because both are easy to get wrong.

### 4.1 `open()` vs `reconstitute()` (D8)

Both funnel through `__init__`, which validates unconditionally: `purpose.account_type is
account_type` (`AccountPurposeMismatchError`), `balance.currency == currency`
(`CurrencyMismatchError`), `version >= 0`. The raw constructor stays guarded — the named classmethods
are the ergonomic path, not the guarantee.

| | `open()` | `reconstitute()` |
| --- | --- | --- |
| balance | forced to `Money.zero(currency)` — an account cannot be born holding money (G1) | taken from storage |
| status | `ACTIVE` | taken from storage |
| version | `0` | taken from storage |

`reconstitute()` **must not** re-assert a non-negative `USER` balance. A reversal legitimately leaves
one negative (PRD §7.3), so a repository that refused to load it would make the debt unrecoverable.
It must likewise accept `CLOSED`. This is the one asymmetry that is easy to add by reflex and wrong.

### 4.2 Applying an entry — validate everything, then mutate nothing that can fail

The review of 2026-09-05 flagged that mutating `source` and then `destination` leaves the first object
changed if the second raises. The fix is that the account methods **do not mutate**. They validate and
return a committed-nothing token:

```python
@dataclass(frozen=True, slots=True)
class PendingApplication:
    account: Account
    entry: Entry
    resulting_balance: Money
    def commit(self) -> None: ...   # assignment only — cannot raise
```

Every method shares one private preflight:

```python
def _prepare(self, entry: Entry, direction: EntryDirection) -> Money:
    self.assert_operable()                                   # AccountNotOperableError
    if entry.account_id != self._account_id: raise EntryAccountMismatchError(...)
    if entry.direction is not direction:     raise EntryDirectionMismatchError(...)
    return self._balance + entry.signed_amount               # CurrencyMismatchError from Money
```

and the three public methods differ only in which rule they add:

| Method | Direction required | Overdraft check |
| --- | --- | --- |
| `credit` | `CREDIT` | none — a credit cannot violate I2 |
| `debit` | `DEBIT` | `self._account_type.overdraft_policy.assert_allows(resulting, account_id=...)` |
| `debit_for_reversal` | `DEBIT` | **none** — the sole path permitted to cross zero |

`commit()` sets `_balance` and increments `_version`. It touches nothing else and has no failure mode,
which is what lets `post_transfer` commit two legs with no partial-mutation window (§5).

Currency agreement between the leg and the account (I4) falls out of `Money.__add__`, which already
raises the shared `CurrencyMismatchError`. There is no second currency check, because a second check
is a second thing that can disagree.

### 4.3 The `debit` / `debit_for_reversal` split (D1 as amended)

Two named methods rather than `debit(..., allow_overdraft=True)`: a boolean can be forwarded from
anywhere, a second method must be *named*. That makes the audit mechanical, and the design turns
"mechanical" into a test rather than a habit:

> **Architecture test**: parse every `.py` under `src/` with `ast`, collect every
> `Attribute`/`Name` node called `debit_for_reversal`, and assert the set of (module, enclosing
> function) pairs is exactly `{("account_balance/domain/account.py", <definition>),
> ("account_balance/domain/posting.py", "post_reversal")}`.

The same test asserts `PendingApplication.commit` is called only inside `posting.py`. Python cannot
make either unreachable — stating otherwise would be a lie — so the enforcement is a failing test on
the next call site, which is exactly what PRD §10.3 asks for ("a test asserts no other path can").

### 4.4 Policy and classification enums

```python
class OverdraftPolicy(Enum):
    FORBIDDEN = auto()
    UNLIMITED = auto()
    def assert_allows(self, resulting_balance: Money, *, account_id: AccountId) -> None: ...

class AccountType(Enum):
    USER = "USER"; SYSTEM = "SYSTEM"
    @property
    def overdraft_policy(self) -> OverdraftPolicy: ...   # USER→FORBIDDEN, SYSTEM→UNLIMITED

class AccountPurpose(Enum):
    CHECKING = "CHECKING"; SAVINGS = "SAVINGS"; FUNDING = "FUNDING"; SETTLEMENT = "SETTLEMENT"
    @property
    def account_type(self) -> AccountType: ...           # each purpose belongs to exactly one type

class AccountStatus(Enum):
    ACTIVE = "ACTIVE"; CLOSED = "CLOSED"
```

- Persisted enums (`AccountType`, `AccountPurpose`, `AccountStatus`, `EntryDirection`) carry **string
  values**, so a reordering of members cannot silently reinterpret stored rows. `OverdraftPolicy` is
  never persisted and uses `auto()`.
- Plain `Enum`, not `StrEnum`: a `StrEnum` member passes as a `str` anywhere, which invites it into a
  SQL parameter or a JSON body without an explicit `.value` and quietly couples storage to the member
  name.
- The type↔purpose mapping lives on `AccountPurpose` because PRD §4.4 says each purpose belongs to
  *exactly one* type — a total function, so validation is one identity comparison rather than a
  membership test against a set.
- `assert_allows` takes `account_id` so the raised error carries the account. The **message** stays
  free of amounts (PRD §11.4 — money movement logs must not carry balances); the amount, if a caller
  needs it, is the caller's own balance and belongs in a structured field, not in the string.

### 4.5 `close()` (PRD §7.2)

Refuses with `AccountNotOperableError` when the account is not `ACTIVE` **or** is a `SYSTEM` account
(infrastructure does not close), and with `AccountNotEmptyError` when the balance is non-zero. Sets
`_status = CLOSED` and bumps `_version`. Closure needs no idempotency key: a second attempt finds
`CLOSED` and is refused (PRD §6.3).

---

## 5. `Transfer` and the posting service (D4, D5, D9, D10, D12)

```python
@dataclass(frozen=True, slots=True)
class Transfer:
    transfer_id: TransferId
    source_account_id: AccountId
    destination_account_id: AccountId
    amount: Money
    requested_by: OwnerId
    idempotency_key: IdempotencyKey
    occurred_at: datetime
    entries: tuple[Entry, ...]
    reverses: TransferId | None = None

    def __post_init__(self) -> None: ...
```

No status field (D9). `entries` is `tuple[Entry, ...]`, not a 2-tuple, because FX (PRD §8) adds legs
without reshaping the type.

`__post_init__` guards, in order:

| Check | Raises |
| --- | --- |
| `amount.is_positive` (I3) | `NonPositiveAmountError` |
| `source_account_id != destination_account_id` (I5) | `SelfTransferError` |
| `len(entries) >= 2`; every `entry.transfer_id == transfer_id`; `source_account_id` appears among the debited legs and `destination_account_id` among the credited legs | `MalformedTransferError` |
| `occurred_at` is timezone-aware | `NaiveTimestampError` |
| **I1 — per-currency netting (D4)** | `UnbalancedTransferError` |

I1 in full, because it is the invariant the whole design exists for:

```python
totals: dict[Currency, Money] = {}
for entry in self.entries:
    currency = entry.amount.currency
    totals[currency] = totals.get(currency, Money.zero(currency)) + entry.signed_amount
if any(not total.is_zero for total in totals.values()):
    raise UnbalancedTransferError(...)
```

`Currency` is a frozen dataclass and therefore hashable, so it is a valid key. In v1 (one currency,
two legs) this degenerates exactly to `sum(debits) == sum(credits)`; the general form is what survives
the four-leg FX posting, and it is checked at *construction*, so no `Transfer` — hand-built or
repository-reconstituted — can exist unbalanced.

### 5.1 `post_transfer`

```python
def post_transfer(
    *,
    transfer_id: TransferId,
    source: Account,
    destination: Account,
    amount: Money,
    requested_by: OwnerId,
    idempotency_key: IdempotencyKey,
    occurred_at: datetime,
    entry_ids: tuple[EntryId, EntryId],
) -> Transfer: ...
```

Sequence, and the order is the whole point:

```
1. guard      source.account_id != destination.account_id      -> SelfTransferError
              amount.is_positive                               -> NonPositiveAmountError
              source.currency == destination.currency == amount.currency (I4)
2. build      debit_leg  = Entry(entry_ids[0], ..., source.account_id,      DEBIT,  amount, occurred_at)
              credit_leg = Entry(entry_ids[1], ..., destination.account_id, CREDIT, amount, occurred_at)
3. prepare    pending = (source.debit(debit_leg), destination.credit(credit_leg))   <- I2 fires here
4. construct  transfer = Transfer(..., entries=(debit_leg, credit_leg))             <- I1 fires here
5. commit     for application in pending: application.commit()
6. return     transfer
```

Steps 1–4 are pure: every rule that can refuse the operation has refused before step 5, and step 5
cannot fail. So the two things that must never disagree — the entries and the balances — are produced
by one function, and there is no window in which one exists without the other. That is D5, made
atomic in-process rather than merely co-located.

### 5.2 `post_reversal`

```python
def post_reversal(
    original: Transfer,
    *,
    transfer_id: TransferId,
    source: Account,          # the original's DESTINATION — this is the account being clawed back
    destination: Account,     # the original's SOURCE — the party being made whole
    requested_by: OwnerId,
    idempotency_key: IdempotencyKey,
    occurred_at: datetime,
    entry_ids: tuple[EntryId, EntryId],
) -> Transfer: ...
```

Identical to `post_transfer` with three differences:

1. `amount` is not a parameter — it is `original.amount`. A partial reversal is just another transfer
   and does not need a concept (D10).
2. Guards `source.account_id == original.destination_account_id` and
   `destination.account_id == original.source_account_id`, else `ReversalMismatchError` (Internal —
   the use case loaded the wrong rows).
3. Step 3 calls **`source.debit_for_reversal(debit_leg)`**, the only call site in the service. The
   resulting `Transfer` carries `reverses=original.transfer_id` (I7); `original` is never touched.

Reversing a *reversal* is deliberately not forbidden here — it is an ordinary correction. "At most one
reversal per transfer" is a question about other rows and is carried to persistence (§8).

---

## 6. Invariant enforcement map

| ID | Enforced by | Raises |
| --- | --- | --- |
| I1 | `Transfer.__post_init__`, per-currency netting | `UnbalancedTransferError` |
| I2 | `Account.debit` → `OverdraftPolicy.assert_allows`; `debit_for_reversal` is the sole crossing path | `InsufficientFundsError` |
| I3 | `Entry.__post_init__` and `Transfer.__post_init__` | `NonPositiveAmountError` |
| I4 | `post_transfer` (source/destination/amount) and `Money.__add__` inside `Account._prepare` (leg vs account) | `CurrencyMismatchError` *(shared)* |
| I5 | `post_transfer` and `Transfer.__post_init__` | `SelfTransferError` |
| I6 | Structural — `Entry` is `frozen=True, slots=True`; `entries` is a tuple; no mutator exists. In-process only (§8) | — |
| I7 | Structural — `post_reversal` returns a new `Transfer`; nothing mutates an existing one | — |
| G5 | `Account.assert_owned_by` — the fact; *which legs to check* is use-case policy | `AccountOwnershipError` |

---

## 7. Error taxonomy

All derive from `shared.domain.DomainError` and carry the `Error` suffix (ruff `N818`). Defined in
`account_balance/domain/errors.py`; `CurrencyMismatchError` is imported from `shared`, never
redefined. Class is a domain fact — whose fault is it — not an HTTP concern.

| Error | Class | Raised when |
| --- | --- | --- |
| `InsufficientFundsError` | Client | I2 — a debit would take a `FORBIDDEN`-policy account below zero |
| `NonPositiveAmountError` | Client | I3 — a transfer or entry amount is ≤ 0 |
| `SelfTransferError` | Client | I5 — source and destination are the same account |
| `AccountNotOperableError` | Client | status (or, for `close()`, account type) forbids the operation |
| `AccountNotEmptyError` † | Client | `close()` on a non-zero balance (PRD §7.2) |
| `AccountOwnershipError` | Client | G5 — the requester does not own the account |
| `AccountPurposeMismatchError` † | Client | `purpose.account_type is not account_type` (PRD §4.4) |
| `InvalidIdempotencyKeyError` † | Client | empty, blank, or over 255 characters |
| `UnbalancedTransferError` | Internal | I1 violated at construction — **if this fires in production it is our bug** (PRD §11.1) |
| `EntryAccountMismatchError` | Internal | an entry for another account reached `credit`/`debit` |
| `EntryDirectionMismatchError` † | Internal | a `CREDIT` leg reached `debit`, or the reverse |
| `MalformedTransferError` † | Internal | fewer than two legs, a leg belonging to another transfer, or source/destination not represented in the legs |
| `NaiveTimestampError` † | Internal | a naive `datetime` reached the domain |
| `ReversalMismatchError` † | Internal | the accounts passed to `post_reversal` are not the original's legs |

† not in the proposal's §7 table — added here because the design introduced the guard. Every one is a
guard the proposal implies but does not name.

---

## 8. What the domain cannot enforce

Named only as constraints the domain imposes on out-of-scope work.

| Rule | Why no aggregate can hold it | Carried to |
| --- | --- | --- |
| A transfer is reversed at most once | A question about *other* `Transfer` rows; check-then-act loses the race | Partial unique index on `transfers.reverses` |
| One account per `(owner, purpose, currency)` | Same shape; two simultaneous opens both read "none exists" | Unique index — this is what lets account opening drop its idempotency key (PRD §6.3) |
| I6 append-only | Frozen dataclasses stop *our* process, not an `UPDATE` | No UPDATE/DELETE path on `entries` |
| G2 no corruption under concurrency | The domain is single-threaded and holds no lock | `SELECT ... FOR UPDATE` ordered by `AccountId` (which is why `AccountId` is `order=True`) |
| `SYSTEM` balance is not materialized (PRD §5.3) | `Account` carries a `balance` field for every type | Persistence must not add a maintained balance column for `SYSTEM` accounts; it reconstitutes them from `SUM(entries)` |

---

## 9. Testing strategy

Strict TDD: every guard below is a failing test before it is a line of code. Work units follow the
module order of §1, each ending green.

### 9.1 Property-based — `hypothesis` required, and it is **not** currently a dev dependency

`pyproject.toml`'s dev group has pytest, pytest-cov, pytest-asyncio, httpx, testcontainers, ruff,
mypy, pre-commit. There is no property-testing library. PRD §10.1 and the proposal's §12 mitigation
both assume one. **Adding `hypothesis` to `[dependency-groups].dev` is a prerequisite of the first
task.** Without it these become table-driven example tests, which is a real loss of coverage.

| # | Property | Why property-based |
| --- | --- | --- |
| P1 | For any two accounts and any amount the source can cover, `post_transfer` leaves `source.balance + destination.balance` unchanged | Conservation is the D2 sign convention; a flipped sign fails immediately |
| P2 | For any generated set of legs, `Transfer` construction succeeds **iff** every currency nets to zero — including synthetic four-leg two-currency sets | Tests I1 in the general form now, before FX exists (proposal §12) |
| P3 | For any sequence of `post_transfer` calls over a pool of accounts, every `USER` balance stays ≥ 0 and the sum over all accounts is zero | I2 totality across paths, not one call |
| P4 | After any generated sequence, each account's balance equals the sum of `signed_amount` over every entry naming it | The domain-level analogue of PRD §10.2 reconciliation, with zero infrastructure |

### 9.2 Example-based

One test per row of §6 and §7 — each must fail when its guard is deleted (proposal §14). Plus the
cases where a specific scenario is the specification:

- **PRD §7.3, verbatim**: Ana transfers 100 to Bruno, Bruno spends 80, an operator reverses. Assert
  Bruno's balance is `-80`, Ana is whole, four entries exist across two transfers, and the reversal
  carries `reverses`. This is the golden test for the amended D1.
- **`debit` still refuses**: the same shortfall through `post_transfer` raises `InsufficientFundsError`.
  The two tests together *are* I2.
- **Atomicity**: `post_transfer` into a `CLOSED` destination raises and leaves `source.balance`
  **unchanged** — the §4.2 fix, and it fails if anyone reintroduces mutate-then-validate.
- **Reconstitution**: `reconstitute` accepts a negative `USER` balance and a `CLOSED` status;
  `open` always produces zero, `ACTIVE`, version `0`, for `SYSTEM` accounts too.
- **Identity**: `AccountId(u) != TransferId(u)`; `AccountId` sorts; two `Account` objects with the same
  id and different balances are equal.

### 9.3 Architecture tests (AST over `src/`, no infrastructure)

1. `debit_for_reversal` is referenced only in `account.py` (definition) and in `post_reversal`.
2. `PendingApplication.commit` is called only in `posting.py`.
3. No module under `account_balance/domain/` imports `application`, `adapters`, or any sibling module
   above it in the §1 table.
4. No `datetime.now`, `datetime.utcnow`, `uuid`, `uuid6` or `IdGenerator` reference exists under
   `domain/` (D6).

### 9.4 Concurrency — what these tests must *demonstrate*

**No domain test can demonstrate G2, and this design does not pretend otherwise.** The domain is
single-threaded, holds no lock, and reads no shared state. A unit test that spawns threads against one
`Account` object would demonstrate the GIL's behaviour, not the service's.

What the domain owes the future integration test is a *deterministic* failure mode: `debit` computes
against `self._balance` at call time and refuses — it never clamps, never partially applies, never
silently succeeds. That is unit-testable and is the precondition the lock makes true globally.

The real demonstration belongs to the persistence phase (out of scope) and must satisfy PRD §10.3 by
**running N concurrent transfers through real connections against real PostgreSQL** — asserting the
final balance equals the serialized expectation, that no `USER` balance went negative, and, critically,
**that the test fails when `FOR UPDATE` is removed**. A concurrency test that passes without the lock
demonstrates nothing.

---

## 10. Where this design departs from the proposal

Stated plainly, because each is a place the proposal as written cannot be implemented verbatim.

| Proposal text | Problem | Resolution |
| --- | --- | --- |
| D1 amended: `def debit(self, amount: Money) -> None` | Takes `Money`, not an `Entry`, and mutates directly — contradicts D5 (a balance cannot move without an entry) and reintroduces the partial-mutation window | Entry-taking, returning `PendingApplication` (§4.2). Method names from PRD §7.3 are preserved exactly, because the grep audit depends on them |
| D10 body: "the reversal is refused with `InsufficientFundsError`" | Reversed by PRD §7.3 and the proposal's own §8b. Stale text | Reversal posts in full via `debit_for_reversal`; the recipient goes negative |
| §9 Q3 row: "absorb the shortfall into a `SYSTEM` receivable" | Same reversal; the row was not updated when §8b landed | §8b is authoritative. **The proposal should be corrected** so a future reader is not led by a resolved-looking table |
| D2: "`Account.apply(entry)` dispatches on direction" | Dispatching on direction to pick a sign creates a *second* place a sign is chosen, defeating D2's own purpose | `Account` adds `entry.signed_amount`; direction selects the *rule* (`credit`/`debit`), never the sign |
| D5: "an entry without a balance change cannot be produced by any code path" | Literally false — a repository reconstituting a `Transfer` builds entries with no balance change, correctly | The precise claim: no path that *posts* can produce one. Reconstitution rebuilds an already-posted transfer, and `__post_init__` still guarantees it is balanced |
| D1: `assert_allows(self, resulting_balance: Money) -> None` | The raised error cannot say which account | `assert_allows(resulting_balance, *, account_id)` |
| §12: "property test the netting rule" | `hypothesis` is not installed | Add it to the dev group as task one (§9.1) |

## 11. Open questions

- [ ] `hypothesis` as a dev dependency — a one-line addition, but it is a dependency decision and
      belongs to the user, not to this design.
- [ ] `IdempotencyKey`'s 255-character ceiling is chosen to match a plausible future column. If the
      persistence phase picks a different width, this constant moves with it.
- [ ] Whether `Account` should expose `purpose`/`account_type`/`owner_id` as public properties. Assumed
      yes — PRD §4.2 says the API exposes them and D1 calls them descriptive data — but nothing in the
      domain reads them from outside except `assert_owned_by`.
