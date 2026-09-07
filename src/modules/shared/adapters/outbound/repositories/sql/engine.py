from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from modules.shared.adapters.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """One async engine per process (AO5) — psycopg3's native async driver."""
    return create_async_engine(settings.database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """`expire_on_commit=False`: adapter methods return the domain object they just persisted.

    Not the ORM row, so there is no reason to force a reload of attributes
    nothing here re-reads after commit.
    """
    return async_sessionmaker(bind=engine, expire_on_commit=False)
