from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from modules.account_balance.adapters.config.dependencies import (
    build_account_balance_container,
)
from modules.account_balance.adapters.inbound.api.deposit_withdraw_routes import (
    router as deposit_withdraw_router,
)
from modules.account_balance.adapters.inbound.api.routes import router as account_balance_router
from modules.account_balance.adapters.inbound.api.transfer_routes import (
    router as transfer_router,
)
from modules.shared.adapters.config.dependencies import SharedDependencies
from modules.shared.adapters.inbound.api.health_routes import router as health_router

_WIRED_MODULES = [
    "modules.account_balance.adapters.inbound.api.routes",
    "modules.account_balance.adapters.inbound.api.transfer_routes",
    "modules.account_balance.adapters.inbound.api.deposit_withdraw_routes",
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Disposes `SharedDependencies.engine` (AO5, one per process) on shutdown.

    A Singleton is never torn down by `dependency_injector` itself -- nothing calls this on a
    plain shutdown signal (SIGTERM, an ECS Fargate task stopping) unless something does it
    explicitly. `httpx.ASGITransport`, which every integration test uses, does not drive the ASGI
    lifespan protocol at all, so this never runs under the test suite -- a real ASGI server
    (uvicorn) does.
    """
    yield
    await SharedDependencies.engine().dispose()


def create_app() -> FastAPI:
    """Composes the service's inbound HTTP surface.

    No app-composition entrypoint existed before this slice; this is the
    smallest one that wires a module's container and mounts its router.
    Each module keeps its own container (`AccountBalanceContainer`,
    mirroring `SharedDependencies`'s shape) — this function only assembles
    them, it does not define any dependency itself.
    """
    account_balance_container = build_account_balance_container()
    account_balance_container.wire(modules=_WIRED_MODULES)

    app = FastAPI(title="SuperCool Finances — Account Balance Service", lifespan=_lifespan)

    app.include_router(health_router)
    app.include_router(account_balance_router)
    app.include_router(transfer_router)
    app.include_router(deposit_withdraw_router)

    return app


app = create_app()
