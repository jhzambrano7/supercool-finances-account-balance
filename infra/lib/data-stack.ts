import * as cdk from 'aws-cdk-lib'
import * as ec2 from 'aws-cdk-lib/aws-ec2'
import * as rds from 'aws-cdk-lib/aws-rds'
import { Construct } from 'constructs'
import { importVpc, NetworkIds } from './environment'

export interface DataStackProps extends cdk.StackProps {
  readonly network: NetworkIds
}

/**
 * The ledger's database, in its own stack.
 *
 * Separate from the service stack because the two have different lifecycles and different blast
 * radii. Compute is rolled forward and back several times a day; the database holds the only copy
 * of every balance in the system. Putting them in one stack means a failed service deploy rolls
 * back a template that also owns the data, and it means `cdk destroy` on the thing you deploy
 * daily reaches the thing you must never destroy.
 *
 * This is the stack where PRD §5's whole concurrency argument comes to rest: the correctness of
 * concurrent transfers is guaranteed by `SELECT ... FOR UPDATE` on real PostgreSQL rows, not by
 * anything in the application's memory. Every choice below serves keeping that guarantee true.
 */
export class DataStack extends cdk.Stack {
  public readonly database: rds.DatabaseInstance
  /** RDS-generated and RDS-rotated: `{username, password, host, port, dbname}`. */
  public readonly credentials: rds.DatabaseSecret | undefined
  public readonly databaseSecurityGroup: ec2.SecurityGroup

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props)

    const vpc = importVpc(this, 'Vpc', props.network)

    this.databaseSecurityGroup = new ec2.SecurityGroup(this, 'DatabaseSecurityGroup', {
      vpc,
      description: 'account-balance RDS: ingress only from the service tasks',
      // No egress rules and none needed: nothing in this design has the database calling out.
      allowAllOutbound: false,
    })

    this.database = new rds.DatabaseInstance(this, 'Database', {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16_4,
      }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MEDIUM),
      vpc,
      // Isolated, not private: the ledger has no reason to reach the internet, so it is placed
      // where it cannot, rather than where it merely does not.
      vpcSubnets: { subnets: props.network.isolatedSubnetIds.map((subnetId, index) =>
        ec2.Subnet.fromSubnetAttributes(this, `IsolatedSubnet${index}`, {
          subnetId,
          availabilityZone: props.network.availabilityZones[index % props.network.availabilityZones.length],
        }),
      ) },
      securityGroups: [this.databaseSecurityGroup],

      // Multi-AZ: a failover is a connection reset, which the application already treats as a
      // failed request rather than as a committed one. A single-AZ instance would make a routine
      // maintenance window an outage of the entire platform's money movement.
      multiAz: true,

      databaseName: 'account_balance',
      // Generated, never authored: a password that exists in a template or a developer's shell
      // history is a password that leaks. Rotation is a property of this secret, and
      // `Settings._compose_database_url_from_parts` exists precisely so rotation keeps working
      // without a second, hand-maintained URL secret drifting out of sync with it.
      credentials: rds.Credentials.fromGeneratedSecret('account_balance_app'),

      storageEncrypted: true,
      allocatedStorage: 20,
      maxAllocatedStorage: 100,

      backupRetention: cdk.Duration.days(14),
      deletionProtection: true,
      // RETAIN over the CloudFormation default of destroying the instance: for a ledger, an
      // accidental `cdk destroy` losing every balance is not a recoverable mistake. The snapshot
      // is the thing that makes it one.
      removalPolicy: cdk.RemovalPolicy.RETAIN,

      // Diagnostics for the one metric the design actually turns on -- §11.2's lock wait time is
      // a database-side signal, not an application one.
      cloudwatchLogsExports: ['postgresql'],
      enablePerformanceInsights: true,
    })

    this.credentials = this.database.secret as rds.DatabaseSecret | undefined

    new cdk.CfnOutput(this, 'DatabaseEndpoint', {
      value: this.database.dbInstanceEndpointAddress,
      description: 'RDS endpoint — reachable only from the service security group',
    })
    new cdk.CfnOutput(this, 'DatabaseSecretArn', {
      value: this.database.secret?.secretArn ?? 'none',
      description: 'Secrets Manager ARN holding the generated, rotated credentials',
    })
  }
}
