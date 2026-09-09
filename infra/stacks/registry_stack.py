"""The container registry, deployed before anything that runs an image from it.

Its own stack for a reason that only shows up on the very first deploy. When the repository lived
in the service stack, that stack both *created* the registry and *referenced an image inside it* —
so a first deploy into an empty account had nowhere to push to beforehand, the migration task
stopped with `CannotPullContainerError`, and the stack rolled back. Worse, the repository carries
`RETAIN` with a fixed name, so deleting the `ROLLBACK_COMPLETE` stack orphaned it and every retry
then failed with `RepositoryAlreadyExistsException`. One shot, and a manual `aws ecr
delete-repository` to get another.

Splitting it makes the ordering expressible: deploy this, push an image, then deploy the rest. It
is the same lifecycle argument the database stack already makes — a registry holding every
rollback target you own should not share a fate with the compute that happens to reference it.
"""

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class RegistryStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name="account-balance",
            # The base image is somebody else's code running next to a ledger.
            image_scan_on_push=True,
            # Immutable: a tag that can be moved means two deploys can claim to be the same
            # version, and then a rollback is a guess rather than a return. It is also why
            # `imageTag` must be a real, unique tag -- see `app.py`.
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep the last 30 images; older ones are not rollback targets",
                    max_image_count=30,
                )
            ],
            # RETAIN is safe here in a way it was not inside the service stack: this stack has
            # nothing that can fail mid-create and force a delete-and-retry.
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        cdk.CfnOutput(
            self,
            "RepositoryUri",
            value=self.repository.repository_uri,
            description="Push the image built from the Dockerfile `runtime` target here",
        )
