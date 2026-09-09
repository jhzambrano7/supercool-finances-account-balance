"""Template-shape helpers shared by the assertion tests.

Not a test module itself (no `test_` prefix, so pytest never collects it). The IAM helpers scan by
*statement shape* rather than by resource type or logical id: whether CDK emits a grant as a
standalone `AWS::IAM::Policy` or inline on `AWS::IAM::Role.Properties.Policies` is an implementation
detail of the CDK version in `uv.lock`, and the blockers these tests guard against
(`logs:*RetentionPolicy` on `"*"`, unscoped `iam:PassRole`) are about statement content, not which
resource wrapper happens to carry it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

JsonDict = dict[str, Any]
#: `Template.to_json()` returns a read-only `Mapping`, not a `dict` -- these helpers only ever
#: read it, so accepting the wider type is what the actual call sites pass.
JsonMapping = Mapping[str, Any]


def iam_statements(template_json: JsonMapping) -> list[JsonDict]:
    """Every IAM statement in the template, wherever CDK chose to put it."""
    statements: list[JsonDict] = []
    for resource in template_json["Resources"].values():
        properties = resource.get("Properties", {})
        documents = []
        policy_document = properties.get("PolicyDocument")
        if policy_document:
            documents.append(policy_document)
        for inline in properties.get("Policies", []) or []:
            inline_document = inline.get("PolicyDocument")
            if inline_document:
                documents.append(inline_document)
        for document in documents:
            statements.extend(document.get("Statement", []))
    return statements


def actions_of(statement: JsonDict) -> list[str]:
    action = statement.get("Action", [])
    return action if isinstance(action, list) else [action]


def resources_of(statement: JsonDict) -> list[Any]:
    resource = statement.get("Resource", [])
    return resource if isinstance(resource, list) else [resource]


def statements_granting(statements: list[JsonDict], *actions: str) -> list[JsonDict]:
    """`Allow` statements whose `Action` intersects `actions` (singular or list form, both legal
    CloudFormation shapes)."""
    wanted = set(actions)
    return [s for s in statements if s.get("Effect") == "Allow" and wanted & set(actions_of(s))]


def grants_on_wildcard_resource(statement: JsonDict) -> bool:
    return "*" in resources_of(statement)


def statements_for_lambda_handler(template_json: JsonMapping, handler: str) -> list[JsonDict]:
    """IAM statements on the execution role of the Lambda function whose `Handler` equals
    `handler` -- e.g. `"migration_handler.on_event"`. Identifying the function by its handler
    string rather than its logical id (a CDK-internal hash) is what lets this survive a
    `aws-cdk-lib` bump that changes how construct ids get hashed.
    """
    functions = {
        logical_id: resource
        for logical_id, resource in template_json["Resources"].items()
        if resource["Type"] == "AWS::Lambda::Function"
        and resource["Properties"].get("Handler") == handler
    }
    assert len(functions) == 1, (
        f"expected exactly one Lambda::Function with Handler={handler!r}, found {list(functions)}"
    )
    ((_function_id, function),) = functions.items()
    role_logical_id = function["Properties"]["Role"]["Fn::GetAtt"][0]

    statements: list[JsonDict] = []
    for resource in template_json["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        roles = resource["Properties"].get("Roles", [])
        if any(isinstance(role, dict) and role.get("Ref") == role_logical_id for role in roles):
            statements.extend(resource["Properties"]["PolicyDocument"]["Statement"])
    return statements
