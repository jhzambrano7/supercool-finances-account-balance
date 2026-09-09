"""The migration runner: `MigrationRunner` (`stacks/migration_runner.py`), invoked from
`ServiceStack`. Every test here locks down a blocker from the adversarial review documented in
`docs/decision-log.md` and `infra/README.md`'s "Migrations run during `cdk deploy`, and gate the
service" -- a declared migration task definition that nothing invokes is indistinguishable, at the
template level, from no migration at all, except that it *looks* like there is one.
"""

from __future__ import annotations

from _helpers import (
    actions_of,
    resources_of,
    statements_for_lambda_handler,
)
from aws_cdk.assertions import Match, Template


def test_a_custom_resource_invokes_the_migration_task(service_template: Template) -> None:
    """Blocker 1: the previous version declared `MigrationTaskDefinition` and left it to a
    `CfnOutput` with a string for a human to paste -- `cdk deploy` reported success against an
    unmigrated schema. A custom resource that actually calls `ecs:RunTask` is the only thing that
    makes "migrations ran" a property of the deploy instead of a step someone has to remember.
    """
    service_template.resource_count_is("AWS::CloudFormation::CustomResource", 1)
    service_template.has_resource_properties(
        "AWS::CloudFormation::CustomResource",
        Match.object_like({"taskDefinitionArn": Match.any_value()}),
    )


def test_the_ecs_service_depends_on_the_migration_custom_resource(
    service_template: Template,
) -> None:
    """Blocker 1, other half: declaring the custom resource is not enough on its own either --
    without `service.node.add_dependency(migrations.resource)`, CloudFormation is free to create
    the `ECS::Service` and the migration in parallel, and "the migration ran first" would be
    accidental rather than guaranteed.
    """
    custom_resources = service_template.find_resources("AWS::CloudFormation::CustomResource")
    assert len(custom_resources) == 1, custom_resources
    (migration_logical_id,) = custom_resources.keys()

    ecs_services = service_template.find_resources("AWS::ECS::Service")
    assert len(ecs_services) == 1, ecs_services
    ((_service_id, service_resource),) = ecs_services.items()

    depends_on = service_resource.get("DependsOn", [])
    assert migration_logical_id in depends_on, (
        f"AWS::ECS::Service must DependsOn the migration custom resource {migration_logical_id!r}; "
        f"found DependsOn={depends_on!r}"
    )


def test_the_migration_custom_resource_carries_image_tag_as_a_property(
    service_template: Template,
) -> None:
    """Blocker 2: CloudFormation only re-invokes a custom resource whose `Properties` changed
    between deploys. A constant `imageTag` (or none at all) means the *second* deploy of a new
    image silently skips migrations -- worse than never running them, because the first deploy
    made it look like the mechanism worked.
    """
    service_template.has_resource_properties(
        "AWS::CloudFormation::CustomResource",
        Match.object_like({"imageTag": "test-1234567"}),  # conftest.IMAGE_TAG
    )


def test_the_migration_container_runs_the_wrapper_not_bare_alembic(
    service_template: Template,
) -> None:
    """`docker/migrate.py`, not `alembic upgrade head`: a CloudFormation rollback re-invokes this
    same resource with the *previous* image, whose `versions/` has never heard of the revision now
    in `alembic_version`. Bare alembic exits non-zero there, failing the rollback itself and
    leaving `UPDATE_ROLLBACK_FAILED` for a human to unstick -- see `infra/README.md`. The wrapper
    recognizes "the database is ahead of me" and exits clean instead.
    """
    service_template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        Match.object_like(
            {
                "ContainerDefinitions": Match.array_with(
                    [Match.object_like({"Command": ["python", "docker/migrate.py"]})]
                )
            }
        ),
    )


def test_the_poller_cannot_start_tasks(service_template: Template) -> None:
    """Blocker 6: `is_complete` only polls and, on timeout, stops the task it is polling
    (`ecs:DescribeTasks`, `ecs:StopTask`). Holding `ecs:RunTask` would let the poller start new
    migration tasks, which is exactly the authority a poller has no business having.
    """
    template_json = service_template.to_json()
    poller_statements = statements_for_lambda_handler(
        template_json, "migration_handler.is_complete"
    )
    poller_actions = {action for statement in poller_statements for action in actions_of(statement)}

    assert "ecs:RunTask" not in poller_actions, (
        f"is_complete's role must not hold ecs:RunTask; actions granted: {sorted(poller_actions)}"
    )
    assert {"ecs:DescribeTasks", "ecs:StopTask"} <= poller_actions, (
        "is_complete must still be able to observe and, on timeout, stop the task it polls "
        "(see MIGRATION_DEADLINE_SECONDS in runtime/migration_handler.py); "
        f"found {sorted(poller_actions)}"
    )


def test_the_starter_holds_run_task_scoped_to_the_cluster(service_template: Template) -> None:
    """The mirror image of the poller test: `on_event` is the half of the split that is *supposed*
    to hold `ecs:RunTask`, and it must stay scoped to this cluster (`ArnEquals: ecs:cluster`)
    rather than every cluster in the account.
    """
    template_json = service_template.to_json()
    starter_statements = statements_for_lambda_handler(template_json, "migration_handler.on_event")
    run_task_statements = [s for s in starter_statements if "ecs:RunTask" in actions_of(s)]

    assert run_task_statements, "on_event must hold ecs:RunTask to start the migration task"
    for statement in run_task_statements:
        assert "ecs:cluster" in statement.get("Condition", {}).get("ArnEquals", {}), (
            f"ecs:RunTask must be scoped to this cluster via a condition, found: {statement}"
        )


def test_pass_role_is_scoped_to_the_migration_task_roles_never_a_wildcard(
    service_template: Template,
) -> None:
    """Blocker 7: an unscoped `iam:PassRole` lets anything that can invoke this function start an
    ECS task as *any* role in the account -- a privilege-escalation path, not a convenience. It
    must name exactly the two roles `MigrationTaskDefinition` actually uses.
    """
    statements = service_template.to_json()["Resources"]
    pass_role_statements = [
        statement["Properties"]["PolicyDocument"]["Statement"]
        for statement in statements.values()
        if statement["Type"] == "AWS::IAM::Policy"
    ]
    flattened = [
        s for group in pass_role_statements for s in group if "iam:PassRole" in actions_of(s)
    ]

    assert flattened, "expected at least one iam:PassRole statement (the migration starter's grant)"
    for statement in flattened:
        resources = resources_of(statement)
        assert resources != ["*"] and "*" not in resources, (
            f"iam:PassRole must be scoped to specific role ARNs, never '*': {statement}"
        )
        assert len(resources) == 2, (
            "expected exactly the task role and execution role of MigrationTaskDefinition "
            f"(see MigrationRunner.passable_roles), found {len(resources)}: {resources}"
        )


def test_the_pollers_deadline_stays_under_the_providers_total_timeout() -> None:
    """Two files, one invariant, enforced nowhere at import time because the two run in different
    processes: `migration_handler.py`'s `DEADLINE_SECONDS` is read inside the Lambda runtime;
    `migration_runner.py`'s `PROVIDER_TOTAL_TIMEOUT` is read at synth time, by CDK. If the
    resource's own timeout ever drops to or below the poller's deadline, CloudFormation gives up
    on the custom resource first -- the task keeps running, still holding the advisory lock
    `alembic/env.py` takes, against a schema CloudFormation has already decided to roll back.
    """
    from runtime.migration_handler import DEADLINE_SECONDS

    from stacks.migration_runner import PROVIDER_TOTAL_TIMEOUT

    assert PROVIDER_TOTAL_TIMEOUT.to_seconds() > DEADLINE_SECONDS, (
        f"poller deadline ({DEADLINE_SECONDS}s) must stay under the provider's total_timeout "
        f"({PROVIDER_TOTAL_TIMEOUT.to_seconds()}s), or CloudFormation gives up first"
    )


def test_the_lambdas_container_name_matches_the_migration_containers_name(
    service_template: Template,
) -> None:
    """`service_stack.py` names the container `"migrate"` in two places (the container
    definition, and `MigrationRunner`'s `container_name=`), and `migration_handler.py` looks it up
    by that name from its own `CONTAINER_NAME` env var. Rename one and every deploy fails inside
    `is_complete` *after* the migration already ran, rolling back a successfully applied schema
    because the poller can no longer find the container it is meant to inspect.
    """
    template_json = service_template.to_json()

    container_names_from_lambda_env = {
        resource["Properties"]["Environment"]["Variables"]["CONTAINER_NAME"]
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::Lambda::Function"
        and "CONTAINER_NAME" in resource["Properties"].get("Environment", {}).get("Variables", {})
    }
    assert container_names_from_lambda_env, (
        "expected at least one Lambda with a CONTAINER_NAME env var"
    )

    migrate_container_names = {
        container["Name"]
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::ECS::TaskDefinition"
        for container in resource["Properties"]["ContainerDefinitions"]
        if container.get("Command") == ["python", "docker/migrate.py"]
    }
    assert migrate_container_names, "expected to find the migration container by its command"

    assert container_names_from_lambda_env == migrate_container_names, (
        f"Lambda CONTAINER_NAME env var(s) {container_names_from_lambda_env} must match the "
        f"migration container's actual name(s) {migrate_container_names}"
    )
