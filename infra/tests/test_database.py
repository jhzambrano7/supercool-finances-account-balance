"""`AccountBalanceData` (`stacks/data_stack.py`): the ledger's only copy of every balance. Each
assertion here protects one line of `infra/README.md` -- these are decisions someone could revert
one property at a time without anyone noticing until an incident.
"""

from __future__ import annotations

from aws_cdk.assertions import Match, Template


def test_the_database_is_postgres_multi_az_encrypted_and_deletion_protected(
    data_template: Template,
) -> None:
    """Multi-AZ: a failover is a connection reset, which the application already treats as a
    failed request rather than a committed one -- single-AZ would turn a routine maintenance
    window into an outage of every money movement on the platform. `StorageEncrypted` and
    `DeletionProtection` are the baseline for a store that is the source of truth for balances.
    """
    data_template.has_resource_properties(
        "AWS::RDS::DBInstance",
        Match.object_like(
            {
                "Engine": "postgres",
                "MultiAZ": True,
                "StorageEncrypted": True,
                "DeletionProtection": True,
            }
        ),
    )


def test_the_database_deletion_policy_is_retain(data_template: Template) -> None:
    """RETAIN here is the mirror image of the rule in `test_service_stack.py`: this stack has
    nothing that can fail mid-CREATE and force a delete-and-retry, so RETAIN costs nothing and
    buys the one thing that matters -- an accidental `cdk destroy` must not be able to take every
    balance in the system with it.
    """
    instances = data_template.find_resources("AWS::RDS::DBInstance")
    assert len(instances) == 1, instances
    ((_logical_id, resource),) = instances.items()
    assert resource.get("DeletionPolicy") == "Retain", resource.get("DeletionPolicy")


def test_the_database_security_group_admits_5432_only_from_the_service_security_group(
    service_template: Template,
) -> None:
    """The ingress rule lives in `ServiceStack`, not `DataStack`: `add_ingress_rule` creates the
    rule in the stack that *owns* the security group object passed to it, and the database's group
    is imported by id into the service stack precisely so "the service may reach the database" is
    expressed where it conceptually belongs (see the comment on `database_security_group_id` in
    `service_stack.py`). Whichever stack it lives in, there must be exactly one such rule, scoped
    to a security group -- never a CIDR.
    """
    template_json = service_template.to_json()
    ingress_on_5432 = [
        resource["Properties"]
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::EC2::SecurityGroupIngress"
        and resource["Properties"].get("ToPort") == 5432
    ]
    assert len(ingress_on_5432) == 1, (
        f"expected exactly one 5432 ingress rule, found: {ingress_on_5432}"
    )

    properties = ingress_on_5432[0]
    assert "SourceSecurityGroupId" in properties, (
        f"5432 must be scoped to the service security group, not a CIDR: {properties}"
    )
    assert "CidrIp" not in properties
    assert "CidrIpv6" not in properties
