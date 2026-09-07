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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_migrations(database_url: str) -> None:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
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


@pytest_asyncio.fixture(autouse=True)
async def _clean_accounts_table(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE accounts"))
