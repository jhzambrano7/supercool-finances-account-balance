"""Decisions that live only in `app.py`, not in any `stacks/*` module -- the exact class of thing
the fixture used to hide by re-implementing this wiring itself instead of calling `build_app()`.
Each test here builds its own app rather than using the shared `app_stacks` fixture, because each
one needs a *different* context or environment than the fixture's fixed `IMAGE_TAG` and unbound
`env`.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from app import PLACEHOLDER_IMAGE_TAG, build_app
from aws_cdk.assertions import Template
from conftest import cdk_json_context


def test_the_default_image_tag_cannot_exist_in_ecr_and_reaches_the_custom_resource() -> None:
    """Blocker 2's actual fix: `latest` was wrong three ways at once, and the quiet one was that a
    *constant* tag means the migration custom resource's properties never change between deploys,
    so CloudFormation never sends it an Update and migrations silently stop running after the
    first one. Nothing before this test exercised `app.py`'s fallback at all -- setting
    `PLACEHOLDER_IMAGE_TAG` back to `"latest"` left the whole suite green.
    """
    assert PLACEHOLDER_IMAGE_TAG != "latest"

    # No `imageTag` context supplied: this is the branch `build_app` takes when nobody passes
    # `-c imageTag=...`, exactly as a first `cdk synth` from a clean checkout would.
    app = cdk.App(context=cdk_json_context())
    stacks = build_app(app)

    service_template = Template.from_stack(stacks.service)
    service_template.has_resource_properties(
        "AWS::CloudFormation::CustomResource",
        {"imageTag": PLACEHOLDER_IMAGE_TAG},
    )


def test_env_is_unbound_with_no_cdk_default_account_or_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader with no AWS account must still get a synthesizable template
    (`infra/README.md`, "Reading it without an account") -- binding `env` unconditionally would
    break that the moment CDK started requiring a concrete account/region for something it
    synthesizes today.
    """
    monkeypatch.delenv("CDK_DEFAULT_ACCOUNT", raising=False)
    monkeypatch.delenv("CDK_DEFAULT_REGION", raising=False)

    stacks = build_app(cdk.App(context=cdk_json_context()))

    assert cdk.Token.is_unresolved(stacks.registry.account)
    assert cdk.Token.is_unresolved(stacks.registry.region)


def test_env_is_bound_when_both_cdk_default_vars_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: a real deployment (`cdk deploy`, run by the CLI with a profile active) sets
    both variables, and the stacks must pick up a concrete account/region rather than staying
    environment-agnostic -- an unbound stack cannot own environment-specific things a later change
    might add (e.g. a Route53 record, an environment-scoped lookup).
    """
    monkeypatch.setenv("CDK_DEFAULT_ACCOUNT", "123456789012")
    monkeypatch.setenv("CDK_DEFAULT_REGION", "us-east-1")

    stacks = build_app(cdk.App(context=cdk_json_context()))

    assert not cdk.Token.is_unresolved(stacks.registry.account)
    assert not cdk.Token.is_unresolved(stacks.registry.region)
    assert stacks.registry.account == "123456789012"
    assert stacks.registry.region == "us-east-1"
