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
| `Account` | `is_active()`, `is_closable()` | reading `.status`/`.account_type` and combining externally |

`OverdraftPolicy.assert_allows(...)` (already in the codebase before this rule was named) is the
sharper form of the same idea: instead of `Account` asking `overdraft_policy` what it permits and
branching itself, `Account` tells the policy "assert you allow this" and the policy raises. Prefer
that shape — *tell an object to enforce a rule and let it raise* — over a boolean the caller then
branches on, whenever the object can name and raise the specific failure itself.

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

### DBOs: the naming for persisted rows, and where their mapping logic lives

**Rule:** a SQLAlchemy-mapped class is named `<Thing>Dbo`, lives in `adapters/.../dbos/`, and owns its
own translation to and from the domain type as `from_domain`/`as_domain` — not as free functions
sitting beside the class in the repository module. Its mapping logic gets its own unit test, not only
indirect coverage through an integration test against a real database (`tests/unit/.../test_dbos.py`
— covering, at minimum, a round trip and reconstituting a row the domain would refuse to *construct*
fresh, e.g. a negative balance, to confirm the mapping uses `reconstitute()` and never re-asserts an
invariant the domain only enforces on the write path).

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
        raise AccountNaturalKeyConflictError(...) from exc  # recognized — its own typed error
    raise AccountRepositoryError(operation="add", cause=exc, metadata=...) from exc  # not — wrapped
```

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

### Applied so far

| Type | Convention | Where |
| --- | --- | --- |
| `AccountRepository` | collection-like (`find`/`get`/`add`), no `find_by_x` methods | `application/gateways/account_repository.py` |
| `FindAccountCriteria` | extensible Criteria, one dataclass per lookup shape | `application/gateways/models/find_accounts_criteria.py` |
| `AccountDbo` | DBO naming, `from_domain`/`as_domain` colocated + unit-tested | `adapters/outbound/repositories/sql/dbos/models.py` |
| `AccountRepositoryError` | `IntegrationError` wrapper, logged only when unrecognized | `adapters/outbound/repositories/sql/sql_account_repository.py` |

**Known gap, not yet reconciled:** `application/use_cases/transfer_money.py` and `revert_transfer.py`
(the `transfer` and `revert` slices, later PRs in this repo's history) each define their own
`AccountNotFoundError` — a plain `Exception` with a message-string constructor — predating this
convention. Reconcile with the one described here (an `ApplicationError`, criteria-based) the next
time either of those modules is touched; don't let two names for the same fact drift further apart in
the meantime.

## When you touch a file and see a violation of a rule added later

Fix it in that file if the cost is proportional to the change you are already making, and note the
fix in the commit body. Do not go hunting for every instance across the codebase as a side effect of
an unrelated task — that turns a small change into an unreviewable one. A rule stated here is binding
on new code immediately, and on old code the next time that code is touched.
