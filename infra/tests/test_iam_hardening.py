"""IAM statements that must never appear anywhere in the app, in any of the three stacks --
scanned globally rather than construct-by-construct, because both blockers here are things CDK
itself can reintroduce silently through a convenience prop, not just something a person types by
hand.

Honesty check on the loop: today only `AccountBalanceService` carries any IAM statement at all
(the migration runner's two Lambda roles); `AccountBalanceRegistry` and `AccountBalanceData`
contribute zero. Looping over all three anyway is what keeps these tests correct the day either of
them gains an IAM statement, without anyone having to remember to add it here then.
"""

from __future__ import annotations

from _helpers import (
    grants_on_wildcard_resource,
    iam_statements,
    managed_policy_arns,
    statements_granting,
)
from aws_cdk.assertions import Template

#: AWS-managed policies broad enough that attaching one to anything in this app should be a
#: deliberate, reviewed decision -- never a default nobody noticed.
_DANGEROUS_MANAGED_POLICY_SUFFIXES = ("AdministratorAccess", "PowerUserAccess", "IAMFullAccess")


def test_no_statement_grants_log_retention_management_on_every_log_group(
    registry_template: Template, data_template: Template, service_template: Template
) -> None:
    """Blocker 5: the deprecated `log_retention=` CDK prop provisions a helper Lambda whose role
    holds `logs:PutRetentionPolicy` and `logs:DeleteRetentionPolicy` on `Resource: "*"` -- a
    function able to shorten or drop the retention of *any* log group in the account, audit trails
    included, to configure the handful of its own. `MigrationRunner._function` declares its log
    groups directly for exactly this reason (see the docstring in `migration_runner.py`); this test
    is what stops that prop from quietly coming back, here or anywhere else in the app.
    """
    for name, template in (
        ("registry", registry_template),
        ("data", data_template),
        ("service", service_template),
    ):
        statements = iam_statements(template.to_json())
        offenders = [
            statement
            for statement in statements_granting(
                statements, "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy"
            )
            if grants_on_wildcard_resource(statement)
        ]
        assert not offenders, f"{name} stack grants log retention management on '*': {offenders}"


def test_no_statement_grants_pass_role_on_a_wildcard_resource(
    registry_template: Template, data_template: Template, service_template: Template
) -> None:
    """Blocker 7, checked globally: `iam:PassRole` on `Resource: "*"` lets whoever can invoke the
    granted principal pass *any* role in the account to a service that accepts one -- a
    privilege-escalation path. `test_migration_runner.py` checks the migration starter's grant is
    scoped to exactly two ARNs; this test is the backstop that nothing else in the app, now or
    added later, grants the unscoped form.
    """
    for name, template in (
        ("registry", registry_template),
        ("data", data_template),
        ("service", service_template),
    ):
        statements = iam_statements(template.to_json())
        offenders = [
            statement
            for statement in statements_granting(statements, "iam:PassRole")
            if grants_on_wildcard_resource(statement)
        ]
        assert not offenders, f"{name} stack grants iam:PassRole on '*': {offenders}"


def test_no_role_attaches_a_broad_aws_managed_policy(
    registry_template: Template, data_template: Template, service_template: Template
) -> None:
    """The blind spot `iam_statements` cannot close: a role's `ManagedPolicyArns` references a
    policy by ARN, and that policy's actual statements -- for an AWS-managed policy like
    `AdministratorAccess` -- live outside this template entirely, so no amount of scanning inline
    `PolicyDocument`s would ever see them. This is the narrower backstop: it cannot see what such a
    policy grants, only that a role attached a name obviously too broad for anything in this app.
    """
    for name, template in (
        ("registry", registry_template),
        ("data", data_template),
        ("service", service_template),
    ):
        arns = managed_policy_arns(template.to_json())
        offenders = [
            arn
            for arn in arns
            if isinstance(arn, str) and arn.endswith(_DANGEROUS_MANAGED_POLICY_SUFFIXES)
        ]
        assert not offenders, f"{name} stack attaches a broad AWS-managed policy: {offenders}"
