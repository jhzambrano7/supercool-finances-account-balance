#!/usr/bin/env python
"""CDK entry point. `cdk synth` runs this with no AWS credentials — see `environment.py`."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.environment import is_placeholder_network, network_from_context
from stacks.registry_stack import RegistryStack
from stacks.service_stack import ServiceStack

#: What `imageTag` becomes when nobody passes one. Deliberately not `latest`.
#:
#: `latest` was the previous default and it was wrong three ways at once: the registry is
#: `IMMUTABLE`, so `:latest` can be pushed exactly once; a moving tag makes a rollback a guess; and
#: — the quiet one — a constant tag means the migration custom resource's properties never change,
#: so CloudFormation never sends it an Update and migrations silently stop running after the first
#: deploy. A placeholder that cannot exist in the registry fails the deploy loudly instead.
PLACEHOLDER_IMAGE_TAG = "PLACEHOLDER-pass-c-imageTag"


@dataclass(frozen=True)
class AppStacks:
    """The composed app plus each stack, by name -- what `build_app` hands back.

    Returning the individual stacks (not just `app`) is what lets a caller assert on them directly
    without walking `app.node.find_child(...)`; `infra/tests/conftest.py` is exactly such a
    caller. Returning `app` too is what lets `__main__` call `.synth()` without a second lookup.
    """

    app: cdk.App
    registry: RegistryStack
    data: DataStack
    service: ServiceStack


def build_app(app: cdk.App | None = None) -> AppStacks:
    """Builds the full three-stack app -- the one function both `__main__` below and
    `infra/tests/conftest.py` call.

    Before this existed, `infra/tests/conftest.py` re-implemented this wiring by hand, which meant
    a decision that lives only here (`service.add_dependency(...)`, the placeholder `imageTag`
    default, whether `env` gets bound) was invisible to the test suite while a test asserting on
    the *fixture's own* re-implementation of it looked like it was covering the real thing. An
    adversarial review caught this by deleting the `add_dependency` calls from this file and
    finding the suite still green. One function, called from both places, is what makes that class
    of gap impossible: there is no wiring left for a test fixture to duplicate.

    `app` is accepted (defaulting to a fresh `cdk.App()`) so a caller can control the context a
    build sees -- `conftest.py` uses it to pin a fixed `imageTag` for assertions and to load
    `cdk.json`'s own context, so the templates under test are the templates `cdk synth` actually
    emits.
    """
    app = app if app is not None else cdk.App()

    network = network_from_context(app)

    if is_placeholder_network(network):
        # Loud, and on stderr so it cannot be mistaken for part of the template. Synthesizing
        # against placeholders is the supported way to *read* this infrastructure; deploying
        # against them is not, and the difference should never be discovered at `cdk deploy` time.
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

    registry = RegistryStack(
        app,
        "AccountBalanceRegistry",
        env=env,
        description=(
            "account-balance: the ECR repository, deployed before anything that pulls from it"
        ),
    )

    image_tag = app.node.try_get_context("imageTag") or PLACEHOLDER_IMAGE_TAG
    if image_tag == PLACEHOLDER_IMAGE_TAG:
        print(
            "[account-balance] No -c imageTag=… given, using a placeholder that cannot exist "
            "in ECR.\n"
            "  Synthesizing is fine; deploying will fail on the image pull. Pass a real, "
            "unique tag:\n"
            "  cdk deploy -c imageTag=$(git rev-parse --short HEAD)",
            file=sys.stderr,
        )

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
        repository=registry.repository,
        # A deploy *is* this value changing, and it is also what re-triggers migrations.
        image_tag=image_tag,
        env=env,
        description="account-balance: ECR, ECS/Fargate behind an ALB, autoscaling, migrations",
    )

    service.add_dependency(data)
    service.add_dependency(registry)

    cdk.Tags.of(app).add("service", "account-balance")
    cdk.Tags.of(app).add("managed-by", "cdk")

    return AppStacks(app=app, registry=registry, data=data, service=service)


if __name__ == "__main__":
    build_app().app.synth()
