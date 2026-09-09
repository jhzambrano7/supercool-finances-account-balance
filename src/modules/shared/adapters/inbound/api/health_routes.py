"""Liveness and readiness.

`docs/prd.md` §11 is descoped: no metrics, no exporters. These two endpoints are not part of that
— they are a deployment requirement. An ALB target group cannot be created without a health-check
path, and one pointed at `/` gets a 404, which ECS reads as an unhealthy task and restarts forever.
Without this, the infrastructure does not come up at all.

**Why the load balancer checks `/health` and not `/ready`.** They answer different questions.
Liveness asks "is this process still working"; readiness asks "can it serve traffic right now",
which for this service means the ledger is reachable (§11.4: "readiness must fail when the database
is unreachable, because a balance service that answers while blind to its ledger is worse than one
that admits it is down").

Wiring the ALB to readiness sounds stricter and is worse: the database is shared, so one RDS
failover deregisters *every* task at once, and the service loses even the ability to return an
honest 503 — a database blip becomes a total blackhole, and the recovery cannot be observed because
nothing is left registered to observe it. Liveness at the load balancer keeps tasks in service to
fail loudly; readiness stays a deliberate probe, for deployment gates and for a human asking
whether a task is actually able to work.
"""

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from modules.shared.adapters.config.dependencies import SharedDependencies

router = APIRouter(tags=["health"])

_logger = logging.getLogger(__name__)


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving. Touches nothing else on purpose — a liveness probe
    that depends on a downstream turns that downstream's outage into a restart loop."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, str]:
    """Readiness: the ledger is reachable.

    Reads the engine straight off `SharedDependencies` rather than through `@inject`, unlike every
    use-case route. Those are wired per module because a use case must be swappable; this is not a
    use case. It is the process reporting on itself, and it needs the one engine the process
    actually holds (AO5) -- an injected substitute would make the probe answer about something
    other than the connection pool in use, which is the only thing worth asking about.

    Returns `503`, not an exception, because the caller of a readiness probe wants a verdict rather
    than a stack trace -- and because a 500 here would be indistinguishable from the service being
    broken in some *other* way, which is precisely the distinction the probe exists to make.
    """
    try:
        async with SharedDependencies.engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        _logger.exception("readiness probe failed: the database is unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable"}

    return {"status": "ready", "database": "reachable"}
