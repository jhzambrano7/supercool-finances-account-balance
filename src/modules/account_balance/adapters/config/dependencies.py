import logging

from dependency_injector import containers, providers

from modules.account_balance.adapters.outbound.repositories.sql.sql_account_repository import (
    SqlAccountRepository,
)
from modules.account_balance.adapters.outbound.repositories.sql.unit_of_work import (
    SqlTransferUnitOfWork,
)
from modules.account_balance.application.use_cases.account_register import AccountRegister
from modules.account_balance.application.use_cases.transfer_money import TransferMoney
from modules.shared.adapters.config.dependencies import SharedDependencies


class AccountBalanceContainer(containers.DeclarativeContainer):
    # `DependenciesContainer` avoids the deep-copy fork a plain
    # `providers.Container` reference would cause: it proxies to whatever
    # container `.override(SharedDependencies)` points it at, instead of
    # copying `SharedDependencies`'s provider graph. It cannot carry a
    # working default -- pre-filling it with named providers at class-
    # definition time (e.g. `session_factory=SharedDependencies.session_
    # factory`) gets deep-copied away by this container's own instantiation
    # just the same (verified empirically). Every real construction site
    # must therefore call `.shared.override(SharedDependencies)` itself --
    # use `build_account_balance_container()` below rather than
    # instantiating this class directly, so that call lives in one place.
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
        unit_of_work_factory=transfer_unit_of_work.provider,
        id_generator=shared.id_generator,
        clock=shared.clock,
    )


def build_account_balance_container() -> AccountBalanceContainer:
    """The one correct way to construct `AccountBalanceContainer` -- every entrypoint (the HTTP
    app, a future cron job or worker) should call this instead of `AccountBalanceContainer()`
    directly, so pairing it with `SharedDependencies` can't be forgotten at a second call site.
    """
    container = AccountBalanceContainer()
    container.shared.override(SharedDependencies)
    return container
