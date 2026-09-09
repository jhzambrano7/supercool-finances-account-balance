"""`RegistryStack` (`stacks/registry_stack.py`): the ECR repository, deployed before anything that
pulls from it.
"""

from __future__ import annotations

from aws_cdk.assertions import Match, Template


def test_repository_tags_are_immutable_and_scanned_on_push(registry_template: Template) -> None:
    """Immutable tags are what makes `imageTag` a meaningful rollback target at all: a mutable tag
    means two deploys can claim to be the same version, and a rollback becomes a guess rather than
    a return (`infra/README.md`, "ECR tags are immutable"). Scan-on-push, because the base image is
    somebody else's code running next to a ledger.
    """
    registry_template.has_resource_properties(
        "AWS::ECR::Repository",
        Match.object_like(
            {
                "ImageTagMutability": "IMMUTABLE",
                "ImageScanningConfiguration": Match.object_like({"ScanOnPush": True}),
            }
        ),
    )
