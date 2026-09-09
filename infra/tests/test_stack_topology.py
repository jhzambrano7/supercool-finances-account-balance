"""Which stack a resource lives in, and which stacks depend on which, are decisions under test in
their own right -- see `infra/README.md`'s "What is here" and "Three stacks, not one".
"""

from __future__ import annotations

from aws_cdk.assertions import Template

from stacks.data_stack import DataStack
from stacks.registry_stack import RegistryStack
from stacks.service_stack import ServiceStack


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


def test_service_stack_depends_on_data_and_registry(
    service_stack: ServiceStack, data_stack: DataStack, registry_stack: RegistryStack
) -> None:
    """`app.py` declares `service.add_dependency(data)` and `service.add_dependency(registry)`
    explicitly; `cdk deploy --all` uses this to order the stacks. Losing it re-opens the exact race
    the registry split fixed: the service (and the migration task inside it) referencing a database
    secret or an image that CloudFormation has not created yet.
    """
    dependency_ids = {dependency.node.id for dependency in service_stack.dependencies}
    assert dependency_ids == {data_stack.node.id, registry_stack.node.id}, dependency_ids
