from sqlalchemy.ext.asyncio import AsyncSession


class KeptOpenSession:
    """An `async with`-able wrapper that hands back an already-open session without closing it on
    exit.

    Every SQL adapter in this module (`SqlAccountRepository`, `SqlTransferRepository`,
    `SqlIdempotencyRepository`) follows the same shape: every method does
    `async with self._session_factory() as session: ...`. Passing a real `async_sessionmaker`
    gives each call a fresh, independently-committed session -- fine for a single statement.
    Passing `lambda: KeptOpenSession(session)` instead makes every one of those `async with`
    blocks reuse the *same* session without ending its transaction early, which is what lets a
    `SELECT ... FOR UPDATE` lock survive from acquisition through a later `update()`/`add()` call
    and into the enclosing unit of work's own commit -- none of those classes' existing code has
    to know which case it is in.

    Shared by every SQL-backed unit of work (`SqlTransferUnitOfWork`, `SqlAccountUnitOfWork`)
    rather than reimplemented per one: this mechanism is what keeps a lock held across repository
    calls, and two independent copies could silently drift apart on exactly the property that
    matters.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None
