"""The ledger's database, in its own stack.

Separate from the service stack because the two have different lifecycles and different blast
radii. Compute is rolled forward and back several times a day; the database holds the only copy of
every balance in the system. Putting them in one stack means a failed service deploy rolls back a
template that also owns the data, and it means `cdk destroy` on the thing you deploy daily reaches
the thing you must never destroy.

This is where PRD §5's whole concurrency argument comes to rest: the correctness of concurrent
transfers is guaranteed by `SELECT ... FOR UPDATE` on real PostgreSQL rows, not by anything in the
application's memory. Every choice below serves keeping that guarantee true.
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct

from stacks.environment import NetworkIds, import_subnets, import_vpc


class DataStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, network: NetworkIds, **kwargs: object
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        vpc = import_vpc(self, "Vpc", network)

        self.security_group = ec2.SecurityGroup(
            self,
            "DatabaseSecurityGroup",
            vpc=vpc,
            description="account-balance RDS: ingress only from the service tasks",
            # No egress rules and none needed: nothing here has the database calling out.
            allow_all_outbound=False,
        )

        self.database = rds.DatabaseInstance(
            self,
            "Database",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16_4),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
            vpc=vpc,
            # Isolated, not private: the ledger has no reason to reach the internet, so it is put
            # where it cannot, rather than where it merely does not.
            vpc_subnets=ec2.SubnetSelection(
                subnets=import_subnets(
                    self, "IsolatedSubnet", network.isolated_subnet_ids, network.availability_zones
                )
            ),
            security_groups=[self.security_group],
            # Multi-AZ: a failover is a connection reset, which the application already treats as a
            # failed request rather than a committed one. Single-AZ would make a routine
            # maintenance window an outage of every money movement on the platform.
            multi_az=True,
            database_name="account_balance",
            # Generated, never authored: a password that exists in a template or a shell history
            # is a password that leaks. Rotation is a property of this secret, and
            # `Settings._compose_database_url_from_parts` exists precisely so rotation keeps
            # working without a second, hand-maintained URL secret drifting out of sync with it.
            credentials=rds.Credentials.from_generated_secret("account_balance_app"),
            storage_encrypted=True,
            allocated_storage=20,
            max_allocated_storage=100,
            backup_retention=cdk.Duration.days(14),
            deletion_protection=True,
            # RETAIN over CloudFormation's default of deleting the instance: for a ledger, an
            # accidental `cdk destroy` losing every balance is not a recoverable mistake.
            removal_policy=cdk.RemovalPolicy.RETAIN,
            # §11.2's lock wait time is a database-side signal, not an application one -- these are
            # where it would be read from.
            cloudwatch_logs_exports=["postgresql"],
            enable_performance_insights=True,
        )

        # `Credentials.from_generated_secret` creates the secret as a construct separate from the
        # instance above -- it does not inherit the instance's `removal_policy`. Left alone it
        # defaults to Delete, so `cdk destroy AccountBalanceData` would RETAIN the database
        # (correctly) while deleting the one credential that can still reach it: recoverable
        # inside Secrets Manager's recovery window, then not. The secret must be retained for the
        # same reason the instance is.
        #
        # `self.database.secret` (== `self.credentials`) is the *attachment* wrapper
        # (`SecretTargetAttachment`), not the underlying `AWS::SecretsManager::Secret` -- CDK nests
        # the actual secret as a child construct named "Secret" of the `DatabaseInstance`. Both get
        # the removal policy: the attachment because nothing should assume it is harmless to lose,
        # the secret because it is the one that actually holds the password.
        self.credentials.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        generated_secret = self.database.node.find_child("Secret")
        if not isinstance(generated_secret, rds.DatabaseSecret):
            raise RuntimeError(
                "expected DatabaseInstance's generated secret construct at child id 'Secret'; "
                "aws-cdk-lib's internal wiring for from_generated_secret() may have changed"
            )
        generated_secret.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        cdk.CfnOutput(
            self,
            "DatabaseEndpoint",
            value=self.database.db_instance_endpoint_address,
            description="RDS endpoint — reachable only from the service security group",
        )
        cdk.CfnOutput(
            self,
            "DatabaseSecretArn",
            value=self.database.secret.secret_arn if self.database.secret else "none",
            description="Secrets Manager ARN holding the generated, rotated credentials",
        )

    @property
    def credentials(self) -> rds.DatabaseSecret:
        """The RDS-generated secret: `{username, password, host, port, dbname}`.

        Raises rather than returning `None`: every consumer needs it, and a stack that somehow
        produced an instance without one should fail at synth time, not hand out an optional that
        each caller then re-checks.
        """
        secret = self.database.secret
        if secret is None:
            raise RuntimeError("DatabaseInstance was created without a generated secret")
        return secret  # type: ignore[return-value]
