from dependency_injector import containers, providers

from modules.account_balance.adapters.outbound.repositories.sql.account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.engine import (
    create_engine,
    create_session_factory,
)
from modules.account_balance.application.use_cases.open_account import OpenAccountUseCase
from modules.shared.adapters.config.dependencies import SharedDependencies
from modules.shared.adapters.config.settings import Settings


class AccountBalanceContainer(containers.DeclarativeContainer):
    """Mirrors `SharedDependencies`'s shape (AO6): a flat `DeclarativeContainer`

    of `providers.Singleton`/`providers.Factory`. Composes
    `SharedDependencies.id_generator` rather than duplicating it.
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
