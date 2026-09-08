"""Unit tests for `RevertTransfer` against fake repositories.

Per openspec/specs/revert/spec.md's Testing Strategy: the negative-balance
case (PRD §7.3's own example), the not-found case, the double-reversal-
conflict case, and idempotent replay -- everything that does not require a
real database transaction (locking itself, and the concurrent double-reversal
race, are integration-only, per transfer's own precedent for the same kind of
claim).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.authorization_gateway import (
    AuthorizationGateway,
    UnauthorizedPrincipalError,
)
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
    IdempotencyRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountCriteria,
)
from modules.account_balance.application.gateways.transfer_repository import (
    TransferAlreadyReversedConflictError,
    TransferRepository,
)
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.application.use_cases.revert_transfer import (
    RevertTransfer,
    RevertTransferRequest,
    TransferAlreadyReversedError,
    TransferNotFoundError,
)
from modules.account_balance.application.use_cases.transfer_money import IdempotencyConflictError
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    PrincipalId,
    TransferId,
)
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")
_OCCURRED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
# The one principal every test's fake `AuthorizationGateway` authorizes, standing in for
# production's `ADMIN_PRINCIPAL_ID` -- a fixed test id, not that real constant, since these tests
# exercise the use case's own authorization *call*, not `FixedAdminAuthorizationGateway`'s specific
# comparison (that adapter has its own test coverage).
_TEST_ADMIN = PrincipalId(uuid4())


class _FakeAuthorizationGateway(AuthorizationGateway):
    def __init__(self, *, authorized: PrincipalId) -> None:
        self._authorized = authorized

    async def authorize(self, principal_id: PrincipalId) -> None:
        if principal_id != self._authorized:
            raise UnauthorizedPrincipalError(principal_id=principal_id)


class _FakeClock(Clock):
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


@dataclass
class _Database:
    """Mirrors `test_transfer_money.py`'s own `_Database` -- the fakes' shared,
    already-committed backing store. Each `_FakeUnitOfWork` writes to its own staged dicts,
    merged back here only on a clean exit."""

    accounts: dict[AccountId, Account] = field(default_factory=dict)
    transfers: dict[TransferId, Transfer] = field(default_factory=dict)
    idempotency: dict[tuple[OwnerId, str], IdempotencyRecord] = field(default_factory=dict)


class _FakeAccountRepository(AccountRepository):
    def __init__(self, database: _Database, staged: dict[AccountId, Account]) -> None:
        self._database = database
        self._staged = staged

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByAccountId(account_id):
                return self._staged.get(account_id) or self._database.accounts.get(account_id)
            case _:
                # RevertTransfer only ever looks accounts up by id -- this
                # fake has no need to support any other criteria.
                raise NotImplementedError

    async def add(self, account: Account) -> None:
        raise NotImplementedError

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        account = self._database.accounts.get(account_id)
        return account if isinstance(account, UserAccount) else None

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        """Sorts, exactly as the real adapter does (T6) -- mirrors `test_transfer_money.py`'s own
        fake."""
        return tuple(
            account
            for account_id in sorted(account_ids)
            if isinstance(account := self._database.accounts.get(account_id), UserAccount)
        )

    async def find_by_owner(self, owner_id: OwnerId) -> tuple[UserAccount, ...]:
        raise NotImplementedError("RevertTransfer never lists accounts by owner")

    async def update(self, account: UserAccount) -> None:
        self._staged[account.account_id] = account


class _FakeTransferRepository(TransferRepository):
    """`add()` simulates the partial unique index on `transfers.reverses` (R4, design §8):
    scanning committed + staged rows for one already reversing the same original, the same fact
    `uq_transfers_reverses` enforces for real against Postgres."""

    def __init__(self, database: _Database, staged: dict[TransferId, Transfer]) -> None:
        self._database = database
        self._staged = staged

    async def add(self, transfer: Transfer) -> None:
        if transfer.reverses is not None:
            already_reversed = {
                existing.reverses
                for existing in (*self._database.transfers.values(), *self._staged.values())
                if existing.reverses is not None
            }
            if transfer.reverses in already_reversed:
                raise TransferAlreadyReversedConflictError(original_transfer_id=transfer.reverses)
        self._staged[transfer.transfer_id] = transfer

    async def get(self, transfer_id: TransferId) -> Transfer | None:
        return self._staged.get(transfer_id) or self._database.transfers.get(transfer_id)


class _FakeIdempotencyRepository(IdempotencyRepository):
    def __init__(
        self, database: _Database, staged: dict[tuple[OwnerId, str], IdempotencyRecord]
    ) -> None:
        self._database = database
        self._staged = staged

    async def find_by_key(
        self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        key = (caller_id, idempotency_key.value)
        return self._staged.get(key) or self._database.idempotency.get(key)

    async def add(self, record: IdempotencyRecord) -> None:
        key = (record.caller_id, record.idempotency_key.value)
        if key in self._database.idempotency or key in self._staged:
            raise IdempotencyRecordConflictError(
                caller_id=record.caller_id, idempotency_key=record.idempotency_key
            )
        self._staged[key] = record


class _FakeUnitOfWork(TransferUnitOfWork):
    def __init__(self, database: _Database) -> None:
        self._database = database
        self._staged_accounts: dict[AccountId, Account] = {}
        self._staged_transfers: dict[TransferId, Transfer] = {}
        self._staged_idempotency: dict[tuple[OwnerId, str], IdempotencyRecord] = {}

    async def __aenter__(self) -> Self:
        self.accounts = _FakeAccountRepository(self._database, self._staged_accounts)
        self.transfers = _FakeTransferRepository(self._database, self._staged_transfers)
        self.idempotency = _FakeIdempotencyRepository(self._database, self._staged_idempotency)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._database.accounts.update(self._staged_accounts)
            self._database.transfers.update(self._staged_transfers)
            self._database.idempotency.update(self._staged_idempotency)


def _unit_of_work_factory(database: _Database) -> Callable[[], _FakeUnitOfWork]:
    return lambda: _FakeUnitOfWork(database)


def _use_case(database: _Database, *, authorized: PrincipalId = _TEST_ADMIN) -> RevertTransfer:
    return RevertTransfer(
        logger=logging.getLogger(__name__),
        unit_of_work_factory=_unit_of_work_factory(database),
        id_generator=IdGenerator(),
        clock=_FakeClock(_OCCURRED_AT),
        authorization_gateway=_FakeAuthorizationGateway(authorized=authorized),
    )


def _account(
    *, owner_id: OwnerId, account_type: AccountType, purpose: AccountPurpose, balance: int
) -> Account:
    account_id = AccountId(uuid4())
    if account_type.is_system():
        return SystemAccount(
            account_id=account_id, owner_id=owner_id, purpose=purpose, currency=USD
        )
    return UserAccount.reconstitute(
        account_id=account_id,
        owner_id=owner_id,
        purpose=purpose,
        currency=USD,
        balance=Money(balance, USD),
        status=AccountStatus.ACTIVE,
        version=0,
    )


def _posted_transfer(
    *,
    transfer_id: TransferId | None = None,
    source: Account,
    destination: Account,
    amount: int,
    requested_by: OwnerId,
    key: str,
    reverses: TransferId | None = None,
) -> Transfer:
    """Builds a `Transfer` directly -- mirrors `test_transfer_money.py`'s own helpers: this stands
    in for an already-posted row the fake `TransferRepository` reconstructs, not something built
    through `posting.transfer()`/`posting.revert()` (which mutate live `Account` instances this
    helper does not need to produce)."""
    tid = transfer_id or TransferId(uuid4())
    money = Money(amount, USD)
    debit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=tid,
        account_id=source.account_id,
        direction=EntryDirection.DEBIT,
        amount=money,
        occurred_at=_OCCURRED_AT,
    )
    credit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=tid,
        account_id=destination.account_id,
        direction=EntryDirection.CREDIT,
        amount=money,
        occurred_at=_OCCURRED_AT,
    )
    return Transfer(
        transfer_id=tid,
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=money,
        requested_by=requested_by,
        idempotency_key=IdempotencyKey(key),
        occurred_at=_OCCURRED_AT,
        entries=(debit, credit),
        reverses=reverses,
    )


def _seed(database: _Database, *accounts: Account) -> None:
    for account in accounts:
        database.accounts[account.account_id] = account


def _request(
    *, transfer_id: TransferId, executed_by: PrincipalId = _TEST_ADMIN, key: str = "rev-1"
) -> RevertTransferRequest:
    return RevertTransferRequest(
        transfer_id=transfer_id, idempotency_key=IdempotencyKey(key), executed_by=executed_by
    )


# --------------------------------------------------------------------------
# R2/R5/R6 -- a completed transfer is reversed, including the unaffordable case
# --------------------------------------------------------------------------


async def test_a_completed_transfer_is_reversed() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=100,
    )
    original = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig"
    )
    database.transfers[original.transfer_id] = original
    _seed(database, ana, bruno)
    use_case = _use_case(database)

    reversal = await use_case.execute(_request(transfer_id=original.transfer_id))

    assert reversal.reverses == original.transfer_id
    assert reversal.source_account_id == bruno.account_id
    assert reversal.destination_account_id == ana.account_id
    assert database.accounts[bruno.account_id].balance == Money(0, USD)  # type: ignore[union-attr]
    assert database.accounts[ana.account_id].balance == Money(100, USD)  # type: ignore[union-attr]
    # The original transfer's own entries are unchanged (I7).
    assert database.transfers[original.transfer_id] is original
    assert len(original.entries) == 2
    assert len(reversal.entries) == 2


async def test_prd_7_3_ana_bruno_worked_example_bruno_ends_up_negative() -> None:
    """GIVEN Ana transferred 100 to Bruno, and Bruno has since spent 80 of

    it elsewhere, WHEN an operator reverses the original, THEN it posts in
    full: Bruno's balance becomes -80, Ana's is restored by 100, and four
    entries exist across the two transfers (PRD §7.3, spec's own golden
    example).
    """
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    # Ana sent her 100 away; Bruno received 100 then spent 80 elsewhere.
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=20
    )
    original = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig"
    )
    database.transfers[original.transfer_id] = original
    _seed(database, ana, bruno)
    use_case = _use_case(database)

    reversal = await use_case.execute(_request(transfer_id=original.transfer_id))

    assert database.accounts[bruno.account_id].balance == Money(-80, USD)  # type: ignore[union-attr]
    assert database.accounts[ana.account_id].balance == Money(100, USD)  # type: ignore[union-attr]
    assert len(original.entries) + len(reversal.entries) == 4


async def test_reversing_a_deposit_never_locks_the_system_leg() -> None:
    """R6: a deposit's reversal still crosses a `SYSTEM` leg -- must not be

    special-cased differently from a customer-to-customer reversal.
    """
    database = _Database()
    owner_b = OwnerId(uuid4())
    funding = _account(
        owner_id=OwnerId(uuid4()),
        account_type=AccountType.SYSTEM,
        purpose=AccountPurpose.FUNDING,
        balance=0,
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=500,
    )
    original = _posted_transfer(
        source=funding, destination=bruno, amount=500, requested_by=owner_b, key="deposit"
    )
    database.transfers[original.transfer_id] = original
    _seed(database, funding, bruno)
    use_case = _use_case(database)

    reversal = await use_case.execute(_request(transfer_id=original.transfer_id))

    assert reversal.source_account_id == bruno.account_id
    assert reversal.destination_account_id == funding.account_id
    assert database.accounts[bruno.account_id].balance == Money(0, USD)  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# R7 -- reversing a nonexistent transfer
# --------------------------------------------------------------------------


async def test_reversing_a_nonexistent_transfer_is_rejected() -> None:
    database = _Database()
    use_case = _use_case(database)

    with pytest.raises(TransferNotFoundError):
        await use_case.execute(_request(transfer_id=TransferId(uuid4())))


# --------------------------------------------------------------------------
# R4 -- at most one reversal per transfer
# --------------------------------------------------------------------------


async def test_a_second_reversal_of_the_same_transfer_is_rejected() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=100,
    )
    original = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig"
    )
    already_reversed = _posted_transfer(
        source=bruno,
        destination=ana,
        amount=100,
        requested_by=OwnerId(uuid4()),
        key="first-reversal",
        reverses=original.transfer_id,
    )
    database.transfers[original.transfer_id] = original
    database.transfers[already_reversed.transfer_id] = already_reversed
    _seed(database, ana, bruno)
    use_case = _use_case(database)

    with pytest.raises(TransferAlreadyReversedError):
        await use_case.execute(
            _request(
                transfer_id=original.transfer_id,
                key="second-attempt",
            )
        )

    # No second reversal was posted.
    assert len(database.transfers) == 2


async def test_reversing_a_reversal_succeeds() -> None:
    """A reversal of a reversal is not forbidden -- "at most one reversal"

    is scoped to each transfer individually, not to a chain.
    """
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    transfer_a = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="a"
    )
    transfer_b = _posted_transfer(
        source=bruno,
        destination=ana,
        amount=100,
        requested_by=OwnerId(uuid4()),
        key="b",
        reverses=transfer_a.transfer_id,
    )
    database.transfers[transfer_a.transfer_id] = transfer_a
    database.transfers[transfer_b.transfer_id] = transfer_b
    _seed(database, ana, bruno)
    use_case = _use_case(database)

    transfer_c = await use_case.execute(_request(transfer_id=transfer_b.transfer_id, key="c"))

    assert transfer_c.reverses == transfer_b.transfer_id
    assert len(database.transfers) == 3


# --------------------------------------------------------------------------
# R3 -- reuses the idempotency mechanism exactly (T3/T4)
# --------------------------------------------------------------------------


async def test_a_retried_reversal_with_the_same_key_replays_the_original() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=100,
    )
    original = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig"
    )
    database.transfers[original.transfer_id] = original
    _seed(database, ana, bruno)
    use_case = _use_case(database)
    request = _request(transfer_id=original.transfer_id, key="retry-me")

    first = await use_case.execute(request)
    second = await use_case.execute(request)

    assert second.transfer_id == first.transfer_id
    # Only one reversal exists (plus the original): the retry did not post again.
    assert len(database.transfers) == 2


async def test_the_same_key_with_a_different_transfer_is_a_conflict() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=100,
    )
    original_1 = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig-1"
    )
    original_2 = _posted_transfer(
        source=ana, destination=bruno, amount=50, requested_by=owner_a, key="orig-2"
    )
    database.transfers[original_1.transfer_id] = original_1
    database.transfers[original_2.transfer_id] = original_2
    _seed(database, ana, bruno)
    use_case = _use_case(database)

    await use_case.execute(_request(transfer_id=original_1.transfer_id, key="dup"))

    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(_request(transfer_id=original_2.transfer_id, key="dup"))

    # Only one reversal was posted -- the second attempt was rejected, not applied.
    assert len(database.transfers) == 3


def test_request_hash_is_stable_over_transfer_id_only() -> None:
    transfer_id = TransferId(uuid4())
    first = _request(transfer_id=transfer_id, executed_by=PrincipalId(uuid4()), key="a")
    second = _request(transfer_id=transfer_id, executed_by=PrincipalId(uuid4()), key="b")

    assert first.hash() == second.hash()


# --------------------------------------------------------------------------
# R1 -- authorization is a real, if simulated, check
# --------------------------------------------------------------------------


async def test_a_non_admin_principal_is_rejected() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    ana = _account(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING, balance=0
    )
    bruno = _account(
        owner_id=owner_b,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=100,
    )
    original = _posted_transfer(
        source=ana, destination=bruno, amount=100, requested_by=owner_a, key="orig"
    )
    database.transfers[original.transfer_id] = original
    _seed(database, ana, bruno)
    use_case = _use_case(database)
    stranger = PrincipalId(uuid4())

    with pytest.raises(UnauthorizedPrincipalError):
        await use_case.execute(_request(transfer_id=original.transfer_id, executed_by=stranger))

    # Rejected before anything was written -- no reversal, no idempotency record either.
    assert len(database.transfers) == 1
    assert len(database.idempotency) == 0
