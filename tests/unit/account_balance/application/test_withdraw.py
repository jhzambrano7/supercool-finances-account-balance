"""Unit tests for `Withdraw` -- a thin wrapper over `TransferMoney`
(openspec/specs/transfer/spec.md, "The design, settled"). Locking, idempotency and authorization
are `TransferMoney`'s own concerns, already covered by `test_transfer_money.py`; these tests cover
only what `Withdraw` adds: resolving the platform's `SETTLEMENT` account for the requested
currency, and refusing when none is seeded.
"""

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from modules.account_balance.application.gateways.account_repository import AccountRepository
from modules.account_balance.application.gateways.idempotency_repository import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
    FindAccountCriteria,
    FindSystemAccountByPurposeAndCurrency,
)
from modules.account_balance.application.gateways.transfer_repository import TransferRepository
from modules.account_balance.application.gateways.unit_of_work import TransferUnitOfWork
from modules.account_balance.application.services.system_account_resolver import (
    CurrencyNotOperationalError,
    SystemAccountResolver,
)
from modules.account_balance.application.use_cases.transfer_money import TransferMoney
from modules.account_balance.application.use_cases.withdraw import Withdraw, WithdrawRequest
from modules.account_balance.domain.account import (
    Account,
    AccountPurpose,
    AccountStatus,
    SystemAccount,
    UserAccount,
)
from modules.account_balance.domain.identifiers import (
    AccountId,
    IdempotencyKey,
    OwnerId,
    TransferId,
)
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


class _Database:
    def __init__(self) -> None:
        self.accounts: dict[AccountId, Account] = {}
        self.transfers: dict[TransferId, Transfer] = {}
        self.idempotency: dict[tuple[OwnerId, str], IdempotencyRecord] = {}


class _FakeAccountRepository(AccountRepository):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def find(self, *, criteria: FindAccountCriteria) -> Account | None:
        match criteria:
            case FindAccountByAccountId(account_id):
                return self._database.accounts.get(account_id)
            case FindSystemAccountByPurposeAndCurrency(purpose, currency):
                return next(
                    (
                        a
                        for a in self._database.accounts.values()
                        if isinstance(a, SystemAccount)
                        and a.purpose == purpose
                        and a.currency == currency
                    ),
                    None,
                )
        raise NotImplementedError

    async def add(self, account: Account) -> None:
        raise NotImplementedError

    async def get_for_update(self, account_id: AccountId) -> UserAccount | None:
        account = self._database.accounts.get(account_id)
        return account if isinstance(account, UserAccount) else None

    async def get_many_for_update(
        self, account_ids: tuple[AccountId, ...]
    ) -> tuple[UserAccount, ...]:
        return tuple(
            account
            for account_id in sorted(account_ids)
            if isinstance(account := self._database.accounts.get(account_id), UserAccount)
        )

    async def update(self, account: UserAccount) -> None:
        self._database.accounts[account.account_id] = account


class _FakeTransferRepository(TransferRepository):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def add(self, transfer: Transfer) -> None:
        self._database.transfers[transfer.transfer_id] = transfer

    async def get(self, transfer_id: TransferId) -> Transfer | None:
        return self._database.transfers.get(transfer_id)


class _FakeIdempotencyRepository(IdempotencyRepository):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def find_by_key(
        self, *, caller_id: OwnerId, idempotency_key: IdempotencyKey
    ) -> IdempotencyRecord | None:
        return self._database.idempotency.get((caller_id, idempotency_key.value))

    async def add(self, record: IdempotencyRecord) -> None:
        self._database.idempotency[(record.caller_id, record.idempotency_key.value)] = record


class _FakeUnitOfWork(TransferUnitOfWork):
    def __init__(self, database: _Database) -> None:
        self._database = database

    async def __aenter__(self) -> Self:
        self.accounts = _FakeAccountRepository(self._database)
        self.transfers = _FakeTransferRepository(self._database)
        self.idempotency = _FakeIdempotencyRepository(self._database)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _transfer_money(database: _Database) -> TransferMoney:
    fixed = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    return TransferMoney(
        logger=logging.getLogger(__name__),
        unit_of_work_factory=lambda: _FakeUnitOfWork(database),
        id_generator=IdGenerator(),
        clock=_FakeClock(fixed),
    )


def _withdraw(database: _Database) -> Withdraw:
    return Withdraw(
        system_account_resolver=SystemAccountResolver(
            repository=_FakeAccountRepository(database),
            logger=logging.getLogger(__name__),
        ),
        transfer_money=_transfer_money(database),
    )


def _funded_user(owner_id: OwnerId, *, balance: int = 10_000) -> UserAccount:
    return UserAccount.reconstitute(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
        balance=Money(balance, USD),
        status=AccountStatus.ACTIVE,
        version=0,
    )


def _settlement_account() -> SystemAccount:
    return SystemAccount(
        account_id=AccountId(uuid4()),
        owner_id=OwnerId(uuid4()),
        purpose=AccountPurpose.SETTLEMENT,
        currency=USD,
    )


async def test_withdraw_resolves_the_settlement_account_and_delegates_to_transfer_money() -> None:
    database = _Database()
    owner = OwnerId(uuid4())
    source = _funded_user(owner)
    settlement = _settlement_account()
    database.accounts[source.account_id] = source
    database.accounts[settlement.account_id] = settlement
    use_case = _withdraw(database)

    transfer = await use_case.execute(
        WithdrawRequest(
            source_account_id=source.account_id,
            amount=Money(2_500, USD),
            idempotency_key=IdempotencyKey(str(uuid4())),
            requested_by=owner,
        )
    )

    assert transfer.source_account_id == source.account_id
    assert transfer.destination_account_id == settlement.account_id
    assert transfer.amount == Money(2_500, USD)
    assert len(database.transfers) == 1


async def test_withdraw_raises_when_no_settlement_account_is_seeded_for_the_currency() -> None:
    database = _Database()
    owner = OwnerId(uuid4())
    source = _funded_user(owner)
    database.accounts[source.account_id] = source
    use_case = _withdraw(database)

    with pytest.raises(CurrencyNotOperationalError):
        await use_case.execute(
            WithdrawRequest(
                source_account_id=source.account_id,
                amount=Money(2_500, USD),
                idempotency_key=IdempotencyKey(str(uuid4())),
                requested_by=owner,
            )
        )

    assert database.transfers == {}
