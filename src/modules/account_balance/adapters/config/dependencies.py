import logging

from dependency_injector import containers, providers

from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.use_cases.open_account import OpenAccountUseCase
from modules.shared.adapters.config.dependencies import SharedDependencies


class AccountBalanceContainer(containers.DeclarativeContainer):
    """Mirrors `SharedDependencies`'s shape (AO6): a flat `DeclarativeContainer`

    of `providers.Singleton`/`providers.Factory`. Composes
    `SharedDependencies.id_generator`/`session_factory` rather than
    duplicating them — settings, the engine and the session factory are
    process-wide, not owned by this module.
    """

    logger = providers.Singleton(logging.getLogger, "modules.account_balance.adapters.sql")

    account_repository = providers.Factory(
        provides=SqlAccountRepository,
        logger=logger,
        session_factory=SharedDependencies.session_factory,
    )

    open_account_use_case = providers.Factory(
        provides=OpenAccountUseCase,
        repository=account_repository,
        id_generator=SharedDependencies.id_generator,
    )
