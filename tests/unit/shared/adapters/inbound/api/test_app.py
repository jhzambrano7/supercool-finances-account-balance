"""Unit test for `create_app`'s lifespan -- disposes the process-wide engine on shutdown.

Not an integration test: `httpx.ASGITransport` (used by every integration test's `client`
fixture) does not drive the ASGI lifespan protocol at all, so this behavior is only ever
exercised here, by calling `_lifespan` directly.
"""

from unittest.mock import AsyncMock

from dependency_injector import providers
from fastapi import FastAPI

from modules.shared.adapters.config.dependencies import SharedDependencies
from modules.shared.adapters.inbound.api.app import _lifespan


async def test_lifespan_disposes_the_shared_engine_on_shutdown() -> None:
    fake_engine = AsyncMock()
    SharedDependencies.engine.override(providers.Object(fake_engine))
    try:
        async with _lifespan(FastAPI()):
            fake_engine.dispose.assert_not_awaited()
        fake_engine.dispose.assert_awaited_once()
    finally:
        SharedDependencies.engine.reset_override()
