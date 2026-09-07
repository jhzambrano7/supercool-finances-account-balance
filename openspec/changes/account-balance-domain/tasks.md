# Tasks: Account Balance — Domain Layer

Scope: `src/modules/account_balance/domain/` and `tests/unit/account_balance/domain/` only. No use
cases, no HTTP, no persistence, no migrations — those are out of scope and are called out below
wherever a task would otherwise be tempted to reach for them.

Strict TDD is active (test runner: `uv run pytest`). Every pair below is ordered test-task-first,
implementation-task-second. Do not write the implementation task before its test task fails for the
right reason (import error or assertion failure on missing behaviour — not a typo).

Dependency order follows design §1's strictly downward import chain:
`errors → identifiers → entry → account → transfer → posting`. Nothing later in this list may be
started before everything earlier in its column is green, because each module imports the ones
before it.

`hypothesis>=6.120` is already a dev dependency (verified in `pyproject.toml`) — not re-tasked.
`Money`, `Currency`, `DomainError`, `InvalidCurrencyError`, `InvalidAmountError`,
`CurrencyMismatchError`, and the UUIDv7 id generator are already implemented and tested — not
re-tasked.

---

## Work Unit 1 — Domain error taxonomy (`domain/errors.py`)

No dependencies. Every other module needs these names to exist first.

- [x] **T1.1 (test)** — Write `tests/unit/account_balance/domain/test_errors.py` asserting: every new
      error listed in spec's "Domain Errors" table and design §7 (`InsufficientFundsError`,
      `NonPositiveAmountError`, `SelfTransferError`, `AccountNotOperableError`,
      `AccountOwnershipError`, `UnbalancedTransferError`, `EntryAccountMismatchError`,
      `InvalidAccountPurposeError`, `AccountNotEmptyError`, `AccountNotClosableError`,
      `InvalidIdempotencyKeyError`, `EntryDirectionMismatchError`, `MalformedTransferError`,
      `NaiveTimestampError`, `ReversalMismatchError`) is a subclass of `shared.domain.errors.DomainError`,
      and that `CurrencyMismatchError` is imported from `shared`, never redefined in this module.
      Satisfies: spec "Domain Errors" table (all rows); design §7.
- [x] **T1.2 (impl)** — Implement `src/modules/account_balance/domain/errors.py` with exactly those
      classes, `Error` suffix per ruff `N818`, importing `CurrencyMismatchError` from
      `modules.shared.domain.errors` and re-exporting nothing it does not define. Satisfies: same as
      T1.1.

**Note on naming split**: the spec's "Resolved after the first draft" section explicitly names
`AccountNotEmptyError`/`AccountNotClosableError` and `InvalidAccountPurposeError`/
`InvalidIdempotencyKeyError` as naming decisions, not product decisions — flag any reviewer pushback
on the split back to that section rather than re-litigating it here.

Commit boundary: this work unit alone is a coherent commit (`errors.py` + its test), `PRD:` trailer
referencing I2/I3/I5/G5/§4.4/§6.3/§7.2.

---

## Work Unit 2 — Identifiers (`domain/identifiers.py`)

Depends on Work Unit 1 (errors).

- [x] **T2.1 (test)** — Write `tests/unit/account_balance/domain/test_identifiers.py` covering the
      example-based cases design §9.2 names explicitly: `AccountId(u) != TransferId(u)` for the same
      UUID (spec "Different identifier types never compare equal"), and two distinct `AccountId`
      values are totally ordered (`<`/`>` consistent on repeated comparison — spec "Two account ids
      are orderable"). Also assert `EntityId.__post_init__` rejects a non-`UUID` value, and that
      `PLATFORM_OWNER_ID` is the nil UUID. Satisfies: spec "Typed Identifiers Are Not Interchangeable",
      "Account Identifiers Support Deterministic Ordering"; design §2.
- [x] **T2.2 (test)** — Add `IdempotencyKey` cases to the same file: rejects empty string, rejects a
      whitespace-only string, rejects a string over 255 characters (after stripping), accepts
      exactly 255 characters, strips surrounding whitespace. Satisfies: spec `IdempotencyKey` row in
      "Resolved after the first draft"; design §2 last bullet. This has no dedicated spec scenario —
      the 255-character bound is documented only in the "Resolved after the first draft" section, not
      as a Given/When/Then requirement, so this task cites that section rather than inventing a
      requirement heading that does not exist in the spec.
- [x] **T2.3 (impl)** — Implement `src/modules/account_balance/domain/identifiers.py`: `EntityId`
      base, `AccountId` (`order=True`), `TransferId`, `EntryId`, `OwnerId`, `IdempotencyKey`,
      `PLATFORM_OWNER_ID`. All frozen, all `slots=True`. Satisfies: same as T2.1/T2.2.

Commit boundary: Work Unit 2 (`identifiers.py` + tests) is one commit, `PRD:` trailer referencing
G1 (id typing), PRD §5 step 3 (lock ordering), §6.3 (idempotency key bound).

---

## Work Unit 3 — `Entry` (`domain/entry.py`)

Depends on Work Units 1–2.

- [ ] **T3.1 (test)** — Write `tests/unit/account_balance/domain/test_entry.py`:
      - Zero-amount entry raises `NonPositiveAmountError` (spec "Zero-amount entry is rejected").
      - Negative-amount entry raises `NonPositiveAmountError` (I3, not in spec's own scenario list
        verbatim but required by the requirement's "strictly positive" text — flagged as an obvious
        extension of the stated scenario, not a new requirement).
      - `signed_amount` derives `+amount` for `CREDIT`, `-amount` for `DEBIT` (spec "Signed amount
        derives from direction").
      - A naive (`tzinfo=None`) `occurred_at` raises `NaiveTimestampError` (design §3 — this guard is
        named in design §7's table as design-introduced, not in the spec's own Entry requirements;
        cite design §3, not the spec, for this one).
      - No mutator exists: attribute assignment on a constructed `Entry` fails (spec "No mutator
        exists" — frozen dataclass semantics).
      Satisfies: spec "An Entry Is a Single, Directional, Strictly Positive Movement", "Entries Are
      Immutable and Append-Only In-Process"; design §3, §6 (I3, I6).
- [ ] **T3.2 (impl)** — Implement `src/modules/account_balance/domain/entry.py`: `EntryDirection`
      enum (string values), `Entry` frozen dataclass with `entry_id`, `transfer_id`, `account_id`,
      `direction`, `amount`, `occurred_at`; `__post_init__` guards positivity and timezone-awareness;
      `signed_amount` property. Satisfies: same as T3.1.

Commit boundary: Work Unit 3 is one commit, `PRD:` trailer referencing I3, I6, §4.2 (direction as a
first-class term).

---

## Work Unit 4 — `Account` (`domain/account.py`)

Depends on Work Units 1–3. This is the largest unit; split into three commits internally because the
design itself separates open/reconstitute, the three balance-moving methods, and closure into
distinct concerns — each is independently testable and each maps to a distinct spec section.

### 4a — classification enums, `open()`, `reconstitute()`

- [ ] **T4.1 (test)** — Write `tests/unit/account_balance/domain/test_account.py::TestOpen` and
      `TestReconstitute`:
      - Valid `(USER, CHECKING)` pair opens with balance `Money.zero`, status `ACTIVE` (spec "Valid
        pair opens").
      - Invalid `(USER, FUNDING)` pair raises `InvalidAccountPurposeError` (spec "Invalid pair is
        rejected").
      - `open()` always produces `Money.zero(currency)` regardless of other inputs, for both `USER`
        and `SYSTEM` (spec "Opening never accepts a starting balance").
      - `reconstitute()` restores a non-zero balance and arbitrary status without going through
        `open()` (spec "Reconstitution restores a non-zero account without going through `open()`").
      - `reconstitute()` accepts a *negative* `USER` balance and a `CLOSED` status without raising
        (design §4.1 — "must not re-assert a non-negative `USER` balance"; this is a design
        requirement, not an independent spec scenario, so it is cited to design §4.1, which itself
        traces to PRD §7.3).
      Satisfies: spec "Account Type and Purpose Form a Validated Pair", "Every New Account Opens at
      Zero Balance"; design §4.1.
- [ ] **T4.2 (impl)** — Implement `AccountType`, `AccountPurpose` (with `.account_type` mapping),
      `AccountStatus` enums (string values) and `Account.open()` / `Account.reconstitute()` per design
      §4, §4.1, validating `purpose.account_type is account_type`, `balance.currency == currency`,
      `version >= 0` unconditionally in `__init__`. Satisfies: same as T4.1.

Commit boundary: 4a is one commit, `PRD:` trailer referencing G1, §4.4.

### 4b — `credit`, `debit`, `debit_for_reversal`, operability

- [ ] **T4.3 (test)** — Extend `test_account.py` with `TestDebit`, `TestCredit`,
      `TestDebitForReversal`, `TestOperability`:
      - USER debit refused when it would go negative, balance unchanged (spec "USER debit refused
        when it would go negative").
      - USER debit succeeds down to exactly zero (spec "USER debit succeeds down to exactly zero").
      - SYSTEM debit has no floor, goes negative (spec "SYSTEM debit has no floor").
      - `debit_for_reversal` succeeds where `debit` would refuse, same account, same amount (spec
        "`debit_for_reversal` succeeds where `debit` would refuse").
      - The ordinary `debit` path still refuses the same amount on the same account (spec "The
        ordinary path still refuses the same amount").
      - Credit increases balance for both account types, no floor or ceiling (spec "Credit increases
        any account's balance").
      - Closed account refuses `debit()` (spec "A closed account refuses a debit").
      - Closed account refuses `credit()` (spec "A closed account refuses a credit").
      - Entry for a different account raises `EntryAccountMismatchError` on `credit`/`debit`/
        `debit_for_reversal` (design §4.2 `_validated_balance` guard — not an explicit spec scenario;
        cite design §4.2, and note the spec's error table lists `EntryAccountMismatchError` as
        "Internal guard" without a Given/When/Then, so this test is filling a gap the spec names but
        does not script).
      - A `CREDIT`-direction entry reaches `debit()` (or the reverse) and raises
        `EntryDirectionMismatchError` (design §4.2 table row; not in spec's error table at all —
        design-only guard, flagged as such).
      - `credit`/`debit`/`debit_for_reversal` each return a new `Account` instance and leave the
        receiver's balance untouched (design §4.2's replace-not-mutate rationale — the concrete,
        testable form of that section's worked example).
      Satisfies: spec "Debit Refuses to Drive a USER Account Below Zero", "Reversal Debit Is the Sole
      Path That May Cross Zero", "Credit Always Increases the Balance", "An Inoperable Account
      Rejects Debits and Credits"; design §4.2, §4.4 (`OverdraftPolicy`).
- [ ] **T4.4 (test) — PROPERTY-BASED (design §9.1 P3, P4)** — Write
      `test_account_properties.py` using `hypothesis`:
      - **P3**: for any generated sequence of `transfer`-shaped debit/credit operations over a pool of
        `USER` accounts (constructed via the posting service once it exists — if this task lands
        before Work Unit 6, scope it to direct `Account.debit`/`credit` sequences instead, and defer
        the full-posting version to Work Unit 6's property tests), every `USER` balance stays `>= 0`
        and the sum across the pool is conserved.
      - **P4**: after any generated sequence of entries applied to one `Account`, the resulting
        balance equals the sum of `signed_amount` over every entry applied to it.
      Mark both as property tests explicitly in the test file (module docstring or class docstring
      naming P3/P4), since design §9.1 distinguishes these from example tests and the distinction
      must survive into the test suite, not just this checklist.
      Satisfies: design §9.1 rows P3, P4 (property-based, not spec Given/When/Then scenarios — no
      spec requirement heading corresponds 1:1 to these; they are design-level coverage of I2 totality
      and the entry/balance correspondence).
- [ ] **T4.5 (impl)** — Implement `OverdraftPolicy` enum (`FORBIDDEN`/`UNLIMITED`,
      `assert_allows(resulting_balance, *, account_id)`), `AccountType.overdraft_policy` property,
      `Account._validated_balance`, `credit()`, `debit()`, `debit_for_reversal()`,
      `assert_operable()`. Each of the three public methods returns a new `Account`; none mutates
      `self`. Satisfies: same as T4.3/T4.4.

Commit boundary: 4b is one commit, `PRD:` trailer referencing I2, I4, G4, §7.3.

### 4c — ownership assertion and `close()`

- [ ] **T4.6 (test)** — Extend `test_account.py` with `TestOwnership`, `TestClose`:
      - Owner mismatch raises `AccountOwnershipError` (spec "Owner mismatch is rejected").
      - Owner match passes silently, no error (spec "Owner match passes silently").
      - Zero-balance USER account closes, status becomes `CLOSED` (spec "Zero-balance USER account
        closes").
      - Non-zero balance blocks closure with `AccountNotEmptyError`, status remains `ACTIVE` (spec
        "Non-zero balance blocks closure").
      - SYSTEM accounts cannot be closed — assert the specific error raised is
        `AccountNotClosableError` (spec scenario says only "the closure is rejected" without naming
        the error; the design and the spec's own error table both name
        `AccountNotClosableError` for this case, so the test may assert the concrete type without
        inventing a requirement — this is confirming spec + design agree, not adding a new rule).
      Satisfies: spec "Ownership Assertion Is a Domain Fact", "Closing an Account Requires Exactly
      Zero Balance and `USER` Type"; design §4.5.
- [ ] **T4.7 (impl)** — Implement `Account.assert_owned_by()` and `Account.close()` per design §4.5.
      Satisfies: same as T4.6.

Commit boundary: 4c is one commit, `PRD:` trailer referencing G5, §7.2.

### 4d — architecture tests for `Account`

- [ ] **T4.8 (test) — ARCHITECTURE TEST** — Write
      `tests/unit/account_balance/domain/test_architecture.py::test_debit_for_reversal_is_referenced_only_in_definition_and_revert`:
      parse every `.py` under `src/` with `ast`, collect every `Attribute`/`Name` node named
      `debit_for_reversal`, and assert the set of `(module, enclosing function/None)` pairs equals
      exactly `{("account_balance/domain/account.py", <the method definition itself>),
      ("account_balance/domain/posting.py", "revert")}`. This test can be written now (it will fail
      because `posting.py` does not exist yet, which is the right kind of failure) but it can only go
      green after Work Unit 6, so leave it red/`xfail`-free and revisit at T6 — do not mark it
      `xfail`, since design §4.3 states this must be a plain failing test until the call site exists.
      Satisfies: design §4.3 "Architecture test" (explicit request in task brief); PRD §10.3 ("a test
      asserts no other path can").
- [ ] **T4.9 (test) — ARCHITECTURE TEST** — Write
      `test_architecture.py::test_account_has_no_in_place_mutator`: parse `account.py` with `ast` and
      assert every method whose body reassigns `self.<field>` or calls `object.__setattr__` does not
      exist — every state-changing method must return `Account`. Satisfies: design §9.3 item 2.

Note: T4.8 and T4.9 are written here (after 4a–4c) because they need `account.py` to exist to have
anything to parse, but T4.8 will only fully pass once Work Unit 6 (`posting.py`) exists — this is
called out explicitly rather than silently deferring the task.

Commit boundary: 4d can ride with 4c's commit or be its own small commit; either is coherent.

---

## Work Unit 5 — `Transfer` (`domain/transfer.py`)

Depends on Work Units 1–4 (Transfer's `__post_init__` uses `Entry` and references `AccountId`, and
its guards produce errors from Work Unit 1; it does not import `Account` itself per design §1, but
tests construct `Entry`/`AccountId` fixtures that mirror account state).

- [ ] **T5.1 (test)** — Write `tests/unit/account_balance/domain/test_transfer.py`:
      - Two balanced legs (`DEBIT 100 USD` on A, `CREDIT 100 USD` on B) construct successfully (spec
        "Two balanced legs pass").
      - An unbalanced pair (`DEBIT 100 USD`, `CREDIT 90 USD`) raises `UnbalancedTransferError` (spec
        "An unbalanced pair is rejected").
      - Zero-amount transfer raises `NonPositiveAmountError` (spec "Zero amount rejected").
      - Negative-amount transfer raises `NonPositiveAmountError` (spec "Negative amount rejected").
      - Self-transfer (source == destination) raises `SelfTransferError` (spec "Self-transfer
        rejected").
      - Cross-currency source/destination raises `CurrencyMismatchError` (spec "Cross-currency
        transfer rejected") — note this scenario is stated at the `Transfer`/posting boundary in the
        spec; construct it however `Transfer.__post_init__` can observe it directly, and if it cannot
        be observed without the posting service's currency check (design §5.1 step 1), say so and
        move this specific scenario's test to Work Unit 6 instead of forcing it here.
      - No mutable status field; attributes are not reassignable (spec "Transfer has no mutable
        status").
      - `idempotency_key` and `requested_by` remain present and unchanged when the transfer is read
        back after construction (spec "Provenance is retained on the transfer").
      - `MalformedTransferError` when fewer than two entries are supplied, or an entry's
        `transfer_id` does not match, or neither debited nor credited legs represent
        source/destination (design §5 guard table — no dedicated spec scenario; cite design §5
        directly).
      - Naive `occurred_at` raises `NaiveTimestampError` (design §5 guard table — same note).
      Satisfies: spec "A Transfer Nets to Zero, Per Currency", "A Transfer's Amount Is Strictly
      Positive", "Source and Destination Must Differ", "Source and Destination Currencies Must
      Match", "A Transfer Is Frozen and Carries No Status", "A Transfer Carries Idempotency Key and
      Requester Provenance"; design §5, §5 guard table, §6 (I1, I3, I4, I5, I7).
- [ ] **T5.2 (test) — PROPERTY-BASED (design §9.1 P2)** — Write `test_transfer_properties.py`:
      for any generated set of entries (including synthetic four-leg, two-currency sets),
      `Transfer.__post_init__` succeeds **iff** every currency present nets to zero. Mark explicitly
      as a property test testing the general I1 form ahead of FX existing. Satisfies: design §9.1 row
      P2; spec "A Transfer Nets to Zero, Per Currency" (the general per-currency form, stated in the
      spec's own requirement text, not only in design).
- [ ] **T5.3 (impl)** — Implement `src/modules/account_balance/domain/transfer.py`: `Transfer` frozen
      dataclass and its `__post_init__` guard sequence exactly as design §5 orders them (positivity →
      self-transfer → shape/malformed → timestamp → I1 netting). Satisfies: same as T5.1/T5.2.

Commit boundary: Work Unit 5 is one commit, `PRD:` trailer referencing I1, I3, I4, I5, I7, §4.2.

---

## Work Unit 6 — Posting domain service (`domain/posting.py`)

Depends on Work Units 1–5 (imports `account`, `transfer`, `entry`, `identifiers`, `errors` per
design §1).

- [ ] **T6.1 (test)** — Write `tests/unit/account_balance/domain/test_posting.py::TestTransfer`:
      - Posting a valid transfer moves both balances and records both legs, returned `Transfer` has
        exactly two entries netting to zero (spec "Posting a valid transfer moves both balances and
        records both legs").
      - A refused debit aborts the whole posting: `InsufficientFundsError` raised, *neither* account's
        balance changes (spec "A refused debit aborts the whole posting").
      - Cross-currency transfer (source USD, destination EUR) raises `CurrencyMismatchError` at the
        `transfer()` guard step, if T5.1 deferred this scenario here (see note in T5.1). Satisfies:
        spec "Cross-currency transfer rejected" (if applicable at this layer instead of Transfer's).
      - Self-transfer through `transfer()` raises `SelfTransferError` before any account is touched
        (design §5.1 step 1 — restates I5 at the service boundary, same spec requirement as T5.1's
        self-transfer scenario, exercised through the service entry point this time).
      Satisfies: spec "Posting a Transfer Produces Balanced Entries and Applies Them Atomically in the
      Domain"; design §5.1.
- [ ] **T6.2 (test)** — Extend `test_posting.py::TestRevert`:
      - Reversal references the original without touching it: new `Transfer` returned with
        `reverses=T1`, `T1`'s fields unchanged (spec "Reversal references the original without
        touching it").
      - Reversal drives the recipient negative and still succeeds — the PRD §7.3 / spec golden
        scenario verbatim: Ana sends 100 to Bruno, Bruno spends 80 (via a second `transfer()` call),
        an operator reverses the original; assert Bruno's balance is `-80`, Ana is whole, four entries
        exist across two transfers, the reversal carries `reverses`, and no error is raised (spec
        "Reversal drives the recipient negative and still succeeds"; design §9.2 names this as "the
        golden test for the amended D1" — call it that in the test name or docstring so its
        provenance is traceable).
      - The reversal calls `Account.debit_for_reversal()` on the original destination and `credit()`
        on the original source, not `debit()` (design §5.2 point 3 — confirms the sole call site the
        architecture test in T4.8 checks for).
      - `ReversalMismatchError` when the `source`/`destination` accounts passed to `revert()` are not
        the original transfer's legs (design §5.2 point 2 — Internal guard, not in spec's error table
        at all; cite design only).
      Satisfies: spec "A Reversal Is an Ordinary Transfer That References Its Original", "A Reversal
      Always Posts in Full, Even Into Negative Territory"; design §5.2, §7.3 (PRD).
- [ ] **T6.3 (test) — PROPERTY-BASED (design §9.1 P1)** — Write `test_posting_properties.py`: for any
      two accounts and any amount the source can cover, `transfer()` leaves
      `source.balance + destination.balance` unchanged (conservation). Mark explicitly as testing the
      D2 sign convention property. Satisfies: design §9.1 row P1 — conservation is implied by I1/I2
      together but has no single spec Given/When/Then of its own; cite design §9.1.
- [ ] **T6.4 (test)** — Extend `test_posting.py` (or `test_account.py`, either is defensible — pick
      one and be consistent) with the atomicity example design §9.2 names: `transfer()` into a
      `CLOSED` destination raises and leaves `source.balance` **unchanged**. Satisfies: design §9.2
      "Atomicity" bullet — this is a design-level regression test for the §4.2 fix (mutate-then-
      validate), not a distinct spec scenario; the spec's closest coverage is "An Inoperable Account
      Rejects Debits and Credits", exercised here through the service rather than the method directly.
- [ ] **T6.5 (impl)** — Implement `src/modules/account_balance/domain/posting.py`: `Posting` frozen
      dataclass (`transfer`, `accounts` tuple), `transfer(...)` and `revert(...)` functions exactly
      per design §5.1/§5.2's guard-build-apply-construct-return sequence, grouping/netting legs per
      account before applying (design §4.2 "One application per account per posting"). Satisfies:
      same as T6.1–T6.4.

Commit boundary: Work Unit 6 is likely two commits given its size — `transfer()` (T6.1, T6.3, T6.4
partially, plus the `transfer()` half of T6.5) as one commit, `revert()` (T6.2 plus the `revert()`
half of T6.5) as a second. Both carry `PRD:` trailers: the first referencing G1, I1, I2; the second
referencing I7, G4, §7.1, §7.3.

---

## Work Unit 7 — Remaining architecture tests and module scaffolding

Depends on all of the above (needs every module to exist to parse them).

- [ ] **T7.1 (test) — ARCHITECTURE TEST** — Complete
      `test_architecture.py::test_debit_for_reversal_is_referenced_only_in_definition_and_revert`
      (started in T4.8): now that `posting.py::revert` exists, assert the full set matches exactly —
      this is the point where T4.8 goes from an expected-red guard to green. Satisfies: design §4.3,
      §9.3 item 1; PRD §10.3.
- [ ] **T7.2 (test) — ARCHITECTURE TEST** — `test_architecture.py::test_no_upward_imports`: no module
      under `account_balance/domain/` imports `application`, `adapters`, or a sibling module listed
      later in design §1's import-direction table (i.e., `errors.py` must not import `identifiers.py`,
      `identifiers.py` must not import `entry.py`, etc.). Satisfies: design §9.3 item 3; D6.
- [ ] **T7.3 (test) — ARCHITECTURE TEST** — `test_architecture.py::test_no_ambient_time_or_id_generation`:
      no `datetime.now`, `datetime.utcnow`, `uuid`, `uuid6`, or `IdGenerator` reference exists under
      `domain/`. Satisfies: design §9.3 item 4; D6.
- [x] **T7.4 (impl)** — Add `src/modules/account_balance/__init__.py` and
      `src/modules/account_balance/domain/__init__.py` (explicit, empty or re-exporting only what the
      package intends to expose — match the `_blank` scaffold's convention of empty `__init__.py`
      files unless re-export is established as a project pattern elsewhere, which it is not observed
      to be in the files read for this task). No test needed for this task itself — it is scaffolding,
      not behaviour; its correctness is verified by every other test's import succeeding.

Commit boundary: T7.1–T7.3 are one commit (all architecture tests, now all green). T7.4 rides with
Work Unit 1's commit in practice (the package must exist before anything imports it) — listed last
here only because it has no test of its own to sequence against, not because it is chronologically
last. Flagging this ordering wrinkle explicitly rather than pretending the checklist's numeric order
is also its execution order for this one item.

---

## Explicitly not covered by any domain unit test

Per design §9.4 and the task brief: **no domain unit test can demonstrate G2** (concurrent operations
never corrupting a balance). The domain is single-threaded, holds no lock, and reads no shared state;
a test spawning threads against one in-memory `Account` would demonstrate the Python GIL's scheduling,
not the service's correctness. What this checklist's tests *can* and do demonstrate is the
precondition G2's persistence-phase lock depends on: `debit()` computes against the balance it was
given at call time and either applies cleanly or refuses — it never clamps, partially applies, or
silently succeeds (T4.3, T4.4/P3). The actual concurrency demonstration — N concurrent transfers
against real PostgreSQL connections, asserting the test fails when `FOR UPDATE` is removed — belongs
to the persistence phase and is out of scope for this task list entirely; it is named here only so it
is not silently dropped from the project's test plan.

Also out of scope, per the task brief's scope boundary, and not tasked here even though the design
references them: idempotency replay-safety (needs storage), the partial unique index on
`transfers.reverses` (needs a schema), the `(owner, purpose, currency)` unique constraint (needs a
schema), and use-case-level authorization policy (which legs `assert_owned_by` gets called against —
design §5.3 is explicit that this is a use-case decision, not a domain one).

---

## Review Workload Forecast

Rough line counts by file, estimated from the design's own type signatures and the test breadth each
requirement demands (implementation + test file, combined):

| Work unit | Files | Est. changed lines |
| --- | --- | --- |
| 1 — errors | `errors.py` + test | ~90 |
| 2 — identifiers | `identifiers.py` + test | ~160 |
| 3 — entry | `entry.py` + test | ~140 |
| 4 — account (4a+4b+4c+4d) | `account.py` + 2 test files | ~430 |
| 5 — transfer | `transfer.py` + 2 test files | ~260 |
| 6 — posting | `posting.py` + 3 test files | ~330 |
| 7 — architecture tests + scaffolding | `test_architecture.py` + 2 `__init__.py` | ~90 |
| **Total** | | **~1,500 lines** |

**This exceeds a 400-line review budget** by roughly 3.5x if delivered as a single PR. Work Unit 4
(`Account`) alone is already over budget on its own.

**Chained PRs are recommended.** A natural split follows the commit boundaries already named above:

1. PR 1 — Work Units 1–2 (errors, identifiers): ~250 lines.
2. PR 2 — Work Unit 3 (entry): ~140 lines.
3. PR 3 — Work Unit 4 (account), itself split into 4a/4b/4c/4d as separate commits within the PR, or
   as three smaller PRs (4a; 4b; 4c+4d) if the reviewer wants every PR under 400 lines strictly —
   4a+4b alone is already close to 400.
4. PR 4 — Work Unit 5 (transfer): ~260 lines.
5. PR 5 — Work Unit 6 (posting), split into `transfer()` and `revert()` halves per the commit
   boundary already noted: ~330 lines total, each half comfortably under 400 alone.
6. PR 6 — Work Unit 7 (architecture tests + scaffolding): ~90 lines.

Each PR after the first depends on the one before it (strict downward import order means there is no
parallelism across work units — only within a work unit's test/impl pair, and even that pair is
sequential by TDD). **Decision needed before apply: yes** — confirm with the user whether to chain
all six PRs or to accept `size:exception` on the Account PR specifically, since Work Unit 4 is the one
unit that cannot be cut smaller without breaking the design's own internal grouping (open/reconstitute,
the three balance-moving methods, and closure genuinely belong together as one aggregate's behaviour).
