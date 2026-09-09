"""`AccountBalanceService` (`stacks/service_stack.py`): compute, ALB, autoscaling. Each test names
the decision in `infra/README.md` it protects, and the failure mode that decision was written to
prevent.
"""

from __future__ import annotations

from aws_cdk.assertions import Match, Template

_DB_SECRET_KEYS = {"DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"}


def test_every_container_with_db_secrets_gets_exactly_the_five_keys(
    service_template: Template,
) -> None:
    """Blocker: `alembic/env.py` used to read `DATABASE_URL` directly, which ECS never sets --
    ECS can inject one JSON key of a secret per environment variable, it cannot assemble them into
    a URL. It fell through to `alembic.ini`'s development default and migrated `localhost` on
    every deploy. Asserting the exact key set (not "at least these five") is what would catch
    `DATABASE_URL` sneaking back in alongside them, half-fixed.

    Scoped to containers that receive *some* secret, not every container in the app: a future
    sidecar with no reason to touch the database (say, an OpenTelemetry collector) is not this
    blocker's concern, and asserting on it too would fail with a message about DB keys that has
    nothing to do with what actually broke.
    """
    template_json = service_template.to_json()
    checked = 0
    for resource in template_json["Resources"].values():
        if resource["Type"] != "AWS::ECS::TaskDefinition":
            continue
        for container in resource["Properties"]["ContainerDefinitions"]:
            secrets = container.get("Secrets")
            if not secrets:
                continue
            secret_keys = {secret["Name"] for secret in secrets}
            assert secret_keys == _DB_SECRET_KEYS, (
                f"container {container['Name']!r} secrets: {secret_keys}"
            )
            checked += 1
    assert checked == 2, (
        f"expected exactly 2 containers with DB secrets (api, migrate), found {checked}"
    )


def test_alb_health_check_targets_health_not_ready(service_template: Template) -> None:
    """`/health`, not `/ready` -- see `src/modules/shared/adapters/inbound/api/health_routes.py`.
    The database is shared across every task, so pointing the load balancer's health check at
    readiness would deregister *every* task at once on a single RDS failover, leaving nothing in
    service to return an honest 503.
    """
    service_template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        Match.object_like({"HealthCheckPath": "/health"}),
    )


def test_autoscaling_ceiling_and_floor(service_template: Template) -> None:
    """`min_capacity=2` (no single point of failure at rest), `max_capacity=24` -- derived, in
    `service_stack.py`'s `MAX_TASKS`, from the database's connection budget, not chosen. Asserting
    the literal 24 here is what would catch someone raising it without also resizing the database
    ("a database sizing decision wearing a compute costume").
    """
    service_template.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalableTarget",
        Match.object_like({"MinCapacity": 2, "MaxCapacity": 24}),
    )


def test_autoscaling_has_both_request_count_and_cpu_policies(service_template: Template) -> None:
    """Request count per target is the primary signal because this service is I/O-bound on a
    PostgreSQL row lock (PRD §5): under contention tasks look *idle* while latency climbs, so a
    CPU-only policy would scale late or not at all, exactly when it is needed most. CPU stays as a
    backstop for the different case of expensive requests at a low rate -- both must exist.
    """
    service_template.resource_count_is("AWS::ApplicationAutoScaling::ScalingPolicy", 2)
    service_template.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalingPolicy",
        Match.object_like(
            {
                "TargetTrackingScalingPolicyConfiguration": Match.object_like(
                    {
                        "PredefinedMetricSpecification": Match.object_like(
                            {"PredefinedMetricType": "ALBRequestCountPerTarget"}
                        )
                    }
                )
            }
        ),
    )
    service_template.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalingPolicy",
        Match.object_like(
            {
                "TargetTrackingScalingPolicyConfiguration": Match.object_like(
                    {
                        "PredefinedMetricSpecification": Match.object_like(
                            {"PredefinedMetricType": "ECSServiceAverageCPUUtilization"}
                        )
                    }
                )
            }
        ),
    )


def test_no_resource_in_the_service_stack_is_retained(service_template: Template) -> None:
    """The combination that made a failed CREATE unrecoverable before ECR moved to its own stack
    (`RegistryStack`): `DeletionPolicy: Retain` plus a fixed physical name means deleting a
    `ROLLBACK_COMPLETE` stack orphans the named resource, and every retry then fails with
    `AlreadyExists`.

    An earlier version of this test matched only properties whose key ended in `"Name"` -- which a
    mutation caught missing: `family=` (an ECS task definition's identity property) and any
    `*Identifier` property sail straight through a `key.endswith("Name")` filter while being
    exactly the same class of CloudFormation-assigned identity. Rather than chase the next
    identity property CloudFormation invents, this asserts the stronger, simpler claim
    `infra/README.md` and `service_stack.py`'s own comments already make: compute rolls forward
    and back several times a day, and *nothing* in this stack needs RETAIN at all -- not just
    nothing with a name.
    """
    template_json = service_template.to_json()
    retained = {
        logical_id: resource["Type"]
        for logical_id, resource in template_json["Resources"].items()
        if resource.get("DeletionPolicy") == "Retain"
    }
    assert not retained, f"service stack must retain nothing; found: {retained}"
