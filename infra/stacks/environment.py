"""The network this platform is deployed *into*, not one it owns.

A VPC, its subnets and its NAT are platform-team property with a lifecycle measured in years; a
service's stack has a lifecycle measured in deploys. Creating one here would mean a `cdk destroy`
of this service could take the network with it, and that asymmetry is why networking is imported
rather than declared.

`from_vpc_attributes`, not `from_lookup`, is a deliberate constraint: `from_lookup` makes a live
describe call, so it needs credentials and a populated `cdk.context.json` before `cdk synth` will
run at all. Anyone reading this repository can synthesize the templates and see exactly what would
be created, with no AWS account and no access.
"""

from dataclasses import dataclass, field

from aws_cdk import aws_ec2 as ec2
from constructs import Construct


@dataclass(frozen=True)
class NetworkIds:
    vpc_id: str
    availability_zones: list[str]
    #: Where the ALB lives: the only publicly reachable tier.
    public_subnet_ids: list[str]
    #: Where Fargate tasks live: egress via NAT, no inbound from the internet.
    private_subnet_ids: list[str]
    #: Where RDS lives: no route to the internet at all, in or out.
    isolated_subnet_ids: list[str]


PLACEHOLDER_NETWORK = NetworkIds(
    vpc_id="vpc-0fake0000000000000",
    availability_zones=["us-east-1a", "us-east-1b"],
    public_subnet_ids=["subnet-0fakepublic0000001", "subnet-0fakepublic0000002"],
    private_subnet_ids=["subnet-0fakeprivate000001", "subnet-0fakeprivate000002"],
    isolated_subnet_ids=["subnet-0fakeisolated00001", "subnet-0fakeisolated00002"],
)


@dataclass(frozen=True)
class SubnetSelection:
    """Imported subnets, resolved once per stack.

    Each `Subnet.from_subnet_attributes` is a construct, so it must be created in the stack that
    uses it -- importing the same subnet id in two stacks is correct and expected, not duplication
    to be factored away.
    """

    subnets: list[ec2.ISubnet] = field(default_factory=list)


def import_vpc(scope: Construct, construct_id: str, ids: NetworkIds) -> ec2.IVpc:
    return ec2.Vpc.from_vpc_attributes(
        scope,
        construct_id,
        vpc_id=ids.vpc_id,
        availability_zones=ids.availability_zones,
        public_subnet_ids=ids.public_subnet_ids,
        private_subnet_ids=ids.private_subnet_ids,
        isolated_subnet_ids=ids.isolated_subnet_ids,
    )


def import_subnets(
    scope: Construct, prefix: str, subnet_ids: list[str], availability_zones: list[str]
) -> list[ec2.ISubnet]:
    return [
        ec2.Subnet.from_subnet_attributes(
            scope,
            f"{prefix}{index}",
            subnet_id=subnet_id,
            availability_zone=availability_zones[index % len(availability_zones)],
        )
        for index, subnet_id in enumerate(subnet_ids)
    ]


def network_from_context(scope: Construct) -> NetworkIds:
    """Reads the network from CDK context, falling back to the placeholders.

    The fallback is what keeps `cdk synth` working for a reader with no account; a real deployment
    passes real ids and gets a template pointed at a real network, from the same code.
    """

    def ids(key: str, fallback: list[str]) -> list[str]:
        raw = scope.node.try_get_context(key)
        if isinstance(raw, str):
            return [value.strip() for value in raw.split(",")]
        if isinstance(raw, list):
            return [str(value) for value in raw]
        return fallback

    return NetworkIds(
        vpc_id=scope.node.try_get_context("vpcId") or PLACEHOLDER_NETWORK.vpc_id,
        availability_zones=ids("availabilityZones", PLACEHOLDER_NETWORK.availability_zones),
        public_subnet_ids=ids("publicSubnetIds", PLACEHOLDER_NETWORK.public_subnet_ids),
        private_subnet_ids=ids("privateSubnetIds", PLACEHOLDER_NETWORK.private_subnet_ids),
        isolated_subnet_ids=ids("isolatedSubnetIds", PLACEHOLDER_NETWORK.isolated_subnet_ids),
    )


def is_placeholder_network(ids: NetworkIds) -> bool:
    """True when nothing overrode the placeholders — used to warn loudly at synth time."""
    return ids.vpc_id == PLACEHOLDER_NETWORK.vpc_id
