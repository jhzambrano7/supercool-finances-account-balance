from dependency_injector import containers, providers

from modules.shared.application.services.id_generator import IdGenerator


class SharedDependencies(containers.DeclarativeContainer):
    id_generator = providers.Singleton(IdGenerator)
