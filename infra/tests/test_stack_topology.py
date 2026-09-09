"""Which stack a resource lives in, and which stacks depend on which, are decisions under test in
their own right -- see `infra/README.md`'s "What is here" and "Three stacks, not one".
"""

from __future__ import annotations

from app import AppStacks
from aws_cdk.assertions import Template


def test_ecr_repository_lives_only_in_the_registry_stack(
    registry_template: Template, data_template: Template, service_template: Template
) -> None:
    """Blocker 3: when the repository lived in the service stack, that stack both created the
    registry *and* referenced an image inside it, so a first deploy into an empty account had
    nowhere to push to beforehand -- the migration task stopped with `CannotPullContainerError`
    and the stack rolled back. Splitting it into `AccountBalanceRegistry` is what makes
    `cdk deploy AccountBalanceRegistry` before anything else possible at all.
    """
    registry_template.resource_count_is("AWS::ECR::Repository", 1)
    data_template.resource_count_is("AWS::ECR::Repository", 0)
    service_template.resource_count_is("AWS::ECR::Repository", 0)


def test_service_stack_is_deployed_after_data_and_registry(app_stacks: AppStacks) -> None:
    """`cdk deploy --all` must create the database and the registry before the service, or the
    service references a secret and an image CloudFormation has not made yet -- the exact race the
    registry split was introduced to close.

    Asserted against the **cloud assembly**, which is what the CLI actually reads to order a
    deployment, rather than against `Stack.dependencies` on the in-memory construct. The two are
    not the same question: `Stack.dependencies` is empty until synthesis runs, and reading it from
    a fixture that happens to have synthesized already makes the result depend on evaluation order
    rather than on the infrastructure.

    Note what this does *not* prove. `app.py`'s explicit `add_dependency` calls are redundant
    today: CDK derives the same ordering from the real cross-stack references (`data.credentials`,
    `registry.repository`, `data.security_group`), so deleting them leaves this green. They are
    kept as insurance against a refactor that stops passing constructs -- taking the secret ARN
    from context or SSM instead, say -- which would silently drop the derived edge. What this test
    protects is the *ordering*, however it arises; that is the property a deploy depends on.
    """
    assembly = app_stacks.app.synth()
    service = assembly.get_stack_by_name(app_stacks.service.stack_name)

    # Intersected with the assembly's own stacks: `dependencies` also lists the service's asset
    # artifact, which says nothing about ordering *between stacks*. (An `isinstance` filter does
    # not work here -- these come back through jsii as the base artifact proxy.)
    stack_ids = {stack.id for stack in assembly.stacks}
    ordered_before = {artifact.id for artifact in service.dependencies} & stack_ids

    assert ordered_before == {
        app_stacks.data.artifact_id,
        app_stacks.registry.artifact_id,
    }, ordered_before
