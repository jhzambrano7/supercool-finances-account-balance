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

## When you touch a file and see a violation of a rule added later

Fix it in that file if the cost is proportional to the change you are already making, and note the
fix in the commit body. Do not go hunting for every instance across the codebase as a side effect of
an unrelated task — that turns a small change into an unreviewable one. A rule stated here is binding
on new code immediately, and on old code the next time that code is touched.
