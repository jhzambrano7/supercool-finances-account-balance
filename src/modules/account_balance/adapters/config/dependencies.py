from dependency_injector import containers, providers

from modules.account_balance.adapters.outbound.repositories.sql.account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.engine import (
    create_engine,
    create_session_factory,
)
from modules.account_balance.adapters.outbound.repositories.sql.unit_of_work import (
    SqlTransferUnitOfWork,
)
from modules.account_balance.application.use_cases.open_account import OpenAccountUseCase
from modules.account_balance.application.use_cases.transfer_money import TransferMoneyUseCase
from modules.shared.adapters.config.dependencies import SharedDependencies
from modules.shared.adapters.config.settings import Settings


class AccountBalanceContainer(containers.DeclarativeContainer):
    """Mirrors `SharedDependencies`'s shape (AO6): a flat `DeclarativeContainer`

    of `providers.Singleton`/`providers.Factory`. Composes
    `SharedDependencies.id_generator`/`.clock` rather than duplicating them.
    """

    settings = providers.Singleton(Settings)
    engine = providers.Singleton(create_engine, settings=settings)
    session_factory = providers.Singleton(create_session_factory, engine=engine)

    account_repository = providers.Factory(SqlAccountRepository, session_factory=session_factory)

    open_account_use_case = providers.Factory(
        OpenAccountUseCase,
        repository=account_repository,
        id_generator=SharedDependencies.id_generator,
    )

    # `.provider` delegation (dependency_injector): `transfer_money_use_case`
    # receives the *provider itself* as a callable, not one resolved
    # instance -- `TransferMoneyUseCase` calls it fresh each time it needs a
    # unit of work (T5's recovery path opens a second one after the first
    # rolls back).
    transfer_unit_of_work = providers.Factory(
        SqlTransferUnitOfWork, session_factory=session_factory
    )

    transfer_money_use_case = providers.Factory(
        TransferMoneyUseCase,
        unit_of_work_factory=transfer_unit_of_work.provider,
        id_generator=SharedDependencies.id_generator,
        clock=SharedDependencies.clock,
    )
