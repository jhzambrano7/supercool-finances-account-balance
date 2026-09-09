"""Unit tests for `runtime/migration_handler.py` -- the two Lambda handlers behind
`MigrationRunner`'s custom resource.

Nothing in `test_migration_runner.py` executes a line of this module: those tests check the
template *declares* the right custom resource, IAM and environment. This module is plain Python
over a `boto3` client, so it is tested the plain way -- import once with fake-but-well-formed
required env vars (set in `conftest.py`, before any test module imports it), replace the module
singleton `ecs` with a `unittest.mock.MagicMock` per test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from runtime import migration_handler

JsonDict = dict[str, Any]


@pytest.fixture
def ecs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A fresh mock ECS client per test -- `migration_handler.ecs` is a module-level singleton
    built once at import time, so tests must not share call history on it, and must never reach
    a real AWS account.
    """
    mock = MagicMock(name="ecs_client")
    monkeypatch.setattr(migration_handler, "ecs", mock)
    return mock


def _task(
    *,
    last_status: str,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    container_name: str = "migrate",
) -> JsonDict:
    container: JsonDict = {"name": container_name}
    if exit_code is not None:
        container["exitCode"] = exit_code
    task: JsonDict = {"lastStatus": last_status, "containers": [container]}
    if started_at is not None:
        task["startedAt"] = started_at
    return task


class TestOnEvent:
    def test_delete_is_a_no_op_and_never_starts_a_task(self, ecs: MagicMock) -> None:
        """Invert this and `cdk destroy` becomes data loss: `infra/README.md`'s "Delete is a
        deliberate no-op" exists because an automated rollback of a schema change is how a
        rollback turns into data loss. This is the one line of code that promise depends on.
        """
        result = migration_handler.on_event(
            {"RequestType": "Delete", "PhysicalResourceId": "prior-physical-id"}, None
        )
        ecs.run_task.assert_not_called()
        assert result == {"PhysicalResourceId": "prior-physical-id"}

    def test_create_starts_the_task_and_returns_its_arn_as_the_physical_id(
        self, ecs: MagicMock
    ) -> None:
        ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-east-1:123456789012:task/test-cluster/abc"}],
            "failures": [],
        }

        result = migration_handler.on_event({"RequestType": "Create"}, None)

        ecs.run_task.assert_called_once()
        assert result == {
            "PhysicalResourceId": "arn:aws:ecs:us-east-1:123456789012:task/test-cluster/abc"
        }

    def test_a_run_task_failure_raises(self, ecs: MagicMock) -> None:
        ecs.run_task.return_value = {
            "tasks": [],
            "failures": [{"reason": "RESOURCE:MEMORY"}],
        }

        with pytest.raises(RuntimeError, match="could not start the migration task"):
            migration_handler.on_event({"RequestType": "Create"}, None)


class TestIsComplete:
    def test_delete_reports_complete_without_describing_anything(self, ecs: MagicMock) -> None:
        result = migration_handler.is_complete(
            {"RequestType": "Delete", "PhysicalResourceId": "x"}, None
        )
        assert result == {"IsComplete": True}
        ecs.describe_tasks.assert_not_called()

    def test_a_still_running_task_reports_not_complete(self, ecs: MagicMock) -> None:
        """The worst available regression in this repository: invert `!= "STOPPED"` and
        CloudFormation is told the migration is done while the task is still holding its advisory
        lock and rewriting tables, so the ECS service (which `DependsOn`s this resource) starts
        against a half-migrated schema.
        """
        ecs.describe_tasks.return_value = {
            "tasks": [_task(last_status="RUNNING", started_at=datetime.now(UTC))]
        }

        result = migration_handler.is_complete(
            {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
        )

        assert result == {"IsComplete": False}
        ecs.stop_task.assert_not_called()

    def test_a_vanished_task_raises(self, ecs: MagicMock) -> None:
        ecs.describe_tasks.return_value = {"tasks": []}

        with pytest.raises(RuntimeError, match="vanished"):
            migration_handler.is_complete(
                {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
            )

    def test_a_task_past_the_deadline_is_stopped_and_raises(self, ecs: MagicMock) -> None:
        started = datetime.now(UTC) - timedelta(seconds=migration_handler.DEADLINE_SECONDS + 30)
        ecs.describe_tasks.return_value = {
            "tasks": [_task(last_status="RUNNING", started_at=started)]
        }

        with pytest.raises(RuntimeError, match="exceeded"):
            migration_handler.is_complete(
                {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
            )

        ecs.stop_task.assert_called_once()
        assert ecs.stop_task.call_args.kwargs["task"] == "arn"

    def test_a_stopped_task_with_no_matching_container_raises(self, ecs: MagicMock) -> None:
        ecs.describe_tasks.return_value = {
            "tasks": [_task(last_status="STOPPED", container_name="something-else")]
        }

        with pytest.raises(RuntimeError, match="no container named"):
            migration_handler.is_complete(
                {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
            )

    def test_a_nonzero_exit_code_raises(self, ecs: MagicMock) -> None:
        """Blocker 1 in a different costume: change `!= 0` to `is None` and a container that
        exited 1 (a failed alembic run) reports success -- exactly the failure mode
        `MigrationRunner` exists to prevent, `cdk deploy` reporting success against an unmigrated
        schema, just moved one file over.
        """
        ecs.describe_tasks.return_value = {"tasks": [_task(last_status="STOPPED", exit_code=1)]}

        with pytest.raises(RuntimeError, match="exit code 1"):
            migration_handler.is_complete(
                {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
            )

    def test_a_zero_exit_code_reports_complete(self, ecs: MagicMock) -> None:
        ecs.describe_tasks.return_value = {"tasks": [_task(last_status="STOPPED", exit_code=0)]}

        result = migration_handler.is_complete(
            {"RequestType": "Create", "PhysicalResourceId": "arn"}, None
        )

        assert result == {"IsComplete": True}
