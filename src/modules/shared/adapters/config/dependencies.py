from dependency_injector import containers, providers

from modules.shared.application.services.clock import Clock
from modules.shared.application.services.id_generator import IdGenerator


class SharedDependencies(containers.DeclarativeContainer):
    id_generator = providers.Singleton(IdGenerator)
    clock = providers.Singleton(Clock)
