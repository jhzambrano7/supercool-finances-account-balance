"""Integration tests for `/health` and `/ready` against a real PostgreSQL.

These endpoints exist for the ALB target group (`infra/`), and a target group whose health check
404s restarts every task forever -- so "does this path exist and return 200" is not a trivial
assertion here, it is the difference between the service deploying and not.
"""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_liveness_answers_without_touching_the_database(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_the_database_as_reachable(client: AsyncClient) -> None:
    """The half of the probe that means something: §11.4 requires readiness to reflect the ledger,
    not merely the process."""
    response = await client.get("/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ready", "database": "reachable"}
