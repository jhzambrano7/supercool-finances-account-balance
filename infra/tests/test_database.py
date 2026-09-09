"""`AccountBalanceData` (`stacks/data_stack.py`): the ledger's only copy of every balance. Each
assertion here protects one line of `infra/README.md` -- these are decisions someone could revert
one property at a time without anyone noticing until an incident.
"""

from __future__ import annotations

from typing import Any

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


def test_the_database_and_its_generated_secret_are_both_retained(data_template: Template) -> None:
    """RETAIN on the instance is the mirror image of the rule in `test_service_stack.py`: this
    stack has nothing that can fail mid-CREATE and force a delete-and-retry, so RETAIN costs
    nothing and buys the one thing that matters -- an accidental `cdk destroy` must not be able to
    take every balance in the system with it.

    That claim is false if the check stops at the instance: a real defect found in this stack
    (`rds.Credentials.from_generated_secret` does not propagate the instance's `removal_policy` to
    the secret it creates) meant the database survived `cdk destroy` while the only credential that
    can reach it did not -- recoverable inside Secrets Manager's recovery window, then not. Every
    `AWS::SecretsManager::Secret` in this stack must be retained too, not just the instance.
    """
    instances = data_template.find_resources("AWS::RDS::DBInstance")
    assert len(instances) == 1, instances
    ((_logical_id, instance),) = instances.items()
    assert instance.get("DeletionPolicy") == "Retain", instance.get("DeletionPolicy")

    secrets = data_template.find_resources("AWS::SecretsManager::Secret")
    assert secrets, "expected at least one generated database secret"
    non_retained_secrets = {
        logical_id: resource.get("DeletionPolicy")
        for logical_id, resource in secrets.items()
        if resource.get("DeletionPolicy") != "Retain"
    }
    assert not non_retained_secrets, f"secrets not retained: {non_retained_secrets}"


def test_the_database_security_group_admits_5432_only_from_security_groups_never_a_cidr(
    registry_template: Template, data_template: Template, service_template: Template
) -> None:
    """The ingress rule lives in `ServiceStack` today, not `DataStack`: `add_ingress_rule` creates
    the rule in the stack that *owns* the security group object passed to it, and the database's
    group is imported by id into the service stack precisely so "the service may reach the
    database" is expressed where it conceptually belongs (see the comment on
    `database_security_group_id` in `service_stack.py`). This test searches all three stacks
    instead of hard-coding that, so it does not care which stack the rule is declared in.

    Deliberately *not* asserting there is exactly one rule: giving the migration task its own
    security group -- a real improvement, since today it shares `service_security_group` with the
    API -- would legitimately add a second 5432 ingress rule, and a count of exactly 1 would turn
    red for that being done right. What must hold regardless of how many rules there are is that
    every one of them names a security group, never a CIDR.
    """
    ingress_on_5432: list[dict[str, Any]] = []
    for template in (registry_template, data_template, service_template):
        template_json = template.to_json()
        ingress_on_5432.extend(
            resource["Properties"]
            for resource in template_json["Resources"].values()
            if resource["Type"] == "AWS::EC2::SecurityGroupIngress"
            and resource["Properties"].get("ToPort") == 5432
        )

    assert ingress_on_5432, "expected at least one 5432 ingress rule somewhere in the app"
    for properties in ingress_on_5432:
        assert "SourceSecurityGroupId" in properties, (
            f"5432 must be scoped to a security group, not a CIDR: {properties}"
        )
        assert "CidrIp" not in properties
        assert "CidrIpv6" not in properties
