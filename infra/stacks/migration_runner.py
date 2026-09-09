"""A construct that actually runs the migration task during `cdk deploy`."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import CustomResource
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import custom_resources as cr
from constructs import Construct

_RUNTIME_DIRECTORY = Path(__file__).resolve().parent.parent / "runtime"

#: Must stay strictly greater than `runtime/migration_handler.py`'s `DEADLINE_SECONDS`. If this
#: resource's own timeout elapses first, CloudFormation gives up on the custom resource while
#: `is_complete`'s poller is still waiting out its own, longer deadline -- the task keeps running,
#: still holding the advisory lock `alembic/env.py` takes, against a schema CloudFormation has
#: already decided to roll back. `infra/tests/test_migration_runner.py` asserts the ordering
#: across both files; nothing enforces it at import time because the two run in different
#: processes (this one at synth time, the other inside the Lambda runtime).
PROVIDER_TOTAL_TIMEOUT = cdk.Duration.hours(1)


class MigrationRunner(Construct):
    """Applies `alembic upgrade head` before the service is allowed to update.

    The `runtime` image target deliberately does not migrate on boot (see the repository's
    `Dockerfile`): a schema that changes because a process happened to start is a change nobody
    gated, applied at a moment nobody chose, concurrently by however many tasks scaled up at once.
    That decision leaves a gap, and this construct is what fills it — otherwise `cdk deploy`
    reports success while the service starts against a schema that was never brought forward.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        container_name: str,
        subnets: list[ec2.ISubnet],
        security_group: ec2.ISecurityGroup,
        image_tag: str,
    ) -> None:
        super().__init__(scope, construct_id)

        environment = {
            "CLUSTER_ARN": cluster.cluster_arn,
            "TASK_DEFINITION_ARN": task_definition.task_definition_arn,
            "SUBNET_IDS": ",".join(subnet.subnet_id for subnet in subnets),
            "SECURITY_GROUP_IDS": security_group.security_group_id,
            "CONTAINER_NAME": container_name,
        }

        def _function(construct_id: str, handler: str) -> lambda_.Function:
            """Each function gets a log group this stack owns.

            Not `log_retention=`: that deprecated prop provisions a helper Lambda whose role holds
            `logs:PutRetentionPolicy` and `logs:DeleteRetentionPolicy` on `Resource: "*"` -- a
            function in this account able to shorten or drop the retention of *any* log group,
            audit trails included, to configure four of its own. Declaring the group directly
            grants nothing and removes five `Custom::LogRetention` resources from the template.
            """
            return lambda_.Function(
                self,
                construct_id,
                runtime=lambda_.Runtime.PYTHON_3_13,
                handler=f"migration_handler.{handler}",
                code=lambda_.Code.from_asset(str(_RUNTIME_DIRECTORY)),
                timeout=cdk.Duration.minutes(2),
                environment=environment,
                log_group=logs.LogGroup(
                    self,
                    f"{construct_id}Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
            )

        # Neither function runs inside the VPC: they call the ECS control plane, which is a public
        # API. Attaching them would add ENIs, cold starts and a NAT dependency to buy nothing --
        # the *task* they start is the thing that must be inside.
        on_event = _function("OnEvent", "on_event")
        is_complete = _function("IsComplete", "is_complete")

        # Split by what each one actually calls, rather than one shared statement. `is_complete`
        # holding `ecs:RunTask` would let the poller start tasks, which is exactly the authority a
        # poller should not have.
        on_event.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=["*"],
                conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
            )
        )
        is_complete.add_to_role_policy(
            iam.PolicyStatement(
                # `StopTask` is used on the timeout path: when the custom resource gives up, the
                # migration task is still running, and leaving it to finish against a schema
                # CloudFormation has already decided to roll back is worse than killing it.
                actions=["ecs:DescribeTasks", "ecs:StopTask"],
                resources=["*"],
                conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
            )
        )

        # `PassRole` is scoped to exactly the two roles this task definition uses, and granted only
        # to the function that starts tasks. Left unscoped it would let anyone who can invoke this
        # function start a task as any role in the account -- a privilege-escalation path, not a
        # convenience.
        passable_roles = [task_definition.task_role.role_arn]
        if task_definition.execution_role is not None:
            passable_roles.append(task_definition.execution_role.role_arn)
        on_event.add_to_role_policy(
            iam.PolicyStatement(actions=["iam:PassRole"], resources=passable_roles)
        )

        provider = cr.Provider(
            self,
            "Provider",
            on_event_handler=on_event,
            is_complete_handler=is_complete,
            # Polling, not one long invocation: a migration that locks and rewrites a table can run
            # for many minutes, and Lambda's 15-minute ceiling is not something to bet a schema
            # change against.
            query_interval=cdk.Duration.seconds(15),
            total_timeout=PROVIDER_TOTAL_TIMEOUT,
        )

        self.resource = CustomResource(
            self,
            "Resource",
            service_token=provider.service_token,
            properties={
                # `imageTag` is what makes this re-run. CloudFormation only invokes a custom
                # resource whose properties changed, so without it a second deploy of a new image
                # would silently skip migrations -- the same bug as never running them, only
                # harder to notice because the first deploy worked.
                "imageTag": image_tag,
                "taskDefinitionArn": task_definition.task_definition_arn,
            },
        )
