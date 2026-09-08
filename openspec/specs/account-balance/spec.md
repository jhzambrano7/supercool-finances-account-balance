# Account Balance Domain — Specification

## Purpose

This is the reference for what the **domain layer** of the account balance service must be true —
`Account`, `Transfer`, `Entry`, their value objects, and the invariants that bind them. It answers
"what exactly must this domain do?", not "how is it built" and not "how is it called". Use cases,
HTTP, persistence, locking and idempotency storage are out of scope; they are referenced only where
a domain rule constrains what they may do.

Source of truth: `docs/prd.md` (goals G1–G5, invariants I1–I7). Design rationale:
`openspec/changes/account-balance-domain/proposal.md` (decisions D1–D12, §8b for the 2026-09-06
revision that this spec already reflects as current truth).

**Implemented today:** `Money`, `Currency` (`src/modules/shared/domain/money.py`) and the base
domain errors `DomainError`, `InvalidCurrencyError`, `InvalidAmountError`, `CurrencyMismatchError`
(`src/modules/shared/domain/errors.py`). Everything else in this document — `Account`, `Transfer`,
`Entry`, and every error and requirement below that is not one of those four names — was **specified,
not built** when this line was written; the `account-opening` and `transfer` slices have since built
most of it (see their own specs).

**Specified, not yet built:** the closed `Currency` enum below. `Currency` ships today as a
shape-only ISO 4217 validator; narrowing it to `USD` — and seeding the matching
`FUNDING`/`SETTLEMENT` accounts `USD` needs — is decided and pending, not done.

## Domain Types

| Type | Kind | Responsibility |
| --- | --- | --- |
| `Account` | `UserAccount \| SystemAccount` — a closed union, not one class | the two kinds do not share a shape: only a `UserAccount` has `balance`, `status` and `version`. See below |
| `UserAccount` | Aggregate root, immutable (every change returns a new instance) | identity, owner, purpose, currency, balance, status, version |
| `SystemAccount` | Aggregate root, immutable | identity, owner, purpose, currency — **no balance**, no status (always operable), no version (never persisted). See `openspec/specs/transfer/spec.md`, "A `SYSTEM` Account Has No Balance At All" |
| `Transfer` | Aggregate root, frozen | source, destination, amount, idempotency key, requester, owns its `Entry` legs |
| `Entry` | Entity inside `Transfer` | one leg: account, direction, positive amount, timestamp |
| `AccountId`, `TransferId`, `EntryId`, `OwnerId` | Value objects over `UUID` | typed identity; not interchangeable |
| `Currency` | Enum: `USD` | the closed set of currencies the platform supports — see its requirement below; widening it also requires seeding `FUNDING`/`SETTLEMENT` accounts in the new currency |
| `AccountType` | Enum: `USER`, `SYSTEM` | drives the overdraft policy |
| `AccountPurpose` | Enum: `CHECKING`, `SAVINGS`, `FUNDING`, `SETTLEMENT` | what the account is *for*; paired with `AccountType` |
| `AccountStatus` | Enum: `ACTIVE`, `CLOSED` | operability gate |
| `EntryDirection` | Enum: `DEBIT`, `CREDIT` | sign of the movement |
| `IdempotencyKey` | Value object | client-supplied retry key, carried on `Transfer`, not interpreted by the domain |

## Domain Errors (vocabulary used throughout)

| Error | Raised when |
| --- | --- |
| `InsufficientFundsError` | `Account.debit()` would take a `USER` balance below zero (I2) |
| `NonPositiveAmountError` | a transfer or entry amount is ≤ 0 (I3) |
| `SelfTransferError` | source and destination are the same account (I5) |
| `AccountNotOperableError` | the account's status forbids the requested movement |
| `AccountOwnershipError` | the requester does not own the account being asserted (G5) |
| `UnbalancedTransferError` | I1 would be violated at construction — Internal, our bug if it fires |
| `EntryAccountMismatchError` | an entry for one account is applied to another — Internal guard |
| `CurrencyMismatchError` | *(shared)* — currencies of two amounts don't match (I4) |
| `InvalidAccountPurposeError` | `purpose` does not belong to `account_type` on `Account.open()` (§4.4) |
| `AccountNotEmptyError` | `Account.close()` on a non-zero balance (§7.2) |
| `AccountNotClosableError` | `Account.close()` on an account that is not `ACTIVE`, or is `SYSTEM`-typed (§7.2) |
| `InvalidIdempotencyKeyError` | the key is empty or exceeds the accepted length |
| `EntryDirectionMismatchError` | a leg is applied through the wrong method — Internal guard |
| `MalformedTransferError` | a transfer's legs do not describe the transfer they belong to — Internal guard |
| `NaiveTimestampError` | `occurred_at` carries no timezone |
| `ReversalMismatchError` | a reversal's legs do not mirror the transfer it reverses — Internal guard |

Closure raises **two** errors rather than one, and the split is by what the caller can do about it.
`AccountNotEmptyError` is actionable — empty the account and try again — so it earns its own type.
The other two causes are not actionable by a customer, and nothing branches differently between
"already closed" and "is a system account", so they share a type and the message carries the reason.
Distinct error types earn their place when a caller would handle them differently.

---

## Requirements

### Identifiers

#### Requirement: Typed Identifiers Are Not Interchangeable

Each identifier type (`AccountId`, `TransferId`, `EntryId`, `OwnerId`) MUST be a distinct type. Two
identifiers wrapping the same UUID value but of different identifier types MUST NOT compare equal.

*(Supports G1 — a movement traced by id must never be confused with a different entity's id.)*

##### Scenario: Different identifier types never compare equal

- GIVEN an `AccountId` and a `TransferId` constructed from the same UUID value
- WHEN they are compared for equality
- THEN the comparison is `False`

#### Requirement: Account Identifiers Support Deterministic Ordering

`AccountId` MUST support total ordering (`<`, `<=`, `>`, `>=`) so a caller can lock accounts in a
deterministic sequence.

*(§5, step 3 — deterministic lock ordering by account id prevents deadlock; the domain supplies the
ordering, locking itself is out of scope.)*

##### Scenario: Two account ids are orderable

- GIVEN two distinct `AccountId` values
- WHEN they are compared
- THEN exactly one of `<` or `>` holds, consistently on repeated comparison

---

### Currency

#### Requirement: The Supported Currencies Are a Closed Set, Enumerated in the Domain

`Currency` MUST be an enum over exactly `USD`. It MUST NOT accept an arbitrary three-letter code
that merely satisfies the ISO 4217 *shape*: a code outside the enum MUST be rejected with
`InvalidCurrencyError`, wherever it enters (an account being opened, an amount being posted), not
deferred to a later failure.

**Scoped to one member for now, deliberately.** `MXN` and `COP` were the currencies originally
discussed alongside `USD` when this requirement was written, and remain the worked examples below of
what widening the set looks like — but shipping only `USD` first is simpler, and the two-step
mechanism this requirement exists to establish (below) doesn't need more than one member to prove
itself. Adding `MXN`, `COP`, or any other currency later is exactly the "widen the enum, seed the
accounts" act this requirement already describes; nothing about that mechanism changes because the
starting set has one member instead of three.

*(Supersedes the original shape-only validation. That rule accepted `EUR`, `GBP` — and `ZZZ` — as
equally valid, which let `POST /accounts` mint an account in a currency the platform holds no
`FUNDING`/`SETTLEMENT` account for: opening it returned `201`, and every deposit into it afterwards
returned `422 CurrencyMismatchError`, permanently. Reproduced against the running service before this
decision was taken. The PRD carries the same gap in prose — §4.4 offers "checking in EUR" as an
example while stating the platform "holds exactly one USD funding account".)*

**Adding a currency is deliberately two coordinated steps, never one.** A new member in this enum is
necessary but not sufficient: the platform MUST also hold a `FUNDING` and a `SETTLEMENT` account
denominated in that currency before any account can be opened in it, because a deposit is
`FUNDING → USER` and a withdrawal is `USER → SETTLEMENT`, and I4 requires both legs to agree on
currency. Coupling the two is the point — it makes "supported currency" an operational fact (the
platform can actually fund and settle it) rather than a validation string, and makes the omission
that caused this decision impossible to repeat: you cannot widen the enum without being confronted
with the accounts the new member requires.

**This is not the multi-currency deferral of PRD §3.** That defers *cross-currency* movement (FX,
conversion, a rate gateway) and remains deferred; I4 still rejects a transfer whose legs disagree.
This requirement is about *coexistence without conversion* — a `COP` account funded from a `COP`
funding account, never crossing into another currency — which the model already committed to on the
customer side, since `(owner, purpose, currency)` is the natural key and currency is therefore part
of an account's identity (§4.4). Only the platform side had not followed.

**Considered and deferred: keep `Currency` open, and register `SYSTEM` accounts through an admin
API.** The enum's whole job is to force a deliberate act before a currency becomes usable. An admin
endpoint that registers a currency's `FUNDING`/`SETTLEMENT` accounts would enforce the same thing
more directly — and better: the *existence of those accounts* becomes the single source of truth for
"supported", collapsing today's two coordinated steps into one and removing the enum as a second
place the same fact is written. Adding a currency would become an operation, not a deploy. It is
deferred, not rejected, for one reason that has nothing to do with currency: this service has no
administrative surface at all. PRD §9.1's entire authentication mechanism is a simulated
`X-Caller-Id` header for customers; an endpoint that mints platform-owned `SYSTEM` accounts needs a
real privilege boundary, which is a larger and separate piece of work than the gap this requirement
closes. Until that surface exists, the enum is the cheap version of the same invariant. When it does,
the enum should dissolve into the query it was standing in for — "the currencies the platform holds
`FUNDING` and `SETTLEMENT` accounts in" — rather than being maintained alongside it.

##### Scenario: A code outside the enum is rejected

- GIVEN the code `EUR`, a well-formed ISO 4217 alphabetic code outside the supported set
- WHEN a `Currency` is constructed from it
- THEN `InvalidCurrencyError` is raised

##### Scenario: Each supported currency is independently usable end to end

- GIVEN the platform holds `FUNDING` and `SETTLEMENT` accounts in `USD` (the one member today)
- WHEN an account is opened in `USD` and a deposit is made into it
- THEN both succeed, and no leg of the resulting `Transfer` is denominated in any other currency
- (The same holds for any future member the moment its `FUNDING`/`SETTLEMENT` accounts exist —
  nothing about this scenario is `USD`-specific, `USD` is simply the only member to check it against
  today.)

---

### Account

#### Requirement: Account Type and Purpose Form a Validated Pair

`Account.open()` MUST accept only `(account_type, purpose)` combinations where `purpose` belongs to
`account_type`: `USER` → `CHECKING` or `SAVINGS`; `SYSTEM` → `FUNDING` or `SETTLEMENT`. An invalid
pair MUST be rejected at construction.

*(§4.4 — type and purpose are separate axes; the pair is validated on opening.)*

##### Scenario: Valid pair opens

- GIVEN `account_type=USER`, `purpose=CHECKING`
- WHEN `Account.open()` is called
- THEN an `Account` is returned with balance zero and status `ACTIVE`

##### Scenario: Invalid pair is rejected

- GIVEN `account_type=USER`, `purpose=FUNDING`
- WHEN `Account.open()` is called
- THEN `InvalidAccountPurposeError` is raised

#### Requirement: Every New Account Opens at Zero Balance

`Account.open()` MUST always produce a `Money.zero(currency)` balance, for both `USER` and `SYSTEM`
accounts. Loading a previously-persisted, non-zero account MUST use a separately named path
(`reconstitute()`), never `open()`.

*(G1 — money only ever arrives through an entry; an account cannot be born holding money.)*

##### Scenario: Opening never accepts a starting balance

- GIVEN valid `owner_id`, `account_type`, `purpose`, `currency`
- WHEN `Account.open()` is called
- THEN the resulting balance is `Money.zero(currency)`, regardless of any other input

##### Scenario: Reconstitution restores a non-zero account without going through `open()`

- GIVEN a previously posted balance of `500 USD` and status `ACTIVE`
- WHEN `Account.reconstitute(balance=Money(500, USD), status=ACTIVE, ...)` is called
- THEN an `Account` is returned holding exactly that balance and status

#### Requirement: Debit Refuses to Drive a USER Account Below Zero

`Account.debit()` MUST compute the resulting balance and consult the account's overdraft policy
before applying it. For a `USER` account this MUST raise `InsufficientFundsError` when the result
would be negative. For a `SYSTEM` account, `debit()` MUST allow the result to go negative.

*(I2, G4 — no customer-initiated operation may drive a `USER` account below zero; `SYSTEM` accounts
are unconstrained by design, §4.1.)*

##### Scenario: USER debit refused when it would go negative

- GIVEN a `USER` account with balance `50 USD`
- WHEN `debit(Money(60, USD))` is called
- THEN `InsufficientFundsError` is raised and the balance remains `50 USD`

##### Scenario: USER debit succeeds down to exactly zero

- GIVEN a `USER` account with balance `50 USD`
- WHEN `debit(Money(50, USD))` is called
- THEN the balance becomes `0 USD` and no error is raised

##### Scenario: SYSTEM debit has no floor

- GIVEN a `SYSTEM` (`FUNDING`) account with balance `0 USD`
- WHEN `debit(Money(1000, USD))` is called (funding a customer deposit)
- THEN the balance becomes `-1000 USD` and no error is raised

#### Requirement: Reversal Debit Is the Sole Path That May Cross Zero

`Account.debit_for_reversal()` MUST be the only method through which a `USER` account balance may
be driven below zero. It MUST NOT consult the overdraft policy that `debit()` consults. `debit()`
itself MUST NOT be reachable with a flag or parameter that bypasses the policy.

*(I2 restated as "which path may cross zero, not whether the rule holds", G4, §7.3.)*

##### Scenario: `debit_for_reversal` succeeds where `debit` would refuse

- GIVEN a `USER` account with balance `20 USD`
- WHEN `debit_for_reversal(Money(100, USD))` is called
- THEN the balance becomes `-80 USD` and no error is raised

##### Scenario: The ordinary path still refuses the same amount

- GIVEN the same `USER` account with balance `20 USD`
- WHEN `debit(Money(100, USD))` is called instead
- THEN `InsufficientFundsError` is raised

#### Requirement: Credit Always Increases the Balance

`Account.credit()` MUST increase the balance by the credited amount, for `USER` and `SYSTEM`
accounts alike, with no floor or ceiling check.

*(§4.1, §4.2 — uniform, holder's-perspective signing; a `SYSTEM` account's balance rising is exactly
as unconstrained as it falling.)*

##### Scenario: Credit increases any account's balance

- GIVEN an account (either type) with balance `X`
- WHEN `credit(Money(n, currency))` is called with `n > 0` and matching currency
- THEN the balance becomes `X + n`

#### Requirement: An Inoperable Account Rejects Debits and Credits

An account whose status is not `ACTIVE` MUST refuse any `debit()`, `debit_for_reversal()`, or
`credit()` call.

*(§7.2 — "a closed account can still be read ... but cannot be debited or credited".)*

##### Scenario: A closed account refuses a debit

- GIVEN a `USER` account with status `CLOSED`
- WHEN `debit(Money(1, USD))` is called
- THEN `AccountNotOperableError` is raised and the balance is unchanged

##### Scenario: A closed account refuses a credit

- GIVEN a `USER` account with status `CLOSED`
- WHEN `credit(Money(1, USD))` is called
- THEN `AccountNotOperableError` is raised

#### Requirement: Closing an Account Requires Exactly Zero Balance and `USER` Type

`Account.close()` MUST refuse unless the account is `USER`-typed, `ACTIVE`, and its balance is
exactly zero. `SYSTEM` accounts MUST NOT be closeable.

*(§7.2 — a closed account holding funds is either an unwatched liability or money quietly taken;
`SYSTEM` accounts are infrastructure and outlive any customer.)*

##### Scenario: Zero-balance USER account closes

- GIVEN a `USER` account, status `ACTIVE`, balance `0 USD`
- WHEN `close()` is called
- THEN the status becomes `CLOSED`

##### Scenario: Non-zero balance blocks closure

- GIVEN a `USER` account, status `ACTIVE`, balance `10 USD`
- WHEN `close()` is called
- THEN `AccountNotEmptyError` is raised and status remains `ACTIVE`

##### Scenario: SYSTEM accounts cannot be closed

- GIVEN a `SYSTEM` account with balance `0 USD`
- WHEN `close()` is called
- THEN the closure is rejected

#### Requirement: Ownership Assertion Is a Domain Fact

`Account.assert_owned_by(owner_id)` MUST raise `AccountOwnershipError` when the given `owner_id`
does not match the account's owner, and MUST pass silently otherwise. The domain supplies only this
fact; deciding **which** legs of a movement must pass this check (per §9.1's rule stated over the
debited leg) is a use-case policy, not a domain rule.

*(G5, §9.1.)*

##### Scenario: Owner mismatch is rejected

- GIVEN a `USER` account owned by `owner_id=A`
- WHEN `assert_owned_by(B)` is called with `B != A`
- THEN `AccountOwnershipError` is raised

##### Scenario: Owner match passes silently

- GIVEN the same account owned by `A`
- WHEN `assert_owned_by(A)` is called
- THEN no error is raised

---

### Entry

#### Requirement: An Entry Is a Single, Directional, Strictly Positive Movement

`Entry` MUST carry a strictly positive `Money` amount and an explicit `EntryDirection`
(`DEBIT`/`CREDIT`), never a signed amount. `Entry.signed_amount` MUST derive `+amount` for `CREDIT`
and `-amount` for `DEBIT` — this MUST be the only place a sign is chosen.

*(I3; §4.2 — direction is a first-class term for reading the ledger; a positive-only amount makes
a zero or negative entry unrepresentable.)*

##### Scenario: Zero-amount entry is rejected

- GIVEN `amount=Money(0, USD)`, `direction=DEBIT`
- WHEN an `Entry` is constructed
- THEN `NonPositiveAmountError` is raised

##### Scenario: Signed amount derives from direction

- GIVEN an `Entry` with `amount=Money(100, USD)`, `direction=DEBIT`
- WHEN `signed_amount` is read
- THEN it equals `Money(-100, USD)`

#### Requirement: Entries Are Immutable and Append-Only In-Process

`Entry` MUST be an immutable structure (frozen, no mutator method). No domain operation MUST exist
that updates or removes an existing `Entry`; a correction MUST always be a new, compensating
`Transfer` (see Posting).

*(I6, I7 — append-only in this document is an in-process guarantee only; the corresponding
persistence guarantee, no UPDATE/DELETE path, is out of scope here.)*

##### Scenario: No mutator exists

- GIVEN a constructed `Entry`
- WHEN any attribute assignment is attempted
- THEN it fails (frozen dataclass semantics)

---

### Transfer

#### Requirement: A Transfer Nets to Zero, Per Currency

`Transfer.__post_init__` MUST guard that, for every currency present among its entries, the sum of
`signed_amount` in that currency is `Money.zero(currency)`. This MUST hold for any construction path,
including direct/repository-reconstituted construction, not only the `transfer` factory.

*(I1 — stated in the general, per-currency form so it survives the future multi-currency extension,
§8, unchanged.)*

##### Scenario: Two balanced legs pass

- GIVEN entries `[DEBIT 100 USD on account A, CREDIT 100 USD on account B]`
- WHEN a `Transfer` is constructed from them
- THEN construction succeeds

##### Scenario: An unbalanced pair is rejected

- GIVEN entries `[DEBIT 100 USD on account A, CREDIT 90 USD on account B]`
- WHEN a `Transfer` is constructed from them
- THEN `UnbalancedTransferError` is raised

#### Requirement: A Transfer's Amount Is Strictly Positive

`Transfer` construction MUST reject a non-positive amount.

*(I3.)*

##### Scenario: Zero amount rejected

- GIVEN a requested transfer amount of `Money(0, USD)`
- WHEN the transfer is constructed
- THEN `NonPositiveAmountError` is raised

##### Scenario: Negative amount rejected

- GIVEN a requested transfer amount of `Money(-10, USD)`
- WHEN the transfer is constructed
- THEN `NonPositiveAmountError` is raised

#### Requirement: Source and Destination Must Differ

`Transfer` construction MUST reject a transfer whose source and destination `AccountId` are equal.

*(I5.)*

##### Scenario: Self-transfer rejected

- GIVEN `source = destination = account_id X`
- WHEN the transfer is constructed
- THEN `SelfTransferError` is raised

#### Requirement: Source and Destination Currencies Must Match

Requesting a transfer between accounts of different currencies MUST be rejected.

*(I4 — v1 rejects cross-currency; a future FX path is a different, explicit domain rule, §8.)*

**Where this is checked.** `Transfer` only ever sees `entry.amount.currency`; it has no visibility
into an account's own currency, and legs in different currencies simply fail I1's per-currency
netting (each currency's total is individually nonzero). The comparison this requirement actually
describes — `source.currency == destination.currency == amount.currency` — needs the `Account`
objects, and is therefore the posting service's guard (design §5.1, step 1), checked before any
`Entry` is built.

##### Scenario: Cross-currency transfer is rejected before posting

- GIVEN a source account in `USD` and a destination account in `EUR`
- WHEN `transfer(...)` is called with them
- THEN `CurrencyMismatchError` is raised and no `Entry` or `Transfer` is constructed

##### Scenario: Mismatched legs fed directly to `Transfer` fail a different, correct way

- GIVEN a debit leg of `100 USD` and a credit leg of `100 EUR`, otherwise well-formed
- WHEN `Transfer(...)` is constructed directly from them
- THEN `UnbalancedTransferError` is raised, not `CurrencyMismatchError` — each currency's total is
  individually nonzero, which is the correct diagnosis for something that skipped the posting
  service's own check

#### Requirement: A Transfer Is Frozen and Carries No Status

`Transfer` MUST be immutable after construction and MUST NOT expose a status field or any value
implying an intermediate state.

*(I7; §5 — a transfer is born already confirmed, with no observable intermediate state.)*

##### Scenario: Transfer has no mutable status

- GIVEN a constructed `Transfer`
- WHEN its attributes are inspected
- THEN no field represents pending/posted/failed states, and no attribute is reassignable

#### Requirement: A Transfer Carries Idempotency Key and Requester Provenance

Every `Transfer` MUST carry its `idempotency_key` and `requested_by` (`OwnerId`) as permanent,
immutable fields of the ledger entry itself, not only of an external idempotency record.

*(§4.2; §6.3 — money movements are the only operations that carry a key; §7.1 — `requested_by`
makes a reversal attributable.)*

##### Scenario: Provenance is retained on the transfer

- GIVEN a `Transfer` posted with `idempotency_key=K`, `requested_by=owner_id O`
- WHEN the transfer is later read
- THEN both `K` and `O` are still present on it, independent of any external record's retention

---

### Posting (domain service: `transfer`, `revert`)

#### Requirement: Posting a Transfer Produces Balanced Entries and Applies Them Atomically in the Domain

`transfer(...)` MUST validate I3/I4/I5, construct the two legs (`DEBIT` on source, `CREDIT` on
destination), apply each to its `Account` via `Account.apply()`, and return the resulting `Transfer`.
There MUST be no domain code path that produces an `Entry` without the corresponding balance change,
or vice versa.

*(G1, I1, I2 — the balance move and the entry that explains it are produced by the same function.)*

##### Scenario: Posting a valid transfer moves both balances and records both legs

- GIVEN a `USER` source with balance `200 USD` and a `USER` destination with balance `0 USD`
- WHEN `transfer(amount=Money(50, USD), ...)` is called
- THEN the source balance becomes `150 USD`, the destination becomes `50 USD`, and the returned
  `Transfer` has exactly two entries whose signed amounts net to zero

##### Scenario: A refused debit aborts the whole posting

- GIVEN a `USER` source with balance `10 USD`
- WHEN `transfer(amount=Money(50, USD), ...)` is called
- THEN `InsufficientFundsError` is raised and neither account's balance changes

#### Requirement: A Reversal Is an Ordinary Transfer That References Its Original

`revert(original, ...)` MUST produce mirror legs of the original transfer's amount and
currency, set `reverses=original.id`, and MUST NOT mutate the original `Transfer` in any way.

*(I7 — corrections happen by compensating entry, never by mutation; §7.1 — the resulting transfer
carries `requested_by` set to the authorizing operator.)*

##### Scenario: Reversal references the original without touching it

- GIVEN an already-posted `Transfer` with id `T1`
- WHEN `revert(original=T1, ...)` is called
- THEN a new `Transfer` is returned with `reverses=T1`, and `T1`'s fields are unchanged

#### Requirement: A Reversal Always Posts in Full, Even Into Negative Territory

Reversing a transfer MUST debit the original destination via `debit_for_reversal()` (not `debit()`)
and credit the original source via `credit()`. The reversal MUST succeed in full regardless of the
destination's current balance; it MUST NOT be refused for insufficient funds.

*(§7.3, G4 — the debt sits on the account that owes it, attributed to a specific customer, rather
than being refused or absorbed elsewhere.)*

##### Scenario: Reversal drives the recipient negative and still succeeds

- GIVEN Ana sends `100 USD` to Bruno (Bruno's balance is now `100 USD`)
- AND Bruno spends `80 USD` (Bruno's balance is now `20 USD`)
- WHEN an operator calls `revert` on the original transfer
- THEN Bruno is debited `100 USD` via `debit_for_reversal`, his balance becomes `-80 USD`
- AND Ana is credited `100 USD`
- AND no error is raised

---

### Internal guards

These four are not business rules a customer can trigger. They catch a caller inside this service
assembling something incoherent, and they exist because the alternative to raising is writing a
ledger entry that no one can explain later. They are specified here because the design enforces
them, and a guard the specification does not state is a test with nothing behind it.

#### Requirement: A Leg Is Applied Through the Method Matching Its Direction

`Account.credit()` MUST reject an entry whose direction is `DEBIT`, and `Account.debit()` and
`Account.debit_for_reversal()` MUST reject one whose direction is `CREDIT`.

*(I1 — the sign is chosen once, on the entry. A method that accepted either direction would be a
second place the sign is decided, and two places can disagree.)*

##### Scenario: Credit refuses a debit leg

- GIVEN an `ACTIVE` account and an entry with direction `DEBIT`
- WHEN `credit(entry)` is called
- THEN `EntryDirectionMismatchError` is raised and no successor account is produced

#### Requirement: A Transfer's Legs Must Describe That Transfer

`Transfer` construction MUST reject legs that do not belong to it: any entry whose `transfer_id`
differs from the transfer's own, fewer than two legs, a `source_account_id` absent from the debited
legs, or a `destination_account_id` absent from the credited legs.

*(G1 — a balance is explained by its entries. Entries that describe a different movement explain
nothing.)*

##### Scenario: A leg belonging to another transfer is rejected

- GIVEN legs that net to zero, one of which carries a different `transfer_id`
- WHEN `Transfer(...)` is constructed
- THEN `MalformedTransferError` is raised

#### Requirement: `occurred_at` Must Be Timezone-Aware

Every `Transfer` and `Entry` timestamp MUST carry a timezone. A naive datetime is rejected at
construction.

*(G1 — the entry trail is the audit record. "14:30" is not a time until you know where, and a ledger
ordered by ambiguous timestamps cannot be reconciled across a daylight-saving boundary.)*

##### Scenario: A naive timestamp is rejected

- GIVEN an otherwise valid transfer whose `occurred_at` has no `tzinfo`
- WHEN it is constructed
- THEN `NaiveTimestampError` is raised

#### Requirement: A Reversal's Legs Mirror the Original

`revert()` MUST produce legs that mirror the original transfer: the same amount and currency, with
the original's debited account credited and its credited account debited.

*(I7 — a compensating entry that does not compensate is just another transfer wearing a reference to
one.)*

##### Scenario: A reversal that does not mirror is rejected

- GIVEN an original transfer of `100 USD` from A to B
- WHEN `revert()` is called with accounts that are not the original's destination and source
  (e.g. B and some unrelated account C, instead of B and A)
- THEN `ReversalMismatchError` is raised

Note: the amount cannot be the thing that mismatches here — `revert()` takes no `amount` parameter at
all (design D10); it always inherits `original.amount`. The only way to construct a reversal whose
legs do not mirror the original is to pass it the wrong *accounts*, which is what the test covering
this requirement (`test_posting.py::TestRevert::test_reversal_mismatch_when_accounts_are_not_the_
originals_legs`) actually exercises. An earlier revision of this scenario described a mismatched
*amount*, which is not an input the implemented API can even accept — corrected here to match.

---

## Constraints Enforced Outside the Domain

These rules are real requirements of the system but cannot be enforced by any single aggregate —
each is a question about *other* rows, and a check-then-act inside the domain loses the race. They
are listed here so they are not silently dropped when persistence is designed.

| Rule | Cannot be enforced by | Must be carried by |
| --- | --- | --- |
| A transfer is reversed at most once | Any `Transfer` or `Account` instance — "has this been reversed?" is a question about *other* `Transfer` rows | A partial unique index on `transfers.reverses` |
| `(owner, purpose, currency)` is unique per account | `Account.open()` alone — two concurrent opens would both read "none exists" | A unique index on that triple |
| Entries are never updated or deleted (I6, persistence half) | In-process immutability only guards this process's own code | No UPDATE/DELETE grant path on the entries table |
| Idempotency replay-safety for money movements (§6) | The domain has no ambient state to check a key against | Idempotency record storage, written in the same transaction as the entries (out of scope here) |

---

## Resolved after the first draft

These three were left unnamed by the accepted design and were flagged rather than invented. They are
naming decisions inside existing conventions, not product decisions, and are recorded here so they
can be overruled cheaply.

- **Invalid `(AccountType, AccountPurpose)` pair** raises `InvalidAccountPurposeError`. Classified
  Client: the domain does not assume an adapter has already narrowed the enum.
- **Refused `Account.close()`** raises `AccountNotEmptyError` or `AccountNotClosableError`, split by
  actionability as described under Domain Errors.
- **`IdempotencyKey`** MUST be non-empty after stripping surrounding whitespace and at most 255
  characters; it raises `InvalidIdempotencyKeyError` otherwise. No format is imposed, because the
  key is client-generated and a UUID, a ULID and a request hash are all legitimate. The bound exists
  because an unbounded client-supplied string is a storage and abuse concern, not because any
  particular length is meaningful.

## Open Questions

None outstanding for the domain. Product-level questions remain in `docs/prd.md` §12.
