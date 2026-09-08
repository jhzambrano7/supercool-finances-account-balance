# Coding Conventions

Project-wide rules that apply to every module, not just the one where they were first established.
Unlike `docs/prd.md` (the product contract) or a change's `spec.md` (its domain requirements), this
file governs *how code is written*, independent of which feature it implements.

## Tell, Don't Ask

**Rule:** if you find yourself reading a value out of an object to decide something about it, ask
whether the object should answer that question itself instead.

The classic violation:

```python
if self.status is not AccountStatus.ACTIVE:
    raise AccountNotOperableError(...)
```

The caller pulls `status` out of the object, and has to know — externally — which member counts as
"operable". The fix is not to add a getter; it is to move the *meaning* onto the type that owns it:

```python
class AccountStatus(Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

    def is_active(self) -> bool:
        return self is AccountStatus.ACTIVE
```

```python
if not self.is_active():
    raise AccountNotOperableError(...)
```

Nothing about the raised error changes. What changes is *where the definition of "active" lives*. If
a `SUSPENDED` status is ever added, `is_active()` is the one place that has to change — every caller
that asks `account.is_active()` keeps working without knowing a new member exists.

### Why this matters more here than in most codebases

This is a ledger. The classifications in it — `AccountType`, `AccountStatus`, `AccountPurpose` — are
exactly the kind of thing PRD revisions have already changed twice this project (§7.3's reversal rule
changed what "operable" implicitly meant; §4.4 added `AccountPurpose` as a second axis). A rule
spelled out at every call site has to be found and fixed at every call site when it changes. A rule
owned by the type is fixed once.

### Applied so far

| Type | Method | Replaces |
| --- | --- | --- |
| `AccountStatus` | `is_active()`, `is_closed()` | `status is AccountStatus.ACTIVE` |
| `AccountType` | `is_user()`, `is_system()` | `account_type is AccountType.SYSTEM` |
| `AccountPurpose` | `matches_type(account_type)` | `purpose.account_type is not account_type` |
| `UserAccount` | `is_active()`, `is_closable()` | reading `.status`/`.account_type` and combining externally |

**Update:** `OverdraftPolicy` — an enum `UserAccount`/`SystemAccount` used to ask "what do you permit"
before branching on the answer — is gone. The `Account = UserAccount | SystemAccount` split
(`docs/decision-log.md`, the ADT-split entries) made the question itself unaskable of a `SystemAccount`:
it has no `debit()` to guard, because it has no balance to protect (T7). `UserAccount.debit()` now
states its own rule inline (`if resulting.is_negative: raise InsufficientFundsError(...)`) rather than
asking a shared policy object — the sharper form of *tell, don't ask* here was not "ask a smarter
object", it was "make the question type-level, so only the type that has an answer can be asked".

### Where this rule does not apply

Not every comparison is a violation. Applying this everywhere turns simple code into a maze of
one-line delegation methods with no encapsulation benefit — that is its own failure mode.

The rule buys value when the object owns the **interpretation** of a classification — deciding what
"active" or "closable" *means*. It buys nothing when two values of the *same* type are compared for
plain equality with no interpretation involved: `entry.account_id != self.account_id`,
`entry.direction is not required_direction`. Wrapping those in `entry.matches_account(self)` or
`entry.matches_direction(required)` would not centralize any decision — the comparison already means
exactly what it says, and the wrapper would only add a name to look up.

**Deliberately deferred, not silently skipped:** `Account._validated_balance` still compares
`entry.account_id`/`entry.direction` directly rather than asking `Entry` to assert them. Moving that
assertion onto `Entry` (so `Entry` raises `EntryAccountMismatchError`/`EntryDirectionMismatchError`
itself) is a reasonable next step in the same spirit, deferred only because `Entry` was mid-build in a
concurrent unit when this rule was written. Revisit before treating `Entry`'s guard shape as settled.

## Repository & Adapter Conventions

Established while building the account-opening slice's SQL adapter (`src/modules/account_balance/adapters/outbound/repositories/sql/`), then verified by fixing every place the codebase still violated it. Applies to every repository/adapter this project adds, not only that one.

### A repository is a persistent collection, not a catalog of one method per query

**Rule:** a repository's public surface is collection-like — `find`, `get`, `add`, `update` — never a dedicated method per way of looking something up.

```python
# Before — invites one method per query shape
async def find_by_natural_key(self, *, owner_id, purpose, currency) -> Account | None: ...
async def find_by_email(self, *, email) -> Account | None: ...  # next one, and the one after
```

```python
# After — one lookup method, extensible without a new port signature
async def find(self, *, criteria: FindAccountCriteria) -> Account | None: ...
```

`get(criteria)` (find-or-raise `AccountNotFoundError`) comes for free from `find` on the abstract
base — a caller picks the failure mode it wants (`None` vs. an exception) instead of every call site
re-deriving "not found" from a `None` check. A new way to look an account up is a new `Criteria`
subtype, not a new abstract method every adapter implementing the port has to add.

### Filters are an extensible Criteria, translated to the query engine at the adapter boundary

**Rule:** what to filter by is a plain value object in `application/gateways/models/`; how to turn it
into an actual query is a pure function living in the adapter (`adapters/.../queries/`), never inside
the port or the use case.

```python
FindAccountCriteria = FindAccountByOwnerAndPurposeAndCurrency | FindAccountByAccountId
```

```python
def find_account_criteria_to_sql_query(criteria: FindAccountCriteria) -> Select[tuple[AccountDbo]]:
    match criteria:
        case FindAccountByOwnerAndPurposeAndCurrency(owner_id, purpose, currency):
            return select(AccountDbo).where(...)
        case FindAccountByAccountId(account_id):
            return select(AccountDbo).where(...)
```

This keeps the application layer free of SQL (Ports & Adapters) while still letting a use case
express an arbitrary lookup — the criteria is domain-shaped, the translation is adapter-shaped, and
neither leaks into the other.

### DBOs and DTOs: the naming for boundary shapes, and where their mapping logic lives

**Rule:** applies at both boundaries the domain never sees across. Outbound (persistence): a
SQLAlchemy-mapped class is named `<Thing>Dbo`, lives in `adapters/.../dbos/`, and owns its own
translation to and from the domain type as `from_domain`/`as_domain`. Inbound (HTTP): a pydantic
request/response model is named `<Thing>Dto`, lives in `adapters/inbound/api/`, and owns its own
translation *from* the application layer as a `from_result`-shaped classmethod (never the domain
directly — a DTO maps from a use case's result, the same boundary a route already respects). Neither
uses free functions sitting beside the class to do this — the mapping is the class's own behavior.
Either kind's mapping logic gets its own unit test, not only indirect coverage through an integration
test against a real database or a real HTTP call (`tests/unit/.../test_dbos.py`,
`tests/unit/.../test_dtos.py` — covering, at minimum, a full round trip; a DBO additionally covers
reconstituting a row the domain would refuse to *construct* fresh, e.g. a negative balance, to confirm
the mapping uses `reconstitute()` and never re-asserts an invariant the domain only enforces on the
write path).

### An adapter's file name carries the same technology prefix as its class

**Rule:** `SqlAccountRepository` lives in `sql_account_repository.py`, not `account_repository.py`
— the file name is the class name, lowercased with underscores, prefix included. The prefix is what
lets a reader (or a directory listing) tell adapters for the same port apart before opening any of
them; dropping it from the file while keeping it on the class means the two names disagree about
what the file is. Applies uniformly across a directory once one adapter in it has set the pattern —
a second, third or fourth adapter added later must match the first, not restart the naming from
scratch.

### Log every exception the adapter catches; wrap only the ones you didn't expect

**Rule:** `logger.exception(...)` fires unconditionally for every exception an adapter method catches
— including a *recognized* one (a unique-constraint violation the use case already knows how to
recover from), not only the unrecognized ones. The log is a complete audit trail of every integrity
violation this adapter sees, not a filtered stream of only what surprised it. What differs by
recognition is not whether it is logged, but what gets **raised** afterward: a recognized failure
still raises its own typed error, unchanged, for the use case to handle; only a failure the adapter
does **not** recognize gets wrapped in an `<Thing>RepositoryError` (an `IntegrationError`) before
crossing the port, so the application and domain layers never depend on which library sits behind
the adapter.

```python
except IntegrityError as exc:
    self._logger.exception(...)                    # logged either way — the audit trail
    if _violates_natural_key_constraint(exc):
        raise AccountAlreadyExistsError(...) from exc  # recognized — its own typed error
    raise AccountRepositoryError(operation="add", cause=exc, metadata=...) from exc  # not — wrapped
```

### Log before you raise — everywhere except the domain

**Rule:** a raise in an adapter or a use case is preceded by a log line carrying the identifiers the
raise itself cannot: which caller, which key, which account. Pick the level for the reader, not the
raise — `logger.exception` where an exception is in hand, `warning` for a client's own mistake
(a reused idempotency key with a different payload), `error` for a should-never-happen the service
must be told about (an idempotency record naming a transfer that does not exist).

**The domain layer is the exception, deliberately.** Domain types raise to *state a rule*, not to
report an incident: `InsufficientFundsError` is the answer to a question the caller asked, and it is
the caller — the use case — that knows whether that answer is routine (a client mistake, refused and
returned as a `422`) or alarming. Giving the domain a logger would also hand it an infrastructure
dependency it otherwise does not have, for no gain: the layer that catches is the layer that knows
what the raise *means*.

### Where each error type lives is decided by its base class, not by which file raises it

**Rule:** `DomainError` (and its subclasses) belong under a module's own `domain/errors.py` or
`shared/domain/errors.py` — they express a business invariant. `ApplicationError`/
`ResourceNotFoundError`/`IntegrationError` belong under `shared/application/errors.py` (or a module's
own `application/` package) — they express a fact about the *operation* or the *port*, not a rule the
domain enforces. A domain-package file must never import from `application/` to build one of its own
errors; if an error's natural base class lives in `application`, the error itself belongs in
`application`, wherever it is actually raised (e.g. `AccountNotFoundError` lives beside
`AccountRepository` in `application/gateways/`, not in `domain/errors.py`, even though it is *about*
an `Account`). Getting this backwards was a real bug found while applying this convention: it forced
the domain layer to import a criteria type from application, inverting the dependency direction
Ports & Adapters exists to keep one-way.

### An error's fields are attributes, not only words inside its message string

**Rule:** a custom error that a caller might reasonably need to inspect (a route's error handler, a
test, a retry policy) stores its identifying fields as real attributes in `__init__`, then builds the
human-readable message from those same fields — never the other way around (a pre-formatted string
handed in at the raise site, with nothing else on the instance). A bare `class Foo(Exception): pass`
raised as `raise Foo(f"...")` gives a caller only `str(exc)` to work with, which means parsing text to
recover a fact the raiser already had as a typed value.

```python
# Before — the caller gets a string, and has to parse it back into a fact
class AccountAlreadyExistsError(Exception):
    """Raised when `add()` loses the natural-key race (AO4)."""


# raised as: raise AccountAlreadyExistsError(f"account already exists for natural key (owner={owner_id}, ...)")
```

```python
# After — the caller reads exc.owner_id directly; the message is still there for logs/`str(exc)`
class AccountAlreadyExistsError(ResourceAlreadyExistsError):
    def __init__(self, *, owner_id: OwnerId, purpose: AccountPurpose, currency: Currency) -> None:
        self.owner_id = owner_id
        self.purpose = purpose
        self.currency = currency
        super().__init__(resource_type="account", resource_identifier=f"(owner={owner_id}, ...)")
```

`ResourceNotFoundError`/`ResourceAlreadyExistsError` (`shared/application/errors.py`) are the shared
shape behind this: both store `resource_type`/`resource_identifier` themselves and build their own
message, so a module-specific error (`AccountNotFoundError`, `AccountAlreadyExistsError`) only adds
the fields specific to *that* resource, never re-implements the message-building. Not every error
needs this — an internal guard nothing outside the domain ever catches by type (e.g.
`EntryDirectionMismatchError`) gains nothing from structured fields nobody reads; reserve the extra
`__init__` for errors a real caller inspects.

### The application layer does not know HTTP exists

**Rule:** an application-layer result (a use case's return type) states facts, never a transport-level
interpretation of them — no status codes, no header names, no framework types. Deciding what a fact
*means* to a particular inbound adapter (e.g. "`created=True` means HTTP 201") is that adapter's job,
done at the boundary, not asked of the result. This is stricter than it sounds: even a plain `int`
alongside a comment disclaiming any framework dependency still encodes an HTTP-specific number inside
the application layer — the encoding is the leak, not the type it's stored as. If a second inbound
adapter (a CLI, a message consumer) ever calls the same use case, it must not have to interpret an
HTTP status code to know what happened.

```python
# Before — the use case's own result decides an HTTP-specific number
@dataclass(frozen=True, slots=True)
class OpenAccountResult:
    account: Account
    created: bool

    @property
    def http_status(self) -> int:
        return 201 if self.created else 200
```

```python
# After — the result states a fact; the inbound adapter interprets it
@dataclass(frozen=True, slots=True)
class OpenAccountResult:
    account: Account
    created: bool


# in the route:
response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
```

### No layer-signaling suffixes

**Rule:** a class name says what it does, not which layer it lives in — the module path already says
that. `UseCase`, `Service`, `Manager`, `Handler` used as a generic suffix are the class-naming
equivalent of Hungarian notation: `AccountRegister` (an application service) reads as well as
`AccountRegisterUseCase` and the suffix has to be typed, read and kept in sync with nothing in
exchange. Prefer a name built from the domain verb the type actually performs.

### Domain and application errors default to `400`, not `500`, at the HTTP boundary

**Rule:** a `DomainError` (or `ApplicationError`) reaching an inbound adapter's defensive catch-all is
a business-rule violation over the request or the system's current state — the caller's fault (or at
least the caller's to know about), not evidence the service itself is broken. Default that catch-all
to `400 Bad Request`, not `500`. A specific error the endpoint already names explicitly (e.g. a
resource-conflict error mapped to `409`) still takes priority over the catch-all; this only changes
what an *unnamed* one defaults to. **Not yet applied everywhere**: `revert`'s routes (a separate branch, not yet merged into this one)
still default to `500` for this same catch-all — reconcile once that branch merges here.

### Applied so far

| Type | Convention | Where |
| --- | --- | --- |
| `AccountRepository` | collection-like (`find`/`get`/`add`), no `find_by_x` methods | `application/gateways/account_repository.py` |
| `FindAccountCriteria` | extensible Criteria, one dataclass per lookup shape | `application/gateways/models/find_accounts_criteria.py` |
| `AccountDbo` | DBO naming, `from_domain`/`as_domain` colocated + unit-tested | `adapters/outbound/repositories/sql/dbos/account_dbo.py` |
| `AccountDbo` / `TransferDbo` / `EntryDbo` / `IdempotencyRecordDbo` | one module per DBO, file name = class name snake_cased; no barrel re-export in `dbos/__init__.py`, importers name the module they need | `adapters/outbound/repositories/sql/dbos/` |
| `TransferRepositoryError` / `IdempotencyRepositoryError` | `IntegrationError` wrapper, logged unconditionally, wrapped only when unrecognized | `application/gateways/transfer_repository.py`, `application/gateways/idempotency_repository.py` |
| `AccountRepositoryError` | `IntegrationError` wrapper, logged unconditionally, wrapped only when unrecognized | `adapters/outbound/repositories/sql/sql_account_repository.py` |
| `SqlTransferRepository` / `SqlIdempotencyRepository` / `SqlTransferUnitOfWork` | file name carries the same `sql_` prefix as the class | `adapters/outbound/repositories/sql/sql_transfer_repository.py`, `sql_idempotency_repository.py`, `sql_unit_of_work.py` |
| `AccountNotFoundError` / `AccountAlreadyExistsError` | structured body (see below), not a bare message string | `application/gateways/account_repository.py` |
| `Base` (SQLAlchemy declarative) | one shared base, not one per module | `shared/adapters/outbound/repositories/sql/base.py` |
| `AccountResponseDto` / `OpenAccountRequestDto` | DTO naming, `from_result` colocated + unit-tested | `adapters/inbound/api/dtos.py` |
| `TransferRequestDto` / `TransferResponseDto` / `EntryResponseDto` | DTO naming, mapping colocated + unit-tested (`from_transfer` maps from the domain `Transfer`, not an application result — see the gap noted below) | `adapters/inbound/api/dtos.py` |
| `OpenAccountResult` | no `http_status`, no HTTP concept at all — the route decides | `application/use_cases/account_register.py` |
| `AccountRegister` | no `UseCase` suffix | `application/use_cases/account_register.py` |
| `TransferMoney` | no `UseCase` suffix | `application/use_cases/transfer_money.py` |

**Known gap, not yet reconciled:** `revert_transfer.py` (the `revert` slice, a separate branch not yet
merged into this one) defines its own `AccountNotFoundError` — a plain `Exception` with a
message-string constructor — predating this convention. Reconcile with the one described here (an
`ApplicationError`, criteria-based) once that branch merges here; don't let two names for the same
fact drift further apart in the meantime.

Also: `TransferResponseDto.from_transfer` maps from the domain `Transfer` directly, unlike
`AccountResponseDto.from_result`, which maps from an application-layer result. Close this gap if
`TransferMoney.execute` ever gains a dedicated result type — not something to fix now.

## When you touch a file and see a violation of a rule added later

Fix it in that file if the cost is proportional to the change you are already making, and note the
fix in the commit body. Do not go hunting for every instance across the codebase as a side effect of
an unrelated task — that turns a small change into an unreviewable one. A rule stated here is binding
on new code immediately, and on old code the next time that code is touched.
