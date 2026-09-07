"""Integration test for `POST /accounts` against a real PostgreSQL."""

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from modules.shared.adapters.config.settings import Settings
from modules.shared.adapters.inbound.api.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(postgres_url: str) -> Iterator[FastAPI]:
    application = create_app()
    container = application.container  # type: ignore[attr-defined]
    container.settings.override(providers.Object(Settings(database_url=postgres_url)))
    yield application
    container.settings.reset_override()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_first_open_returns_201_created(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "CHECKING", "currency": "USD"}

    response = await client.post("/accounts", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == payload["owner_id"]
    assert body["purpose"] == "CHECKING"
    assert body["currency"] == "USD"
    assert body["balance"] == 0
    assert body["status"] == "ACTIVE"


async def test_retry_returns_200_with_the_same_account(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "SAVINGS", "currency": "USD"}

    first = await client.post("/accounts", json=payload)
    second = await client.post("/accounts", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["account_id"] == first.json()["account_id"]


async def test_a_system_only_purpose_is_rejected(client: AsyncClient) -> None:
    payload = {"owner_id": str(uuid4()), "purpose": "FUNDING", "currency": "USD"}

    response = await client.post("/accounts", json=payload)

    assert response.status_code == 422
