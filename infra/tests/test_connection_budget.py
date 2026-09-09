"""The database's connection budget, asserted rather than commented.

The task ceiling is the database's usable connections divided by one task's pool. That division
was previously performed against a comment describing a SQLAlchemy default that no module in the
service had declared — so the deployment's `maxCapacity` rested on a library's choice, and a
`pool_size` changed for perfectly good local reasons would have invalidated it in silence. The
resulting failure is not slowness: it is refused connections on valid money movements, and it
arrives under load, which is exactly when the extra tasks were supposed to help.

These tests close that gap from both ends. One asserts the arithmetic still holds. The other
asserts that the pool the arithmetic assumed is the pool the container is actually given — the
same shape as `test_migration_runner.py`'s deadline check, and for the same reason: an invariant
spread across two files with nothing enforcing it is a comment, not a guarantee.
"""

from __future__ import annotations

from aws_cdk.assertions import Template

from modules.shared.adapters.config.settings import Settings
from stacks.service_stack import (
    CONNECTIONS_PER_TASK,
    CONNECTIONS_RESERVED_FOR_OPERATORS,
    DATABASE_MAX_CONNECTIONS,
    MAX_OVERFLOW_PER_TASK,
    MAX_TASKS,
    MIGRATION_POOL_SIZE,
    POOL_SIZE_PER_TASK,
)


def test_a_fully_scaled_fleet_stays_within_the_database_budget() -> None:
    """The whole point of deriving `MAX_TASKS` instead of choosing it.

    Scaling out ECS does not scale RDS. Past this bound the extra tasks add no throughput -- they
    exhaust the connection pool, and the caller sees a refused connection rather than a slower
    response. Raising the instance class raises `DATABASE_MAX_CONNECTIONS`, and the ceiling should
    follow from here rather than being edited independently.
    """
    at_full_scale = MAX_TASKS * CONNECTIONS_PER_TASK

    assert at_full_scale + CONNECTIONS_RESERVED_FOR_OPERATORS <= DATABASE_MAX_CONNECTIONS, (
        f"{MAX_TASKS} tasks x {CONNECTIONS_PER_TASK} connections = {at_full_scale}, "
        f"plus {CONNECTIONS_RESERVED_FOR_OPERATORS} reserved, exceeds {DATABASE_MAX_CONNECTIONS}"
    )


def test_one_more_task_than_the_ceiling_would_exceed_the_budget() -> None:
    """The ceiling is tight, not merely safe.

    A bound that happens to hold because it is wildly conservative teaches a reader nothing and
    invites someone to raise it casually. Asserting that `MAX_TASKS + 1` breaks the budget says
    the number was derived, and turns any future edit of the inputs into a deliberate act.
    """
    one_too_many = (MAX_TASKS + 1) * CONNECTIONS_PER_TASK

    assert one_too_many + CONNECTIONS_RESERVED_FOR_OPERATORS > DATABASE_MAX_CONNECTIONS, (
        f"{MAX_TASKS + 1} tasks would still fit in {DATABASE_MAX_CONNECTIONS} connections; "
        "MAX_TASKS is not derived from the budget it claims to be derived from"
    )


def test_overflow_is_zero_so_peak_equals_steady_state() -> None:
    """Overflow would make the fleet's connection count have two answers -- a steady one and a
    peak one -- forcing every safety margin to use the peak while only the steady state is
    enjoyed. The budget above would then be an optimistic bound rather than a real one.

    `settings.py` argues the operational half: this service's contention is row locks, so a
    connection a caller can only wait on is better left unallocated.
    """
    assert MAX_OVERFLOW_PER_TASK == 0
    assert CONNECTIONS_PER_TASK == POOL_SIZE_PER_TASK


def test_the_pool_the_budget_assumes_is_the_pool_the_container_gets(
    service_template: Template,
) -> None:
    """The gap this closes is the one that existed before: the arithmetic used one number and the
    running process used another, with nothing connecting them. Injecting the pool as container
    environment makes them the same number by construction; this asserts it stayed that way.
    """
    containers = {
        container["Name"]: {
            variable["Name"]: variable["Value"] for variable in container.get("Environment", [])
        }
        for definition in service_template.find_resources("AWS::ECS::TaskDefinition").values()
        for container in definition["Properties"]["ContainerDefinitions"]
    }

    assert containers["api"]["DB_POOL_SIZE"] == str(POOL_SIZE_PER_TASK), containers["api"]
    assert containers["api"]["DB_MAX_OVERFLOW"] == str(MAX_OVERFLOW_PER_TASK), containers["api"]

    # The migration task is a separate process holding its own pool, and it runs *during* a deploy
    # while the service is at full task count -- so it spends from the same budget.
    assert containers["migrate"]["DB_POOL_SIZE"] == str(MIGRATION_POOL_SIZE), containers["migrate"]
    assert containers["migrate"]["DB_MAX_OVERFLOW"] == "0", containers["migrate"]


def test_the_injected_variable_names_are_ones_the_service_actually_reads() -> None:
    """Without this, the coupling is theatre.

    `Settings` falls back to its own defaults for any variable it does not recognise, and those
    defaults currently equal what the stack injects. A typo in an environment variable name would
    therefore change nothing observable today, while quietly severing the link between the
    deployment's arithmetic and the process's behaviour -- and it would stay severed until someone
    changed one of the two numbers and wondered why nothing happened.
    """
    fields = Settings.model_fields

    assert "db_pool_size" in fields, "DB_POOL_SIZE is injected but Settings does not read it"
    assert "db_max_overflow" in fields, "DB_MAX_OVERFLOW is injected but Settings does not read it"


def test_the_services_own_default_matches_what_the_deployment_injects() -> None:
    """Local development and production should not disagree about pool sizing without someone
    having decided that they should. They are allowed to differ -- the deployment wins, because it
    injects the value -- but a silent divergence means the pool exercised in development is not
    the pool that runs, and pool exhaustion is precisely the failure that only appears under
    load."""
    defaults = Settings(_env_file=None)

    assert defaults.db_pool_size == POOL_SIZE_PER_TASK
    assert defaults.db_max_overflow == MAX_OVERFLOW_PER_TASK
    assert defaults.max_database_connections_per_process == CONNECTIONS_PER_TASK
