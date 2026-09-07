## Verification Report

**Change**: account-balance-domain
**Version**: spec.md as of 2026-09-06 (post-23:37 revision); tasks.md as of 2026-09-06 22:29
**Mode**: Strict TDD (runner: `uv run pytest`)

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 28 (Work Units 1-7) |
| Tasks complete | 28 |
| Tasks incomplete | 0 |

Every checkbox in `openspec/changes/account-balance-domain/tasks.md` is `[x]`; `rg '^\s*-\s*\[ \]'` against
the file returns nothing. Cross-checked against the actual module tree: all 6 domain modules
(`errors.py`, `identifiers.py`, `entry.py`, `account.py`, `transfer.py`, `posting.py`) plus both
`__init__.py` files exist and match design §1's layout exactly.

### Build & Tests Execution

**Tests**: ✅ 131 passed / 0 failed / 0 skipped
```text
$ uv run pytest
131 passed in 0.50s
```
96 of these are under `tests/unit/account_balance/domain/`; the remainder (35) are pre-existing
`shared` tests (`Money`, `IdGenerator`), unaffected by this change.

**Gates** — all run fresh, not inferred from prior reports:
```text
$ uv run pre-commit run --all-files
check for added large files..............................................Passed
check for merge conflicts................................................Passed
check toml...............................................................Passed
check yaml................................................................Passed
fix end of files..........................................................Passed
trim trailing whitespace..................................................Passed
detect private key........................................................Passed
ruff (lint)...............................................................Passed
ruff (format)..............................................................Passed
mypy (strict).............................................................Passed
uv.lock matches pyproject.toml............................................Passed

$ uv run mypy src tests
Success: no issues found in 58 source files
```
`.pre-commit-config.yaml`'s `mypy` hook already runs `args: [src, tests]` — confirmed the gate covers
both trees, matching what was run standalone. Working tree was `git status --short` clean before and
after every command in this pass (verified explicitly after the mutation/architecture probes below).

**Coverage**: not configured as a gate for this change (`pytest-cov` is a dev dependency but no
threshold/report is wired into `pyproject.toml` or pre-commit) — not a failure, just not measured.

### Mutation checks (I1, I2) — executed, not inferred

For each, the guard was weakened in-place, the relevant tests were run to confirm a real failure (not
a vacuous pass), then the file was restored via `git checkout --`.

| Invariant | Mutation | Result before revert | Tests that caught it |
|---|---|---|---|
| I2 | `account.py::OverdraftPolicy.assert_allows` — prefixed the guard with `if False and ...` (never raises) | 4 tests failed | `test_account.py::TestDebit::test_user_debit_refused_when_it_would_go_negative`, `test_account.py::TestDebitForReversal::test_the_ordinary_path_still_refuses_the_same_amount`, `test_account_properties.py::...test_user_balances_never_go_negative_and_pool_sum_is_conserved` (P3), `test_posting.py::TestTransfer::test_a_refused_debit_aborts_the_whole_posting` |
| I1 | `transfer.py::Transfer.__post_init__` — prefixed the netting check with `if False and ...` (never raises) | 2 tests failed | `test_transfer.py::TestNetsToZeroPerCurrency::test_an_unbalanced_pair_is_rejected`, `test_transfer_properties.py::...test_construction_succeeds_iff_every_currency_nets_to_zero` (P2) |

`git status --short` was empty after both reverts; `uv run pytest` was re-run clean (131 passed) after
restoring.

### Architecture test verification — executed, not inferred

| Test | Violation introduced | Result before revert |
|---|---|---|
| `test_no_upward_imports` (T7.2) | Appended `from modules.account_balance.domain.identifiers import AccountId` to `errors.py` (an upward import) | Failed: `AssertionError: assert {'errors': {'identifiers'}} == {}` |
| `test_no_ambient_time_or_id_generation` (T7.3) | Added `import datetime; _TEMP_VIOLATION = datetime.datetime.now()` at module level in `entry.py` | Failed: `AssertionError: assert {'entry.py': ['datetime.now']} == {}` |

Both files were restored via `git checkout --` immediately after observing the failure;
`git status --short` was empty afterward.

**One incidental finding from this probe, corrected in-flight, not a defect in the shipped code**:
my first attempt at the ambient-call violation used `from datetime import datetime as _dt; _dt.now()`
(an aliased import) and the test did **not** catch it, because `_call_root_name` resolves to the
literal alias `"_dt"` and the check is `"datetime" in root.lower()` — an alias that doesn't contain the
substring `"datetime"` evades the guard. This is **not** a finding against the shipped code (nothing in
`domain/` uses an aliased `datetime` import today — confirmed by grep), but it is a real, narrow gap in
the guard itself: an aliased `import datetime as dt` followed by `dt.now()` would not be caught. Noted
as a SUGGESTION below since it does not affect current compliance.

*(Also incidental: while constructing the ambient-call violation, an `sd` regex intended only for
`entry.py` also matched and modified `src/modules/shared/domain/money.py` — caught immediately via
`git status`, and reverted with `git checkout --` before running anything against it. Confirmed via
`git diff --stat` that no unintended change survived into any test run.)*

### Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Typed Identifiers Are Not Interchangeable | Different identifier types never compare equal | `test_identifiers.py::TestTypedIdentifiersAreNotInterchangeable::test_different_identifier_types_never_compare_equal` | ✅ COMPLIANT |
| Account Identifiers Support Deterministic Ordering | Two account ids are orderable | `test_identifiers.py::TestAccountIdentifiersSupportDeterministicOrdering::test_two_account_ids_are_orderable` | ✅ COMPLIANT |
| Account Type and Purpose Form a Validated Pair | Valid pair opens / Invalid pair is rejected | `test_account.py::TestOpen::test_valid_pair_opens_at_zero_balance_and_active`, `::test_invalid_pair_is_rejected` | ✅ COMPLIANT |
| Every New Account Opens at Zero Balance | Opening never accepts a starting balance / Reconstitution restores non-zero | `test_account.py::TestOpen::test_opening_never_accepts_a_starting_balance` (USER+SYSTEM), `TestReconstitute::test_restores_a_non_zero_account_without_going_through_open` | ✅ COMPLIANT |
| Debit Refuses to Drive a USER Account Below Zero | Refused / succeeds to zero / SYSTEM no floor | `test_account.py::TestDebit::test_user_debit_refused_when_it_would_go_negative`, `::test_user_debit_succeeds_down_to_exactly_zero`, `::test_system_debit_has_no_floor` | ✅ COMPLIANT (mutation-verified) |
| Reversal Debit Is the Sole Path That May Cross Zero | `debit_for_reversal` succeeds where `debit` refuses / ordinary path still refuses | `test_account.py::TestDebitForReversal::test_succeeds_where_debit_would_refuse`, `::test_the_ordinary_path_still_refuses_the_same_amount` + `test_architecture.py::test_debit_for_reversal_is_referenced_only_in_definition_and_revert` | ✅ COMPLIANT |
| Credit Always Increases the Balance | Credit increases any account's balance | `test_account.py::TestCredit::test_credit_increases_any_accounts_balance` (parametrized USER/SYSTEM) | ✅ COMPLIANT |
| An Inoperable Account Rejects Debits and Credits | Closed account refuses debit / credit | `test_account.py::TestOperability::test_a_closed_account_refuses_a_debit`, `::test_a_closed_account_refuses_a_credit` | ✅ COMPLIANT |
| Closing an Account Requires Exactly Zero Balance and USER Type | Zero-balance closes / non-zero blocks / SYSTEM cannot | `test_account.py::TestClose` (3 tests) | ✅ COMPLIANT |
| Ownership Assertion Is a Domain Fact | Mismatch rejected / match passes | `test_account.py::TestOwnership` (2 tests) | ✅ COMPLIANT |
| An Entry Is a Single, Directional, Strictly Positive Movement | Zero-amount rejected / signed amount derives | `test_entry.py::TestEntryIsAStrictlyPositiveMovement`, `TestSignedAmount` | ✅ COMPLIANT |
| Entries Are Immutable and Append-Only In-Process | No mutator exists | `test_entry.py::TestEntryIsImmutable::test_no_mutator_exists` | ✅ COMPLIANT |
| A Transfer Nets to Zero, Per Currency | Two balanced legs pass / unbalanced pair rejected | `test_transfer.py::TestNetsToZeroPerCurrency` + `test_transfer_properties.py` (P2, general form) | ✅ COMPLIANT (mutation-verified) |
| A Transfer's Amount Is Strictly Positive | Zero / negative rejected | `test_transfer.py::TestAmountIsStrictlyPositive` (2 tests) | ✅ COMPLIANT |
| Source and Destination Must Differ | Self-transfer rejected | `test_transfer.py::TestSourceAndDestinationMustDiffer` + `test_posting.py::test_self_transfer_is_rejected_before_any_account_is_touched` | ✅ COMPLIANT |
| Source and Destination Currencies Must Match — *scenario 1* | Cross-currency transfer rejected before posting | `test_posting.py::TestTransfer::test_cross_currency_transfer_is_rejected_before_posting` | ✅ COMPLIANT |
| Source and Destination Currencies Must Match — *scenario 2* | Mismatched legs fed directly to `Transfer` fail a different, correct way (`UnbalancedTransferError`, not `CurrencyMismatchError`) | **none** | ❌ UNTESTED — see CRITICAL-1 |
| A Transfer Is Frozen and Carries No Status | No mutable status field | `test_transfer.py::TestTransferIsFrozenAndCarriesNoStatus` | ✅ COMPLIANT |
| A Transfer Carries Idempotency Key and Requester Provenance | Provenance retained | `test_transfer.py::TestProvenanceIsRetained` | ✅ COMPLIANT |
| Posting a Transfer Produces Balanced Entries, Applied Atomically | Valid posting moves both balances / refused debit aborts whole posting | `test_posting.py::TestTransfer::test_posting_a_valid_transfer_moves_both_balances_and_records_both_legs`, `::test_a_refused_debit_aborts_the_whole_posting` (+ atomicity regression `test_transfer_into_a_closed_destination_leaves_source_balance_unchanged`) | ✅ COMPLIANT |
| A Reversal Is an Ordinary Transfer That References Its Original | References original without touching it | `test_posting.py::TestRevert::test_reversal_references_the_original_without_touching_it` | ✅ COMPLIANT |
| A Reversal Always Posts in Full, Even Into Negative Territory | Reversal drives recipient negative and still succeeds (golden PRD §7.3 test) | `test_posting.py::TestRevert::test_reversal_drives_the_recipient_negative_and_still_succeeds` | ✅ COMPLIANT |
| A Leg Is Applied Through the Method Matching Its Direction | Credit refuses a debit leg (and reverse) | `test_account.py::TestDebit::test_credit_direction_entry_reaching_debit_raises_entry_direction_mismatch`, `TestCredit::test_debit_direction_entry_reaching_credit_raises_entry_direction_mismatch` | ✅ COMPLIANT |
| A Transfer's Legs Must Describe That Transfer | Leg from another transfer / fewer than two legs / roles reversed | `test_transfer.py::TestTransfersLegsMustDescribeThatTransfer` (3 tests) | ✅ COMPLIANT |
| `occurred_at` Must Be Timezone-Aware | Naive timestamp rejected | `test_entry.py::TestOccurredAtMustBeTimezoneAware`, `test_transfer.py::TestOccurredAtMustBeTimezoneAware` | ✅ COMPLIANT |
| A Reversal's Legs Mirror the Original | Reversal that does not mirror is rejected | `test_posting.py::TestRevert::test_reversal_mismatch_when_accounts_are_not_the_originals_legs` | ⚠️ PARTIAL — test covers a real, correctly-designed guard, but not the *literal* scenario text; see WARNING-2 |

**Compliance summary**: 24/25 scenario groups fully compliant, 1 untested (CRITICAL-1), 1 partial by
literal-text mismatch (WARNING-2, functionally sound).

### Design Coherence

| Decision / section | Followed? | Notes |
|---|---|---|
| §1 Module layout & import direction | ✅ Yes | Verified by reading every file; also mutation-tested via `test_no_upward_imports` |
| §2 Identifiers (`EntityId`, `order=True` on `AccountId`, `PLATFORM_OWNER_ID`) | ✅ Yes | Matches exactly |
| §3 `Entry` — sole sign derivation | ✅ Yes | `signed_amount` is the only place a sign is chosen; confirmed `Account` never inspects `direction` for sign, only for routing |
| §4.2 Delta — instance-returning methods (no `PendingApplication`) | ✅ Yes, consistently | All three of `credit`/`debit`/`debit_for_reversal` use `dataclasses.replace`, none mutate `self`; also verified structurally by `test_account_has_no_in_place_mutator` |
| §4.2 "no second currency check" (I4 via `Money.__add__`) | ⚠️ Implemented, untested at `Account` level | See WARNING-1 |
| §4.3 `debit_for_reversal` architecture test | ✅ Yes | `test_debit_for_reversal_is_referenced_only_in_definition_and_revert` passes and was confirmed to fail on a synthetic violation in a prior session (per apply-progress) |
| §4.5 `close()` guard order | ✅ Yes | Closability checked before balance, matching the code read; consistent with the one documented judgment call in the decision log |
| §5.1/§5.2 `transfer`/`revert` signatures, incl. `entry_ids: Callable[[], EntryId]` | ✅ Yes, consistently | Both functions use the callable form; no remaining `tuple[EntryId, EntryId]` anywhere in `src/` |
| §6 Invariant enforcement map (I1-I7, G5) | ✅ Yes | I1/I2 mutation-verified above; I3/I5/I7 confirmed by reading + passing tests; I6 structural (frozen/slots), confirmed |
| §9.3 Architecture tests (4 items) | ✅ Yes | All 4 present and green; T7.2/T7.3 mutation-verified in this pass, T4.8/T7.1 verified via the debit_for_reversal test |
| §1 posting.py "Imports from" column | ⚠️ Incomplete in the doc | Table omits `money`/shared errors, which `posting.py` does import (`Money`, `CurrencyMismatchError`) — doc nit, see SUGGESTION-2 |
| §10 stated deltas (this section itself) | ✅ Correctly documented | Both deltas (instance-returning methods, callable `entry_ids`) match code; not re-litigated per instructions |
| §5.3 "Open, to revisit" note | N/A | Correctly informational per instructions; not flagged |
| Proposal §8b PRD-revision table | ✅ Consistent | Spot-checked against code; no fresh contradiction found beyond the two known deltas |

### Issues Found

**CRITICAL**:
1. **Spec scenario "Mismatched legs fed directly to `Transfer` fail a different, correct way" has no
   covering test.** `openspec/specs/account-balance/spec.md`, under "Source and Destination Currencies
   Must Match", states: *GIVEN a debit leg of 100 USD and a credit leg of 100 EUR, otherwise
   well-formed, WHEN `Transfer(...)` is constructed directly from them, THEN
   `UnbalancedTransferError` is raised, not `CurrencyMismatchError`*. No test in
   `tests/unit/account_balance/domain/test_transfer.py` or elsewhere constructs this exact 2-leg case.
   The closest coverage is `test_transfer_properties.py`'s P2 property test, but its generator always
   keeps a balanced base USD pair present and only adds 0-4 *extra* legs on top — it never produces the
   minimal 2-leg (one USD debit, one EUR credit) shape the spec's GIVEN describes. I manually
   constructed exactly the spec's scenario in a REPL and confirmed the code's behavior **is** correct
   (`UnbalancedTransferError` is raised, exact totals shown: `{USD: -100, EUR: 100}`) — this is a test
   coverage gap, not a behavioral defect. Root cause: this scenario was added to spec.md during the
   2026-09-06 23:37 revision (per `docs/decision-log.md`), which post-dates `tasks.md` (written
   22:29) — `tasks.md` only ever names the first ("Cross-currency transfer is rejected before
   posting") scenario, never this second one, so no task was ever created for it. Recommend adding one
   `pytest.raises(UnbalancedTransferError)` example test directly against `Transfer.__post_init__`
   before archiving.

**WARNING**:
1. **I4 currency agreement at the `Account` level (leg vs. account currency, distinct from
   source/destination/amount agreement at the posting-service level) has no dedicated test.**
   `src/modules/account_balance/domain/account.py::Account._validated_balance` relies on
   `self.balance + entry.signed_amount` (`Money.__add__`) to raise `CurrencyMismatchError` when an
   entry's currency differs from the account's own — design §4.2 states this explicitly ("there is no
   second currency check"). No test in `test_account.py` calls `credit()`/`debit()`/
   `debit_for_reversal()` with a mismatched-currency `Entry`. I manually verified the guard fires
   correctly (`CurrencyMismatchError: cannot operate on USD and EUR without a rate`), but this path is
   entirely reachable from the public `Account` API independent of the posting service's own upfront
   check, and today nothing in the test suite would catch a regression here (e.g., someone
   accidentally reordering `_validated_balance` to check direction before computing the balance would
   not be caught by any currency-specific assertion). Recommend one direct `Account`-level test.
2. **Spec scenario "A reversal that does not mirror is rejected" describes an input the implemented
   `revert()` cannot receive.** `openspec/specs/account-balance/spec.md` states: *GIVEN an original
   transfer of 100 USD from A to B, WHEN a reversal is constructed whose legs move 50 USD, THEN
   `ReversalMismatchError` is raised*. But `posting.py::revert()` does not take an `amount` parameter
   at all (design §5.2 point 1 / proposal D10: "amount is not a parameter — it is `original.amount`").
   There is no way to construct "a reversal whose legs move 50 USD" through the implemented, tested
   API — the amount is always inherited from `original`. The actual guard implemented and tested
   (`test_posting.py::TestRevert::test_reversal_mismatch_when_accounts_are_not_the_originals_legs`)
   checks **account identity** (source/destination must be the original's destination/source), per
   design §5.2 point 2 and `tasks.md` T6.2's own citation — which correctly cites design, not the
   spec's literal text, for this test. The underlying requirement ("a reversal that does not mirror
   the original is rejected") **is** enforced and **is** tested; only the spec's illustrative example
   is stale relative to the finalized `revert()` signature. Recommend updating spec.md's GIVEN/WHEN/THEN
   to describe a mismatched-account reversal rather than a mismatched-amount one.

**SUGGESTION**:
1. **The `test_no_ambient_time_or_id_generation` architecture test can be evaded by import aliasing.**
   `_call_root_name` in `test_architecture.py` resolves a call's root name and checks
   `"datetime" in root.lower()`; an `import datetime as dt` followed by `dt.now()` would produce
   `root == "dt"`, which does not contain the substring `"datetime"` and so is not flagged. Verified
   with a temporary, reverted mutation (see above) — the unaliased form is caught correctly; the
   aliased form is not. Nothing in the current codebase does this (confirmed by grep), so this is not
   a live compliance gap, but the guard's robustness could be improved by resolving the imported name
   back to its origin module (e.g., via the `ast.Import`/`ast.ImportFrom` alias map) rather than string-
   matching the call-site identifier.
2. **`design.md` §1's module layout table understates `posting.py`'s imports.** The "Imports from"
   column for `domain/posting.py` lists `account, transfer, entry, identifiers, errors`, omitting
   `money` (the actual code imports `Money` from `shared.domain.money` and `CurrencyMismatchError` from
   `shared.domain.errors`, both used in the I4 guard at the top of `transfer()`). Every other row in
   the same table does list `money` where it applies (e.g. `entry.py`, `account.py`, `transfer.py`).
   Cosmetic-only; no functional impact.

### Verdict
**PASS WITH WARNINGS** — 131/131 tests pass, all gates clean (ruff, mypy strict over src+tests,
pre-commit), I1/I2 mutation-tested with real failures observed, both architecture tests confirmed to
catch synthetic violations, all 28 tasks genuinely complete and matching the code on disk. One
CRITICAL gap (an untested-but-behaviorally-correct spec scenario, root-caused to a tasks.md/spec.md
sequencing gap on 2026-09-06) and two WARNINGs (one untested-but-correct design-level guard, one stale
spec example text) should be closed before archiving; none represent a broken invariant or a task
falsely marked complete.
