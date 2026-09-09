import logging

from dependency_injector import containers, providers

from modules.account_balance.adapters.config.fixed_admin_authorization_gateway import (
    FixedAdminAuthorizationGateway,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_account_unit_of_work import (
    SqlAccountUnitOfWork,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_collections_repository import (
    SqlCollectionsRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_movement_repository import (
    SqlMovementRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.sql_unit_of_work import (
    SqlTransferUnitOfWork,
)
from modules.account_balance.application.services.system_account_resolver import (
    SystemAccountResolver,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.account_balance.application.use_cases.close_account import CloseAccount
from modules.account_balance.application.use_cases.deposit import Deposit
from modules.account_balance.application.use_cases.get_account import GetAccount
from modules.account_balance.application.use_cases.get_collections_report import (
    GetCollectionsReport,
)
from modules.account_balance.application.use_cases.list_accounts import ListAccounts
from modules.account_balance.application.use_cases.list_movements import ListMovements
from modules.account_balance.application.use_cases.revert_transfer import RevertTransfer
from modules.account_balance.application.use_cases.transfer_money import TransferMoney
from modules.account_balance.application.use_cases.withdraw import Withdraw
from modules.shared.adapters.config.dependencies import SharedDependencies


class AccountBalanceContainer(containers.DeclarativeContainer):
    shared: SharedDependencies = providers.DependenciesContainer()  # type: ignore[assignment]

    logger = providers.Singleton(logging.getLogger, "modules.account_balance")

    account_repository = providers.Factory(
        provides=SqlAccountRepository,
        logger=logger,
        session_factory=shared.session_factory,
    )

    account_register = providers.Factory(
        provides=AccountRegister,
        repository=account_repository,
        id_generator=shared.id_generator,
    )

    get_account = providers.Factory(
        provides=GetAccount,
        repository=account_repository,
        logger=logger,
    )

    list_accounts = providers.Factory(
        provides=ListAccounts,
        repository=account_repository,
    )

    account_unit_of_work = providers.Factory(
        provides=SqlAccountUnitOfWork,
        logger=logger,
        session_factory=shared.session_factory,
    )

    # `.provider` delegation, same reasoning as `transfer_money` below:
    # `CloseAccount` calls the factory fresh, it does not hold one resolved
    # unit of work across calls.
    close_account = providers.Factory(
        provides=CloseAccount,
        logger=logger,
        unit_of_work_factory=account_unit_of_work.provider,
    )

    # A fresh, independently-committed session per call (like account_repository above), not the
    # transactional unit-of-work session: reading movements is not part of any write transaction.
    movement_repository = providers.Factory(
        provides=SqlMovementRepository,
        logger=logger,
        session_factory=shared.session_factory,
    )

    list_movements = providers.Factory(
        provides=ListMovements,
        account_repository=account_repository,
        movement_repository=movement_repository,
        logger=logger,
    )

    # Same reasoning as movement_repository above: a read, not part of any write transaction, so
    # a fresh independently-committed session per call is enough.
    collections_repository = providers.Factory(
        provides=SqlCollectionsRepository,
        logger=logger,
        session_factory=shared.session_factory,
    )

    transfer_unit_of_work = providers.Factory(
        provides=SqlTransferUnitOfWork,
        logger=logger,
        session_factory=shared.session_factory,
    )

    # `.provider` delegation (dependency_injector): `transfer_money`
    # receives the *provider itself* as a callable, not one resolved
    # instance -- `TransferMoney` calls it fresh each time it needs a
    # unit of work (T5's recovery path opens a second one after the first
    # rolls back).
    transfer_money = providers.Factory(
        provides=TransferMoney,
        logger=logger,
        unit_of_work_factory=transfer_unit_of_work.provider,
        id_generator=shared.id_generator,
        clock=shared.clock,
    )

    # Reads through account_repository (unlocked, matching how transfer_money
    # itself reads a SYSTEM leg, T7) rather than through transfer_unit_of_work's
    # own accounts repository, since resolving the platform's FUNDING/SETTLEMENT
    # account is not part of the transfer's own transaction.
    system_account_resolver = providers.Factory(
        provides=SystemAccountResolver,
        repository=account_repository,
        logger=logger,
    )

    # Deposit/Withdraw are thin wrappers over transfer_money (openspec/specs/
    # transfer/spec.md, "The design, settled").
    deposit = providers.Factory(
        provides=Deposit,
        system_account_resolver=system_account_resolver,
        transfer_money=transfer_money,
    )

    withdraw = providers.Factory(
        provides=Withdraw,
        system_account_resolver=system_account_resolver,
        transfer_money=transfer_money,
    )

    # Stateless (a fixed-constant comparison, R1) -- a Singleton, same reasoning as `logger`.
    authorization_gateway = providers.Singleton(FixedAdminAuthorizationGateway, logger=logger)

    # Shares `transfer_unit_of_work`'s provider (R3: same unit of work, same
    # three repositories, same idempotency mechanism as `transfer` -- no
    # parallel infrastructure for this slice).
    revert_transfer = providers.Factory(
        provides=RevertTransfer,
        logger=logger,
        unit_of_work_factory=transfer_unit_of_work.provider,
        id_generator=shared.id_generator,
        clock=shared.clock,
        authorization_gateway=authorization_gateway,
    )

    # Shares `authorization_gateway` (R1's own singleton) rather than standing up a second
    # authorization mechanism -- a collections list is operator-only for exactly the reason a
    # reversal is (PRD §7.1).
    get_collections_report = providers.Factory(
        provides=GetCollectionsReport,
        collections_repository=collections_repository,
        authorization_gateway=authorization_gateway,
        clock=shared.clock,
        logger=logger,
    )


def build_account_balance_container() -> AccountBalanceContainer:
    """The one correct way to construct `AccountBalanceContainer` -- every entrypoint (the HTTP
    app, a future cron job or worker) should call this instead of `AccountBalanceContainer()`
    directly, so pairing it with `SharedDependencies` can't be forgotten at a second call site.
    """
    container = AccountBalanceContainer()
    container.shared.override(SharedDependencies)
    return container
