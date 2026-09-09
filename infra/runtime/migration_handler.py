"""Runs `alembic upgrade head` as an ECS task, as part of `cdk deploy`.

Declaring a migration task definition is not the same as running one. Before this existed, the
stack described a perfectly good migration task that nothing ever invoked: `cdk deploy` completed,
reported success, and left the service pointed at an unmigrated schema.

**Why a CDK custom resource and not the pipeline.** Both are legitimate, and in a repository with a
CodePipeline the migration would usually be a pipeline stage. There is no pipeline here, so putting
it anywhere but the deployment itself would mean a deploy that is not a deploy — a step a human has
to remember, which is the exact failure mode `docker-compose.yml` already refuses locally.

**Why two handlers.** CDK's `Provider` supports an async pattern: `on_event` starts work and
returns, `is_complete` is polled until it says otherwise. Migrations take minutes to lock and
rewrite tables, and Lambda's 15-minute ceiling is not a bound anyone should be betting a schema
change against. Splitting them means the wait costs nothing and cannot time out mid-migration.

**Failure is the point.** A migration that exits non-zero fails the custom resource, which fails
the stack, which rolls the deployment back — instead of shipping code against a schema that never
arrived.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ecs = boto3.client("ecs")

CLUSTER = os.environ["CLUSTER_ARN"]
TASK_DEFINITION = os.environ["TASK_DEFINITION_ARN"]
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_IDS = os.environ["SECURITY_GROUP_IDS"].split(",")
CONTAINER_NAME = os.environ["CONTAINER_NAME"]

#: How long a single migration task is allowed to run before this poller kills it.
#:
#: Deliberately shorter than the custom resource's own `total_timeout`. If the resource times out
#: first, CloudFormation gives up while the task keeps running -- still holding the advisory lock
#: `alembic/env.py` takes, still rewriting tables, against a schema CloudFormation has already
#: decided to roll back. Stopping it from here means the deadline is enforced by something that
#: can actually act on it.
DEADLINE_SECONDS = int(os.environ.get("MIGRATION_DEADLINE_SECONDS", "2700"))


def on_event(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Starts the migration task. Returns immediately; `is_complete` does the waiting."""
    request_type = event["RequestType"]

    if request_type == "Delete":
        # Deliberately a no-op. There is no "down" here: an automated rollback of a schema change
        # is how a rollback turns into data loss, and `cdk destroy` on this stack must not be the
        # thing that decides to run one.
        logger.info("Delete: no migration is run on stack deletion, by design")
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "migration-noop")}

    response = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=TASK_DEFINITION,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": SECURITY_GROUP_IDS,
                # Private subnets with a NAT: the task pulls its image and reads its secret over
                # egress, and is never itself reachable.
                "assignPublicIp": "DISABLED",
            }
        },
    )

    failures = response.get("failures") or []
    if failures:
        raise RuntimeError(f"could not start the migration task: {failures}")

    task_arn = response["tasks"][0]["taskArn"]
    logger.info("migration task started: %s", task_arn)
    # The task ARN becomes the physical id, so every deploy that migrates is traceable to the exact
    # task whose logs explain what it did.
    return {"PhysicalResourceId": task_arn}


def is_complete(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Polled until it reports completion. Raising here fails the deployment."""
    if event["RequestType"] == "Delete":
        return {"IsComplete": True}

    task_arn = event["PhysicalResourceId"]
    described = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])

    tasks = described.get("tasks") or []
    if not tasks:
        raise RuntimeError(f"migration task {task_arn} vanished before it could be inspected")

    task = tasks[0]
    if task["lastStatus"] != "STOPPED":
        started = task.get("startedAt") or task.get("createdAt")
        running_for = (datetime.now(UTC) - started).total_seconds() if started else 0.0
        if running_for > DEADLINE_SECONDS:
            logger.error(
                "migration task %s has run for %.0fs, past the %ss deadline; stopping it",
                task_arn,
                running_for,
                DEADLINE_SECONDS,
            )
            ecs.stop_task(cluster=CLUSTER, task=task_arn, reason="migration exceeded its deadline")
            raise RuntimeError(
                f"migrations exceeded {DEADLINE_SECONDS}s and were stopped. Task {task_arn} -- "
                "check its logs before retrying; a partially applied migration needs a human."
            )

        logger.info("migration task %s is %s (%.0fs)", task_arn, task["lastStatus"], running_for)
        return {"IsComplete": False}

    container = next(
        (c for c in task.get("containers", []) if c["name"] == CONTAINER_NAME),
        None,
    )
    if container is None:
        raise RuntimeError(f"migration task {task_arn} has no container named {CONTAINER_NAME}")

    exit_code = container.get("exitCode")
    if exit_code != 0:
        # `stoppedReason` carries the interesting half when the container never ran at all --
        # an image that could not be pulled, or a secret the execution role could not read.
        raise RuntimeError(
            f"migrations failed: exit code {exit_code}, "
            f"reason {container.get('reason') or task.get('stoppedReason')}. "
            f"Task {task_arn}"
        )

    logger.info("migrations applied successfully by %s", task_arn)
    return {"IsComplete": True}
