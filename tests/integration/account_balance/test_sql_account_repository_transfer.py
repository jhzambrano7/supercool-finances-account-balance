"""Integration tests for the transfer-specific `AccountRepository` behaviour (T6, T7, T9) against a
real PostgreSQL: `get_for_update`, `update`, and a `SYSTEM` account's balance being computed from
its entries rather than read from `balance_amount`."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.account_balance.adapters.config.seeded_accounts import FUNDING_ACCOUNT_ID
from modules.account_balance.adapters.outbound.repositories.sql.dbos.models import (
    EntryDbo,
    TransferDbo,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_unit_of_work import (
    SqlTransferUnitOfWork,
)
from modules.account_balance.application.gateways.models.find_accounts_criteria import (
    FindAccountByAccountId,
)
from modules.account_balance.domain.account import Account, AccountPurpose, AccountType
from modules.account_balance.domain.entry import Entry, EntryDirection
from modules.account_balance.domain.identifiers import AccountId, EntryId, OwnerId, TransferId
from modules.shared.domain.money import Currency, Money

pytestmark = pytest.mark.integration

USD = Currency("USD")
_logger = logging.getLogger(__name__)


def _repository(session_factory: Callable[[], AsyncSession]) -> SqlAccountRepository:
    return SqlAccountRepository(_logger, session_factory)


def _open_user_account(*, owner_id: OwnerId) -> Account:
    return Account.open(
        account_id=AccountId(uuid4()),
        owner_id=owner_id,
        account_type=AccountType.USER,
        purpose=AccountPurpose.CHECKING,
        currency=USD,
    )


async def _post_funding_entry(
    session_factory: Callable[[], AsyncSession],
    *,
    account_id: AccountId,
    direction: str,
    amount: int,
) -> None:
    """Writes a bare `transfers`/`entries` row pair directly -- this test only needs a `SYSTEM`
    account to have entries against it, not a fully posted, balanced `Transfer` (that is
    `TransferMoney`'s own concern, exercised by `test_transfer_route.py`)."""
    transfer_id = uuid4()
    async with session_factory() as session:
        await session.execute(
            insert(TransferDbo).values(
                transfer_id=transfer_id,
                source_account_id=account_id.value,
                destination_account_id=account_id.value,
                amount=amount,
                currency="USD",
                requested_by=account_id.value,
                idempotency_key=str(uuid4()),
                occurred_at=datetime.now(UTC),
                reverses=None,
            )
        )
        await session.execute(
            insert(EntryDbo).values(
                entry_id=uuid4(),
                transfer_id=transfer_id,
                account_id=account_id.value,
                direction=direction,
                amount=amount,
                occurred_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def test_get_computes_system_balance_from_entries_not_the_stored_column(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)
    account_id = AccountId(FUNDING_ACCOUNT_ID)

    await _post_funding_entry(session_factory, account_id=account_id, direction="DEBIT", amount=100)
    await _post_funding_entry(session_factory, account_id=account_id, direction="DEBIT", amount=200)

    funding = await repository.find(criteria=FindAccountByAccountId(account_id))

    assert funding is not None
    assert funding.account_type.is_system()
    # Two debits totaling 300 against the funding account (its own
    # perspective as the source of every deposit, spec's own worked example)
    # -- -300, never whatever the seeded, forever-zero `balance_amount`
    # column holds.
    assert funding.balance.amount == -300


async def test_get_for_update_locks_a_user_account(
    session_factory: Callable[[], AsyncSession],
) -> None:
    repository = _repository(session_factory)
    account = _open_user_account(owner_id=OwnerId(uuid4()))
    await repository.add(account)

    locked = await repository.get_for_update(account.account_id)

    assert locked is not None
    assert locked.account_id == account.account_id


async def test_get_for_update_never_returns_a_system_account(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """T6: a `SYSTEM` account is never locked -- `get_for_update` must not hand one back even though
    the row exists."""
    repository = _repository(session_factory)

    locked = await repository.get_for_update(AccountId(FUNDING_ACCOUNT_ID))

    assert locked is None


async def test_update_persists_a_user_accounts_new_balance_and_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`update()` deliberately does not commit on its own (T3: the enclosing `TransferUnitOfWork`
    commits once, atomically) -- so this test goes through `SqlTransferUnitOfWork`, the only way
    `update()` is ever really used, rather than calling it against a bare, per-call session that
    would roll back before anything could be observed."""
    account_repository = _repository(session_factory)
    account = _open_user_account(owner_id=OwnerId(uuid4()))
    await account_repository.add(account)

    unit_of_work = SqlTransferUnitOfWork(_logger, session_factory)
    async with unit_of_work as uow:
        locked = await uow.accounts.get_for_update(account.account_id)
        assert locked is not None

        credited = locked.credit(
            Entry(
                entry_id=EntryId(uuid4()),
                transfer_id=TransferId(uuid4()),
                account_id=account.account_id,
                direction=EntryDirection.CREDIT,
                amount=Money(750, USD),
                occurred_at=datetime.now(UTC),
            )
        )
        await uow.accounts.update(credited)

    reloaded = await account_repository.find(criteria=FindAccountByAccountId(account.account_id))
    assert reloaded is not None
    assert reloaded.balance.amount == 750
    assert reloaded.version == 1
