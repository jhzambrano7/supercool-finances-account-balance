"""`infra/README.md`'s "Reading it without an account": `cdk synth` must work with no AWS
credentials and no `cdk.context.json`. The other test files rely on this by using the session
`stacks` fixture at all -- it is built from a `cdk.App()` with no context and no environment,
exactly like a reader with no AWS account gets. This file asserts on the property directly, and
uses its own app rather than the shared fixture so a regression here fails loudly instead of just
making every other test's fixture explode for an unrelated-looking reason.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.data_stack import DataStack
from stacks.environment import PLACEHOLDER_NETWORK, is_placeholder_network, network_from_context
from stacks.registry_stack import RegistryStack
from stacks.service_stack import ServiceStack


def test_network_from_context_falls_back_to_placeholders_with_no_context() -> None:
    """`Vpc.from_vpc_attributes` (static ids), not `Vpc.from_lookup` (a live `DescribeVpcs` call)
    -- the whole point of `stacks/environment.py`'s docstring. A context-less app must resolve to
    the placeholder network, not raise, and not silently need a populated `cdk.context.json`.
    """
    app = cdk.App()
    network = network_from_context(app)
    assert network == PLACEHOLDER_NETWORK
    assert is_placeholder_network(network) is True


def test_the_full_app_synthesizes_with_no_context_and_no_bound_environment() -> None:
    """The end-to-end version of the property above: build all three stacks exactly as `app.py`
    does when nobody passes `-c vpcId=...` and `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION` are
    unset, and confirm `Template.from_stack` (CDK's own synth-and-validate path) does not raise.
    If `import_vpc` ever switched to `Vpc.from_lookup`, this would fail here -- `from_lookup`
    requires a concrete account/region to make its context-provider lookup, which an unbound
    environment does not have, and it would raise before producing a template at all.
    """
    app = cdk.App()
    network = network_from_context(app)
    registry = RegistryStack(app, "AccountBalanceRegistry")
    data = DataStack(app, "AccountBalanceData", network=network)
    service = ServiceStack(
        app,
        "AccountBalanceService",
        network=network,
        database_secret=data.credentials,
        database_security_group_id=data.security_group.security_group_id,
        repository=registry.repository,
        image_tag="synth-smoke-test",
    )

    service_template = Template.from_stack(service)

    # The placeholder vpc id appearing literally in the synthesized template is the observable
    # difference between "imported statically" and "resolved via a live lookup": a `from_lookup`
    # call would have needed a real vpc id from `cdk.context.json` to produce a template at all,
    # and the literal placeholder string would never appear in its output.
    template_json = service_template.to_json()
    security_groups = template_json["Resources"]
    vpc_ids = {
        resource["Properties"]["VpcId"]
        for resource in security_groups.values()
        if resource["Type"] == "AWS::EC2::SecurityGroup"
    }
    assert vpc_ids == {PLACEHOLDER_NETWORK.vpc_id}
