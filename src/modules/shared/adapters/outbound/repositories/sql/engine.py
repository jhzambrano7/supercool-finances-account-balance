from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from modules.shared.adapters.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """One async engine per process (AO5) — psycopg3's native async driver.

    Every pool parameter is passed explicitly. Not for tidiness: the deployment divides the
    database's connection budget by this process's footprint to get its task ceiling
    (`infra/stacks/service_stack.py`), and before this was explicit that division rested on a
    SQLAlchemy default no module here had ever declared. A `pool_size` changed for good local
    reasons would have silently invalidated the production ceiling, and the failure that follows
    is not slowness — it is refused connections on perfectly valid money movements.

    `settings.py` argues each value. The one worth repeating here is `max_overflow=0`: this
    service's contention is row locks, so a connection a caller cannot use for anything but
    waiting is better left unallocated. Waiting in this pool costs nothing and is visible;
    waiting inside PostgreSQL costs a backend process and degrades every other session.
    """
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle_seconds,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """`expire_on_commit=False`: adapter methods return the domain object they just persisted.

    Not the ORM row, so there is no reason to force a reload of attributes
    nothing here re-reads after commit.
    """
    return async_sessionmaker(bind=engine, expire_on_commit=False)
