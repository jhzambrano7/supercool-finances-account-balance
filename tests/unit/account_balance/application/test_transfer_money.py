"""Unit tests for `TransferMoney` against fake repositories.

Per openspec/specs/transfer/spec.md's Testing Strategy: authorization for all
four movement shapes (T1's table), idempotency replay and conflict, and the
idempotency race path (T5) -- everything that does not require a real
database transaction (locking itself is integration-only, per the spec's own
note).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRecordConflictError,
    IdempotencyRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountByOwnerAndPurposeAndCurrency,
    FindAccountCriteria,
)
from modules.account_balance.application.gateways.transfer_repository import TransferRepository
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.application.use_cases.transfer_money import (
    AccountNotFoundError,
    IdempotencyConflictError,
    SystemToSystemTransferNotAllowedError,
    TransferMoney,
    TransferMoneyRequest,
)
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    AccountType,
)
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.errors import AccountOwnershipError
from modules.account_balance.domain.identifiers import (
    AccountId,
    EntryId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
from modules.account_balance.domain.posting import Posting
from modules.account_balance.domain.transfer import Transfer
from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator
from modules.shared.domain.money import Currency, Money

USD = Currency("USD")


class _FakeClock(Clock):
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


@dataclass
class _Database:
    """The fakes' shared, already-committed backing store.

    Each `_FakeUnitOfWork` writes to its own *staged* dicts instead of these
    directly, merging them back here only on a clean exit -- the same
    read-committed isolation a real transaction gives: nothing this attempt
    writes is visible to anyone (including a concurrent "winner" the test
    injects directly into this store) until it commits, and a rollback
    simply discards the staged dicts without touching this store at all.
    """

    accounts: dict[AccountId, Account] = field(default_factory=dict)
    transfers: dict[TransferId, Transfer] = field(default_factory=dict)
    idempotency: dict[tuple[OwnerId, str], IdempotencyRecord] = field(default_factory=dict)
    force_idempotency_conflict_once: bool = False
    conflicting_winner: IdempotencyRecord | None = None


class _FakeAccountRepository(AccountRepository):
    def __init__(self, database: _Database, staged: dict[AccountId, Account]) -> None:
        self._database = database
        self._staged = staged

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByAccountId(account_id):
                return self._staged.get(account_id) or self._database.accounts.get(account_id)
            case FindAccountByOwnerAndPurposeAndCurrency(owner_id, purpose, currency):
                return next(
                    (
                        a
                        for a in {**self._database.accounts, **self._staged}.values()
                        if a.owner_id == owner_id
                        and a.purpose == purpose
                        and a.currency == currency
                    ),
                    None,
                )

    async def add(self, account: Account) -> None:
        raise NotImplementedError

    async def get_for_update(self, account_id: AccountId) -> Account | None:
        account = self._database.accounts.get(account_id)
        if account is None or not account.account_type.is_user():
            return None
        return account

    async def update(self, account: Account) -> None:
        self._staged[account.account_id] = account


class _FakeTransferRepository(TransferRepository):
    def __init__(self, database: _Database, staged: dict[TransferId, Transfer]) -> None:
        self._database = database
        self._staged = staged

    async def add(self, posting: Posting) -> None:
        self._staged[posting.transfer.transfer_id] = posting.transfer

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
        if self._database.force_idempotency_conflict_once:
            # Simulates a concurrent request committing the winning row
            # directly against the shared store, ahead of this attempt --
            # exactly what a real unique-constraint violation reports.
            self._database.force_idempotency_conflict_once = False
            self._database.idempotency[key] = self._database.conflicting_winner  # type: ignore[assignment]
            raise IdempotencyRecordConflictError(
                caller_id=record.caller_id, idempotency_key=record.idempotency_key
            )
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


def _use_case(database: _Database, *, occurred_at: datetime | None = None) -> TransferMoney:
    fixed = occurred_at or datetime(2026, 9, 7, 12, 0, tzinfo=__import__("datetime").UTC)
    return TransferMoney(
        unit_of_work_factory=_unit_of_work_factory(database),
        id_generator=IdGenerator(),
        clock=_FakeClock(fixed),
    )


def _open(
    *,
    owner_id: OwnerId,
    account_type: AccountType,
    purpose: AccountPurpose,
    balance: int = 0,
) -> Account:
    """`balance` reconstitutes a pre-funded account -- `Account.open()` always forces a zero
    balance, and a `USER` account debited by these tests needs enough on hand to satisfy I2. A
    `SYSTEM` account's balance is irrelevant to the use case (T7: never read from or written to
    this column), so it is left at zero unless a test says otherwise."""
    account_id = AccountId(uuid4())
    if balance == 0:
        return Account.open(
            account_id=account_id,
            owner_id=owner_id,
            account_type=account_type,
            purpose=purpose,
            currency=USD,
        )
    return Account.reconstitute(
        account_id=account_id,
        owner_id=owner_id,
        account_type=account_type,
        purpose=purpose,
        currency=USD,
        balance=Money(balance, USD),
        status=AccountStatus.ACTIVE,
        version=0,
    )


def _seed(database: _Database, *accounts: Account) -> None:
    for account in accounts:
        database.accounts[account.account_id] = account


def _request(
    *,
    source: Account,
    destination: Account,
    requested_by: OwnerId,
    key: str = "k1",
    amount: int = 500,
) -> TransferMoneyRequest:
    return TransferMoneyRequest(
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=Money(amount, USD),
        idempotency_key=IdempotencyKey(key),
        requested_by=requested_by,
    )


# --------------------------------------------------------------------------
# T1 -- authorization over the debited leg(s), all four movement shapes
# --------------------------------------------------------------------------


async def test_customer_to_customer_transfer_is_authorized_by_source_only() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)

    transfer = await use_case.execute(
        _request(source=source, destination=destination, requested_by=owner_a)
    )

    assert transfer.source_account_id == source.account_id
    assert transfer.destination_account_id == destination.account_id


async def test_deposit_is_authorized_by_destination_since_source_is_system() -> None:
    database = _Database()
    owner_b = OwnerId(uuid4())
    funding = _open(
        owner_id=OwnerId(uuid4()), account_type=AccountType.SYSTEM, purpose=AccountPurpose.FUNDING
    )
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, funding, destination)
    use_case = _use_case(database)

    transfer = await use_case.execute(
        _request(source=funding, destination=destination, requested_by=owner_b)
    )

    assert transfer.destination_account_id == destination.account_id


async def test_withdraw_is_authorized_by_source_which_is_the_only_user_leg() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    settlement = _open(
        owner_id=OwnerId(uuid4()),
        account_type=AccountType.SYSTEM,
        purpose=AccountPurpose.SETTLEMENT,
    )
    _seed(database, source, settlement)
    use_case = _use_case(database)

    transfer = await use_case.execute(
        _request(source=source, destination=settlement, requested_by=owner_a)
    )

    assert transfer.source_account_id == source.account_id


async def test_a_transfer_debiting_an_account_the_caller_does_not_own_is_rejected() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    stranger = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    destination = _open(
        owner_id=OwnerId(uuid4()), account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)

    with pytest.raises(AccountOwnershipError):
        await use_case.execute(
            _request(source=source, destination=destination, requested_by=stranger)
        )

    assert database.transfers == {}


async def test_a_system_to_system_movement_is_rejected() -> None:
    database = _Database()
    funding = _open(
        owner_id=OwnerId(uuid4()), account_type=AccountType.SYSTEM, purpose=AccountPurpose.FUNDING
    )
    settlement = _open(
        owner_id=OwnerId(uuid4()),
        account_type=AccountType.SYSTEM,
        purpose=AccountPurpose.SETTLEMENT,
    )
    _seed(database, funding, settlement)
    use_case = _use_case(database)

    with pytest.raises(SystemToSystemTransferNotAllowedError):
        await use_case.execute(
            _request(source=funding, destination=settlement, requested_by=OwnerId(uuid4()))
        )


async def test_an_unknown_account_id_is_rejected() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    _seed(database, source)
    use_case = _use_case(database)
    ghost = _open(
        owner_id=OwnerId(uuid4()), account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )

    with pytest.raises(AccountNotFoundError):
        await use_case.execute(_request(source=source, destination=ghost, requested_by=owner_a))


# --------------------------------------------------------------------------
# T3/T4 -- idempotent replay and payload conflict
# --------------------------------------------------------------------------


async def test_a_retried_transfer_with_the_same_body_replays_the_original() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)
    request = _request(source=source, destination=destination, requested_by=owner_a)

    first = await use_case.execute(request)
    second = await use_case.execute(request)

    assert second.transfer_id == first.transfer_id
    assert len(database.transfers) == 1


async def test_the_same_key_with_a_different_amount_is_a_conflict() -> None:
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)
    first_request = _request(
        source=source, destination=destination, requested_by=owner_a, key="dup", amount=500
    )
    second_request = _request(
        source=source, destination=destination, requested_by=owner_a, key="dup", amount=999
    )

    await use_case.execute(first_request)

    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(second_request)

    assert len(database.transfers) == 1


def _build_winner_transfer(
    *, source: Account, destination: Account, amount: Money, requested_by: OwnerId, key: str
) -> Transfer:
    """A `Transfer` built directly (not through `posting.transfer()`, which needs live `Account`
    instances) to stand in for a concurrent request's already-committed result -- same body as
    the loser's own request, but a transfer_id the loser never generated itself."""
    transfer_id = TransferId(uuid4())
    debit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=source.account_id,
        direction=EntryDirection.DEBIT,
        amount=amount,
        occurred_at=datetime(2026, 9, 7, 12, 0, tzinfo=__import__("datetime").UTC),
    )
    credit = Entry(
        entry_id=EntryId(uuid4()),
        transfer_id=transfer_id,
        account_id=destination.account_id,
        direction=EntryDirection.CREDIT,
        amount=amount,
        occurred_at=debit.occurred_at,
    )
    return Transfer(
        transfer_id=transfer_id,
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=amount,
        requested_by=requested_by,
        idempotency_key=IdempotencyKey(key),
        occurred_at=debit.occurred_at,
        entries=(debit, credit),
    )


async def test_two_concurrent_requests_with_the_same_key_never_both_apply() -> None:
    """T5: the fake simulates the race by making the *first* `add()` on the idempotency repository
    behave as though a concurrent request already committed the winning row directly against the
    shared store -- the use case must catch that, not error, and return the winner's result
    rather than its own."""
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        balance=10_000,
    )
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)
    amount = Money(500, USD)
    winner = _build_winner_transfer(
        source=source, destination=destination, amount=amount, requested_by=owner_a, key="race"
    )
    database.transfers[winner.transfer_id] = winner
    request = _request(
        source=source, destination=destination, requested_by=owner_a, key="race", amount=500
    )
    database.conflicting_winner = IdempotencyRecord(
        caller_id=owner_a,
        idempotency_key=IdempotencyKey("race"),
        request_hash=request.hash(),
        transfer_id=winner.transfer_id,
        status="COMPLETED",
        created_at=winner.occurred_at,
    )
    database.force_idempotency_conflict_once = True

    result = await use_case.execute(request)

    assert result.transfer_id == winner.transfer_id
    # The loser's own attempt must not have posted a second transfer: only
    # the winner's row (seeded ahead of time, simulating its commit) exists.
    assert len(database.transfers) == 1


async def test_a_losing_concurrent_request_replays_even_when_underfunded() -> None:
    """T5's harder case, found by an independent review: the account has
    exactly enough balance for *one* application, not two. `source`'s
    balance here is seeded at 0 -- what it already is *after* the winner's
    debit -- standing in for the state a real loser's `get_for_update` would
    actually observe under real concurrency. The loser must never reach that
    read at all: the idempotency reservation has to be attempted, and lose,
    before any account is touched. Before the fix this raised
    `InsufficientFundsError` instead of replaying the winner.
    """
    database = _Database()
    owner_a = OwnerId(uuid4())
    owner_b = OwnerId(uuid4())
    source = _open(
        owner_id=owner_a, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )  # balance=0, as if the winner's 500-debit already landed
    destination = _open(
        owner_id=owner_b, account_type=AccountType.USER, purpose=AccountPurpose.CHECKING
    )
    _seed(database, source, destination)
    use_case = _use_case(database)
    amount = Money(500, USD)
    winner = _build_winner_transfer(
        source=source, destination=destination, amount=amount, requested_by=owner_a, key="race"
    )
    database.transfers[winner.transfer_id] = winner
    request = _request(
        source=source, destination=destination, requested_by=owner_a, key="race", amount=500
    )
    database.conflicting_winner = IdempotencyRecord(
        caller_id=owner_a,
        idempotency_key=IdempotencyKey("race"),
        request_hash=request.hash(),
        transfer_id=winner.transfer_id,
        status="COMPLETED",
        created_at=winner.occurred_at,
    )
    database.force_idempotency_conflict_once = True

    result = await use_case.execute(request)

    assert result.transfer_id == winner.transfer_id
    assert len(database.transfers) == 1
