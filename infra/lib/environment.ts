import * as ec2 from 'aws-cdk-lib/aws-ec2'
import { Construct } from 'constructs'

/**
 * The network this platform is deployed *into*, not one it owns.
 *
 * A VPC, its subnets and its NAT are platform-team property with a lifecycle measured in years;
 * a service's stack has a lifecycle measured in deploys. Creating one here would mean a
 * `cdk destroy` of this service could take the network with it, and that asymmetry is the reason
 * networking is imported rather than declared.
 *
 * `fromVpcAttributes`, not `fromLookup`, is a deliberate constraint: `fromLookup` makes a live
 * describe call, so it needs credentials and a populated `cdk.context.json` before `cdk synth`
 * will run at all. Anyone reading this repository can synthesize the templates and see exactly
 * what would be created, with no AWS account and no access. The ids below are placeholders --
 * `PLACEHOLDER.md` in this folder says which values a real deployment substitutes.
 */
export interface NetworkIds {
  readonly vpcId: string
  readonly availabilityZones: string[]
  /** Where the ALB lives: the only publicly reachable tier. */
  readonly publicSubnetIds: string[]
  /** Where Fargate tasks live: egress via NAT, no inbound from the internet. */
  readonly privateSubnetIds: string[]
  /** Where RDS lives: no route to the internet at all, in or out. */
  readonly isolatedSubnetIds: string[]
}

/** Placeholder network. Real values come from `-c vpcId=... -c ...` or a committed context file. */
export const PLACEHOLDER_NETWORK: NetworkIds = {
  vpcId: 'vpc-0fake0000000000000',
  availabilityZones: ['us-east-1a', 'us-east-1b'],
  publicSubnetIds: ['subnet-0fakepublic0000001', 'subnet-0fakepublic0000002'],
  privateSubnetIds: ['subnet-0fakeprivate000001', 'subnet-0fakeprivate000002'],
  isolatedSubnetIds: ['subnet-0fakeisolated00001', 'subnet-0fakeisolated00002'],
}

export function importVpc(scope: Construct, id: string, ids: NetworkIds): ec2.IVpc {
  return ec2.Vpc.fromVpcAttributes(scope, id, {
    vpcId: ids.vpcId,
    availabilityZones: ids.availabilityZones,
    publicSubnetIds: ids.publicSubnetIds,
    privateSubnetIds: ids.privateSubnetIds,
    isolatedSubnetIds: ids.isolatedSubnetIds,
  })
}

/**
 * Reads the network from CDK context, falling back to the placeholders.
 *
 * The fallback is what keeps `cdk synth` working for a reader with no account; a real deployment
 * passes real ids and gets a template pointed at a real network, from the same code.
 */
export function networkFromContext(scope: Construct): NetworkIds {
  const list = (key: string, fallback: string[]): string[] => {
    const raw = scope.node.tryGetContext(key)
    if (typeof raw === 'string') return raw.split(',').map((value) => value.trim())
    if (Array.isArray(raw)) return raw as string[]
    return fallback
  }

  return {
    vpcId: scope.node.tryGetContext('vpcId') ?? PLACEHOLDER_NETWORK.vpcId,
    availabilityZones: list('availabilityZones', PLACEHOLDER_NETWORK.availabilityZones),
    publicSubnetIds: list('publicSubnetIds', PLACEHOLDER_NETWORK.publicSubnetIds),
    privateSubnetIds: list('privateSubnetIds', PLACEHOLDER_NETWORK.privateSubnetIds),
    isolatedSubnetIds: list('isolatedSubnetIds', PLACEHOLDER_NETWORK.isolatedSubnetIds),
  }
}

/** True when nothing overrode the placeholders — used to print a loud warning at synth time. */
export function isPlaceholderNetwork(ids: NetworkIds): boolean {
  return ids.vpcId === PLACEHOLDER_NETWORK.vpcId
}
