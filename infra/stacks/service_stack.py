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

#: The pool this deployment gives each task. These are **injected into the container** as
#: `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` and divided into the connection budget below to get the task
#: ceiling — one value, used for both, so the arithmetic and the running process cannot disagree.
#:
#: Before this, the ceiling was derived from a comment asserting SQLAlchemy's defaults. The
#: service never declared a pool at all, so production capacity rested on a library default that
#: no module owned; changing it for good local reasons would have invalidated the ceiling in
#: silence, and the failure that follows is refused connections on valid money movements.
#:
#: `settings.py` argues each number. Overflow is zero because this service's contention is row
#: locks: a connection a caller can only wait on is better left unallocated, since waiting in the
#: application pool costs nothing while waiting inside PostgreSQL costs a backend process and
#: degrades every other session.
POOL_SIZE_PER_TASK = 10
MAX_OVERFLOW_PER_TASK = 0

#: One engine per process (AO5), so this is the footprint of one task, not of one request. Peak
#: equals steady state by construction, which is what makes the budget below a real bound rather
#: than an optimistic one.
CONNECTIONS_PER_TASK = POOL_SIZE_PER_TASK + MAX_OVERFLOW_PER_TASK

#: The migration task is a separate process with a pool of its own, and it runs *during a deploy*
#: — while the service is at full task count. Kept deliberately small: it is one sequential
#: `alembic upgrade head` holding one advisory lock, so it has no use for concurrency.
MIGRATION_POOL_SIZE = 2

#: `db.t4g.medium` has 4 GiB, and RDS derives `max_connections` as `DBInstanceClassMemory/9531392`
#: — roughly 450. The reserve covers secret rotation, the migration task's own small pool, and a
#: human with `psql` during an incident, which is the worst possible moment to discover there is
#: no connection left.
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
        repository: ecr.IRepository,
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

        # The registry is `RegistryStack`'s, not this stack's: an image has to exist before
        # anything can run it, and a stack cannot create a repository and pull from it in the same
        # deployment.
        image = ecs.ContainerImage.from_ecr_repository(repository, image_tag)

        # ---------------------------------------------------------------- security groups

        alb_security_group = ec2.SecurityGroup(
            self, "AlbSecurityGroup", vpc=vpc, description="account-balance ALB: public ingress"
        )
        # No hand-written 443 rule. `add_listener` opens the listener's own port by default
        # (`open=True`), so writing one here produced a group that advertised HTTPS while the only
        # thing actually listening was plaintext 80 -- a stated invariant that the synthesized
        # template contradicted. A real deployment adds an ACM certificate and a 443 listener, and
        # that listener opens its own port the same way.

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
            # DESTROY, unlike the database and the registry. A named log group that survives its
            # stack blocks the stack from ever being recreated -- the retry after a failed first
            # deploy fails again on `ResourceAlreadyExistsException`. The durable record of what
            # the ledger did is the database and its backups; a month of application logs is not
            # worth making the stack un-redeployable for.
            removal_policy=cdk.RemovalPolicy.DESTROY,
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
            # The pool is passed in, not left to the image's default. That is what makes
            # `MAX_TASKS` a real bound: the number divided into the budget and the number the
            # process actually opens are the same number.
            environment={
                "DB_POOL_SIZE": str(POOL_SIZE_PER_TASK),
                "DB_MAX_OVERFLOW": str(MAX_OVERFLOW_PER_TASK),
            },
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
            environment={
                "DB_POOL_SIZE": str(MIGRATION_POOL_SIZE),
                "DB_MAX_OVERFLOW": "0",
            },
            # `docker/migrate.py`, not bare `alembic upgrade head`: during a CloudFormation
            # rollback this same resource is re-invoked with the *previous* image, whose
            # `versions/` does not contain the revision now in `alembic_version`. Plain alembic
            # exits non-zero there, which fails the rollback itself and wedges the stack in
            # UPDATE_ROLLBACK_FAILED. The wrapper recognizes "the database is ahead of me",
            # refuses to downgrade, and exits clean.
            command=["python", "docker/migrate.py"],
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
            "MigrationTaskFamily",
            value=migration_task_definition.family,
            description=(
                "Applied automatically during deploy (Migrations custom resource); named here "
                "for the manual re-run an incident occasionally needs"
            ),
        )
