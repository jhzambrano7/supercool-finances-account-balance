import logging

from dependency_injector import containers, providers

from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.shared.adapters.config.dependencies import SharedDependencies


class AccountBalanceContainer(containers.DeclarativeContainer):
    logger = providers.Singleton(logging.getLogger, "modules.account_balance.adapters.sql")

    account_repository = providers.Factory(
        provides=SqlAccountRepository,
        logger=logger,
        session_factory=SharedDependencies.session_factory,
    )

    account_register = providers.Factory(
        provides=AccountRegister,
        repository=account_repository,
        id_generator=SharedDependencies.id_generator,
    )
