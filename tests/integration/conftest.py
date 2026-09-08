"""Fixtures for account_balance integration tests — a real PostgreSQL via testcontainers.

openspec/specs/account-opening/spec.md, Testing Strategy: "the SQL
AccountRepository adapter and the POST /accounts route against a real
PostgreSQL ... run via docker compose" — testcontainers provisions its own
PostgreSQL so no developer has to remember to start one (README, "Running it
and deploying it").
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from modules.shared.adapters.config.dependencies import SharedDependencies
from modules.shared.adapters.config.settings import Settings
from modules.shared.adapters.inbound.api.app import create_app

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_migrations(database_url: str) -> None:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    # `attributes`, not `set_main_option`: `env.py` checks this first so a
    # `DATABASE_URL` in the developer's own shell can never shadow the
    # testcontainers URL these migrations must actually run against.
    config.attributes["sqlalchemy.url"] = database_url
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        _run_migrations(url)
        yield url


@pytest_asyncio.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(postgres_url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> Callable[[], AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def app(postgres_url: str) -> Iterator[FastAPI]:
    """One `FastAPI` app, one wired `AccountBalanceContainer`, for the whole session -- not one per
    test. `create_app()`/`.wire()` mutate process-global state
    (`dependency_injector.wiring`); building a second app per test would silently repoint every
    previously-built app's routes at the newest container (see decision-log.md, the `wire()`
    finding). `postgres_url` is itself session-scoped already, so overriding `settings` once here
    loses nothing a per-test override was actually giving us."""
    application = create_app()
    # Settings/engine/session_factory are process-wide (SharedDependencies),
    # not owned by AccountBalanceContainer — overridden at their real source.
    SharedDependencies.settings.override(providers.Object(Settings(database_url=postgres_url)))
    yield application
    SharedDependencies.settings.reset_override()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """Isolates every test from the next -- but not from the migration's own seed data (T8's two
    SYSTEM accounts, transfer/spec.md): those are seeded once, at container startup, not per
    test, so a blind `TRUNCATE ... accounts` would delete them after the first test that runs and
    leave every later test unable to deposit or withdraw. `entries`/`transfers`/
    `idempotency_records` are truncated first (nothing but a SYSTEM account's own zero-forever
    `balance_amount` depends on them), then every `USER` account is removed -- by that point
    nothing still references it."""
    yield
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE entries, idempotency_records, transfers"))
        await connection.execute(text("DELETE FROM accounts WHERE account_type <> 'SYSTEM'"))
