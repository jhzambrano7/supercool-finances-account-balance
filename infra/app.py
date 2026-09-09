#!/usr/bin/env python
"""CDK entry point. `cdk synth` runs this with no AWS credentials — see `environment.py`."""

import os
import sys

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.environment import is_placeholder_network, network_from_context
from stacks.service_stack import ServiceStack

app = cdk.App()

network = network_from_context(app)

if is_placeholder_network(network):
    # Loud, and on stderr so it cannot be mistaken for part of the template. Synthesizing against
    # placeholders is the supported way to *read* this infrastructure; deploying against them is
    # not, and the difference should never be discovered at `cdk deploy` time.
    print(
        "[account-balance] Synthesizing against PLACEHOLDER network ids. Pass real ones with\n"
        "  cdk synth -c vpcId=vpc-… -c publicSubnetIds=… -c privateSubnetIds=… "
        "-c isolatedSubnetIds=…\n"
        "  (see infra/README.md). Templates are valid to read, not to deploy.",
        file=sys.stderr,
    )

# `env` is left unbound unless the CLI supplied one: these stacks are region-agnostic to
# synthesize, so a reader with no AWS account still gets templates. A real deployment binds it
# through CDK_DEFAULT_ACCOUNT/CDK_DEFAULT_REGION, which the CLI fills from the active profile.
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("CDK_DEFAULT_REGION")
env = cdk.Environment(account=account, region=region) if account and region else None

data = DataStack(
    app,
    "AccountBalanceData",
    network=network,
    env=env,
    description="account-balance: RDS PostgreSQL and its generated, rotated credentials",
)

service = ServiceStack(
    app,
    "AccountBalanceService",
    network=network,
    database_secret=data.credentials,
    database_security_group_id=data.security_group.security_group_id,
    # A deploy *is* this value changing, and it is also what re-triggers migrations. Never
    # `latest`: a moving tag makes a rollback a guess, and makes the migration step a no-op.
    image_tag=app.node.try_get_context("imageTag") or "latest",
    env=env,
    description="account-balance: ECR, ECS/Fargate behind an ALB, autoscaling, migrations",
)

service.add_dependency(data)

cdk.Tags.of(app).add("service", "account-balance")
cdk.Tags.of(app).add("managed-by", "cdk")

app.synth()
