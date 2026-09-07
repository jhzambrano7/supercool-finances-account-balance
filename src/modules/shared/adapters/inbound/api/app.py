from fastapi import FastAPI

from modules.account_balance.adapters.config.dependencies import AccountBalanceContainer
from modules.account_balance.adapters.inbound.api.routes import router as account_balance_router
from modules.account_balance.adapters.inbound.api.transfer_routes import (
    router as transfer_router,
)

_WIRED_MODULES = [
    "modules.account_balance.adapters.inbound.api.routes",
    "modules.account_balance.adapters.inbound.api.transfer_routes",
]


def create_app() -> FastAPI:
    """Composes the service's inbound HTTP surface.

    No app-composition entrypoint existed before this slice; this is the
    smallest one that wires a module's container and mounts its router.
    Each module keeps its own container (`AccountBalanceContainer`,
    mirroring `SharedDependencies`'s shape) — this function only assembles
    them, it does not define any dependency itself.
    """
    account_balance_container = AccountBalanceContainer()
    account_balance_container.wire(modules=_WIRED_MODULES)

    app = FastAPI(title="SuperCool Finances — Account Balance Service")
    # Kept as an attribute so tests can override providers (e.g. `settings`)
    # against a real database before the app is exercised.
    app.container = account_balance_container  # type: ignore[attr-defined]
    app.include_router(account_balance_router)
    app.include_router(transfer_router)
    return app


app = create_app()
