from dependency_injector import containers, providers

from modules.shared.adapters.config.settings import Settings
from modules.shared.adapters.outbound.repositories.sql.engine import (
    create_engine,
    create_session_factory,
)
from modules.shared.application.services.id_generator import IdGenerator


class SharedDependencies(containers.DeclarativeContainer):
    """Cross-module singletons — every module composes these rather than
    each defining its own settings/engine/session factory, which would
    otherwise mean one Postgres connection pool per module in a single
    process.
    """

    id_generator = providers.Singleton(IdGenerator)
    settings = providers.Singleton(Settings)
    engine = providers.Singleton(create_engine, settings=settings)
    session_factory = providers.Singleton(create_session_factory, engine=engine)
