"""ECR, the Fargate service behind an ALB, its autoscaling policy, and the migration that runs
before the service is allowed to update.
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from constructs import Construct

from stacks.environment import NetworkIds, import_subnets, import_vpc
from stacks.migration_runner import MigrationRunner

CONTAINER_PORT = 8000

#: How many database connections one task can hold: SQLAlchemy's defaults, `pool_size` 5 plus
#: `max_overflow` 10. One engine per process (AO5), so this is per task, not per request.
CONNECTIONS_PER_TASK = 15

#: `db.t4g.medium` has 4 GiB, and RDS derives `max_connections` as `DBInstanceClassMemory/9531392`
#: — roughly 450. The reserve is for rotation, the migration task, and a human with `psql` during
#: an incident, which is the worst possible moment to find there is no connection left.
DATABASE_MAX_CONNECTIONS = 450
CONNECTIONS_RESERVED_FOR_OPERATORS = 90

#: The ceiling on horizontal scaling — derived, not chosen, and that is the point.
#:
#: Scaling out ECS does not scale the database. Past this number the extra tasks add no throughput;
#: they exhaust the connection pool, and the failure mode is not slower responses but *refused
#: connections*, surfacing as errors on money movements that were perfectly valid. Raising this is
#: a database sizing decision wearing a compute costume: change the instance class and this number
#: follows, never the other way round.
MAX_TASKS = (DATABASE_MAX_CONNECTIONS - CONNECTIONS_RESERVED_FOR_OPERATORS) // CONNECTIONS_PER_TASK


class ServiceStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        network: NetworkIds,
        database_secret: rds.DatabaseSecret,
        # By id, not as a construct: `add_ingress_rule` creates the rule in the stack that *owns*
        # the group, so passing the object would make DataStack reference this stack while this
        # stack references DataStack — a dependency cycle CDK refuses to synthesize. Importing it
        # here re-homes the rule, which is also where it belongs: "the service may reach the
        # database" is a fact about the service.
        database_security_group_id: str,
        image_tag: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        vpc = import_vpc(self, "Vpc", network)
        private_subnets = import_subnets(
            self, "PrivateSubnet", network.private_subnet_ids, network.availability_zones
        )
        public_subnets = import_subnets(
            self, "PublicSubnet", network.public_subnet_ids, network.availability_zones
        )

        # ---------------------------------------------------------------- registry

        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name="account-balance",
            # The base image is somebody else's code running next to a ledger.
            image_scan_on_push=True,
            # Immutable: a tag that can be moved means two deploys can claim to be the same
            # version, and then a rollback is a guess rather than a return.
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep the last 30 images; older ones are not rollback targets",
                    max_image_count=30,
                )
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        image = ecs.ContainerImage.from_ecr_repository(self.repository, image_tag)

        # ---------------------------------------------------------------- security groups

        alb_security_group = ec2.SecurityGroup(
            self, "AlbSecurityGroup", vpc=vpc, description="account-balance ALB: public ingress"
        )
        alb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS from the internet"
        )

        service_security_group = ec2.SecurityGroup(
            self,
            "ServiceSecurityGroup",
            vpc=vpc,
            description="account-balance tasks: ingress only from the ALB",
        )
        # Not `any_ipv4` on the container port: the tasks sit in private subnets, but "unreachable
        # by routing" and "not allowed" are different guarantees, and only the second survives
        # someone later attaching a route.
        service_security_group.add_ingress_rule(
            alb_security_group,
            ec2.Port.tcp(CONTAINER_PORT),
            "Application traffic from the load balancer only",
        )

        database_security_group = ec2.SecurityGroup.from_security_group_id(
            self, "ImportedDatabaseSecurityGroup", database_security_group_id, mutable=True
        )
        database_security_group.add_ingress_rule(
            service_security_group,
            ec2.Port.tcp(5432),
            "PostgreSQL from the account-balance tasks only",
        )

        # ---------------------------------------------------------------- task definitions

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name="/ecs/account-balance",
            # §11.4: a movement that cannot be reconstructed from its trace cannot be explained to
            # a customer. Retention is that requirement expressed in days.
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            cluster_name="account-balance",
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # The credentials reach the container as five separate values, one per JSON key, because
        # that is the only thing ECS can do with a secret: inject keys, never assemble them. The
        # application composes the URL itself (`Settings._compose_database_url_from_parts`), which
        # is what lets RDS rotate this secret without a second, hand-written URL secret silently
        # going stale behind it.
        secrets = {
            "DB_HOST": ecs.Secret.from_secrets_manager(database_secret, "host"),
            "DB_PORT": ecs.Secret.from_secrets_manager(database_secret, "port"),
            "DB_USER": ecs.Secret.from_secrets_manager(database_secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(database_secret, "password"),
            "DB_NAME": ecs.Secret.from_secrets_manager(database_secret, "dbname"),
        }

        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64),
        )
        task_definition.add_container(
            "api",
            image=image,
            secrets=secrets,
            port_mappings=[ecs.PortMapping(container_port=CONTAINER_PORT)],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=log_group),
            # Container-level liveness, distinct from the ALB's: this one restarts a wedged task
            # even after the load balancer has already stopped sending it traffic.
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    'python -c "import urllib.request;'
                    f"urllib.request.urlopen('http://localhost:{CONTAINER_PORT}/health')\"",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(30),
            ),
        )
        # No explicit `grant_read`: passing `secrets` to `add_container` already grants the
        # execution role exactly the keys named above, and a hand-written grant would only widen
        # that.

        migration_task_definition = ecs.FargateTaskDefinition(
            self,
            "MigrationTaskDefinition",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64),
        )
        migration_task_definition.add_container(
            "migrate",
            image=image,
            secrets=secrets,
            command=["alembic", "upgrade", "head"],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="migrate", log_group=log_group),
        )

        # ---------------------------------------------------------------- migrations

        migrations = MigrationRunner(
            self,
            "Migrations",
            cluster=cluster,
            task_definition=migration_task_definition,
            container_name="migrate",
            subnets=private_subnets,
            security_group=service_security_group,
            image_tag=image_tag,
        )

        # ---------------------------------------------------------------- service and ingress

        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            service_name="account-balance",
            desired_count=2,
            security_groups=[service_security_group],
            vpc_subnets=ec2.SubnetSelection(subnets=private_subnets),
            # A bad image rolls itself back rather than sitting half-deployed until somebody
            # notices the error rate.
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
            max_healthy_percent=200,
        )
        # The ordering that makes the migration mean anything: new tasks must not start against a
        # schema that has not been brought forward yet.
        service.node.add_dependency(migrations.resource)

        self.load_balancer = elbv2.ApplicationLoadBalancer(
            self,
            "LoadBalancer",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnets=public_subnets),
        )

        listener = self.load_balancer.add_listener(
            "Listener",
            port=80,
            # HTTP because a placeholder account has no ACM certificate. A real deployment adds one
            # and redirects 80 to 443; the security group above already admits only 443, so that is
            # the one line which changes.
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        target_group = listener.add_targets(
            "Api",
            port=CONTAINER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service],
            health_check=elbv2.HealthCheck(
                # `/health`, not `/ready`, on purpose -- see `health_routes.py`. The database is
                # shared, so pointing this at readiness would deregister every task at once during
                # a single RDS failover, turning a database blip into a total blackhole with
                # nothing left in service to return an honest 503.
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(15),
                timeout=cdk.Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            # Long enough for an in-flight transfer to finish committing rather than being cut
            # mid-request; short enough that a deploy is not measured in coffee breaks.
            deregistration_delay=cdk.Duration.seconds(30),
        )

        # ---------------------------------------------------------------- autoscaling

        scaling = service.auto_scale_task_count(min_capacity=2, max_capacity=MAX_TASKS)

        # Requests per target is the primary signal, not CPU. This service is I/O-bound: a transfer
        # spends its time waiting on a row lock in PostgreSQL (§5), not burning CPU. Under real
        # contention the tasks look *idle* while latency climbs, so a CPU-driven policy scales
        # late, or not at all, exactly when it is needed.
        scaling.scale_on_request_count(
            "RequestScaling",
            requests_per_target=1000,
            target_group=target_group,
            scale_in_cooldown=cdk.Duration.minutes(5),
            scale_out_cooldown=cdk.Duration.minutes(1),
        )

        # CPU stays as a backstop for the case the request-rate signal misses: expensive requests
        # at a low rate. Deliberately not the primary policy.
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=cdk.Duration.minutes(5),
            scale_out_cooldown=cdk.Duration.minutes(1),
        )

        # ---------------------------------------------------------------- outputs

        cdk.CfnOutput(
            self,
            "ServiceUrl",
            value=f"http://{self.load_balancer.load_balancer_dns_name}",
            description="Public entry point (add ACM + HTTPS for a real deployment)",
        )
        cdk.CfnOutput(
            self,
            "RepositoryUri",
            value=self.repository.repository_uri,
            description="Push the image built from the Dockerfile `runtime` target here",
        )
        cdk.CfnOutput(
            self,
            "MigrationTaskFamily",
            value=migration_task_definition.family,
            description=(
                "Applied automatically during deploy (Migrations custom resource); named here "
                "for the manual re-run an incident occasionally needs"
            ),
        )
