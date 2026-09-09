import * as cdk from 'aws-cdk-lib'
import * as ec2 from 'aws-cdk-lib/aws-ec2'
import * as ecr from 'aws-cdk-lib/aws-ecr'
import * as ecs from 'aws-cdk-lib/aws-ecs'
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2'
import * as logs from 'aws-cdk-lib/aws-logs'
import * as rds from 'aws-cdk-lib/aws-rds'
import { Construct } from 'constructs'
import { importVpc, NetworkIds } from './environment'

export interface ServiceStackProps extends cdk.StackProps {
  readonly network: NetworkIds
  readonly database: rds.DatabaseInstance
  /**
   * The database's security group, by id rather than as a construct.
   *
   * Passing the object and calling `addIngressRule` on it looks equivalent and is not: the rule is
   * created in the stack that *owns* the group, so DataStack would end up referencing this stack's
   * service group while this stack references DataStack — a dependency cycle CDK refuses to
   * synthesize. Importing the group here re-homes the rule into this stack, which is also where it
   * belongs conceptually: "the service may reach the database" is a fact about the service.
   */
  readonly databaseSecurityGroupId: string
  /** Which image the service runs. A deploy is this changing — never `latest`. */
  readonly imageTag: string
}

const CONTAINER_PORT = 8000

/**
 * How many database connections one task can hold: SQLAlchemy's defaults, `pool_size` 5 plus
 * `max_overflow` 10. One engine per process (AO5), so this is per task, not per request.
 */
const CONNECTIONS_PER_TASK = 15

/**
 * `db.t4g.medium` has 4 GiB, and RDS derives `max_connections` as `DBInstanceClassMemory/9531392`
 * — roughly 450. The reserve is for rotation, the migration task, and a human with `psql` during
 * an incident, which is the worst possible moment to discover there is no connection left.
 */
const DATABASE_MAX_CONNECTIONS = 450
const CONNECTIONS_RESERVED_FOR_OPERATORS = 90

/**
 * The ceiling on horizontal scaling — derived, not chosen, and that is the point.
 *
 * Scaling out ECS does not scale the database. Past this number the extra tasks add no throughput;
 * they exhaust the connection pool, and the failure mode is not slower responses but *refused
 * connections*, surfacing as errors on money movements that were perfectly valid. Raising this is
 * therefore a database sizing decision wearing a compute costume: change the instance class and
 * this number follows, never the other way round.
 */
const MAX_TASKS = Math.floor(
  (DATABASE_MAX_CONNECTIONS - CONNECTIONS_RESERVED_FOR_OPERATORS) / CONNECTIONS_PER_TASK,
)

/**
 * The service: ECR, the Fargate service behind an ALB, its autoscaling policy, and the one-off
 * task that applies migrations.
 */
export class ServiceStack extends cdk.Stack {
  public readonly repository: ecr.Repository
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer

  constructor(scope: Construct, id: string, props: ServiceStackProps) {
    super(scope, id, props)

    const vpc = importVpc(this, 'Vpc', props.network)
    const privateSubnets = props.network.privateSubnetIds.map((subnetId, index) =>
      ec2.Subnet.fromSubnetAttributes(this, `PrivateSubnet${index}`, {
        subnetId,
        availabilityZone:
          props.network.availabilityZones[index % props.network.availabilityZones.length],
      }),
    )
    const publicSubnets = props.network.publicSubnetIds.map((subnetId, index) =>
      ec2.Subnet.fromSubnetAttributes(this, `PublicSubnet${index}`, {
        subnetId,
        availabilityZone:
          props.network.availabilityZones[index % props.network.availabilityZones.length],
      }),
    )

    // ------------------------------------------------------------------ registry

    this.repository = new ecr.Repository(this, 'Repository', {
      repositoryName: 'account-balance',
      // Scanning on push, because the base image is somebody else's code running next to a ledger.
      imageScanOnPush: true,
      // Immutable tags: a deploy is identified by its tag, so a tag that can be moved means two
      // deploys can claim to be the same version and a rollback cannot be trusted to go back.
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      encryption: ecr.RepositoryEncryption.AES_256,
      lifecycleRules: [
        {
          description: 'Keep the last 30 images; older ones are not rollback targets anyone uses',
          maxImageCount: 30,
        },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    })

    const image = ecs.ContainerImage.fromEcrRepository(this.repository, props.imageTag)

    // ------------------------------------------------------------------ security groups

    const albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSecurityGroup', {
      vpc,
      description: 'account-balance ALB: public ingress on 443',
    })
    albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'HTTPS from the internet',
    )

    const serviceSecurityGroup = new ec2.SecurityGroup(this, 'ServiceSecurityGroup', {
      vpc,
      description: 'account-balance tasks: ingress only from the ALB',
    })
    // Not `anyIpv4` on the container port: the tasks sit in private subnets, but "unreachable by
    // routing" and "not allowed" are different guarantees, and only the second survives someone
    // later attaching a route.
    serviceSecurityGroup.addIngressRule(
      albSecurityGroup,
      ec2.Port.tcp(CONTAINER_PORT),
      'Application traffic from the load balancer only',
    )
    const databaseSecurityGroup = ec2.SecurityGroup.fromSecurityGroupId(
      this,
      'ImportedDatabaseSecurityGroup',
      props.databaseSecurityGroupId,
      { mutable: true },
    )
    databaseSecurityGroup.addIngressRule(
      serviceSecurityGroup,
      ec2.Port.tcp(5432),
      'PostgreSQL from the account-balance tasks only',
    )

    // ------------------------------------------------------------------ task definitions

    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: '/ecs/account-balance',
      // §11.4: a movement that cannot be reconstructed from its trace cannot be explained to a
      // customer. Retention here is that requirement expressed in days.
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    })

    const cluster = new ecs.Cluster(this, 'Cluster', {
      vpc,
      clusterName: 'account-balance',
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    })

    /**
     * The credentials reach the container as five separate values, one per JSON key, because that
     * is the only thing ECS can do with a secret: inject keys, never assemble them. The
     * application composes the URL itself
     * (`Settings._compose_database_url_from_parts`), which is what lets RDS rotate this secret
     * without a second, hand-written URL secret silently going stale behind it.
     */
    const databaseSecrets = (secret: rds.DatabaseSecret): Record<string, ecs.Secret> => ({
      DB_HOST: ecs.Secret.fromSecretsManager(secret, 'host'),
      DB_PORT: ecs.Secret.fromSecretsManager(secret, 'port'),
      DB_USER: ecs.Secret.fromSecretsManager(secret, 'username'),
      DB_PASSWORD: ecs.Secret.fromSecretsManager(secret, 'password'),
      DB_NAME: ecs.Secret.fromSecretsManager(secret, 'dbname'),
    })

    const secret = props.database.secret as rds.DatabaseSecret
    const secrets = databaseSecrets(secret)

    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDefinition', {
      cpu: 512,
      memoryLimitMiB: 1024,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64 },
    })
    taskDefinition.addContainer('api', {
      image,
      secrets,
      portMappings: [{ containerPort: CONTAINER_PORT }],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'api', logGroup }),
      // Container-level liveness, distinct from the ALB's: this one restarts a wedged task even
      // when the load balancer has already stopped sending it traffic.
      healthCheck: {
        command: ['CMD-SHELL', `python -c "import urllib.request;urllib.request.urlopen('http://localhost:${CONTAINER_PORT}/health')"`],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(30),
      },
    })
    // No explicit `grantRead`: passing `secrets` to `addContainer` already grants the execution
    // role exactly the keys named above, and a hand-written grant would only widen that.

    /**
     * Migrations, as their own task and never as a side effect of the service booting.
     *
     * The `runtime` image target deliberately has no migration step (see the repository's
     * `Dockerfile`): a schema that changes because a process happened to start is a change nobody
     * gated, applied at a moment nobody chose, concurrently by however many tasks scaled up at
     * once. Declaring it here makes it a step a deployment pipeline runs once, deliberately,
     * before shifting traffic — and one that can fail the deploy instead of half-migrating it.
     */
    const migrationTaskDefinition = new ecs.FargateTaskDefinition(this, 'MigrationTaskDefinition', {
      cpu: 256,
      memoryLimitMiB: 512,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64 },
    })
    migrationTaskDefinition.addContainer('migrate', {
      image,
      secrets,
      command: ['alembic', 'upgrade', 'head'],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'migrate', logGroup }),
    })
    // ------------------------------------------------------------------ service and ingress

    const service = new ecs.FargateService(this, 'Service', {
      cluster,
      taskDefinition,
      serviceName: 'account-balance',
      desiredCount: 2,
      securityGroups: [serviceSecurityGroup],
      vpcSubnets: { subnets: privateSubnets },
      // Rolling deploys with a circuit breaker: a bad image rolls itself back rather than sitting
      // half-deployed until somebody notices the error rate.
      circuitBreaker: { rollback: true },
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
    })

    this.loadBalancer = new elbv2.ApplicationLoadBalancer(this, 'LoadBalancer', {
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
      vpcSubnets: { subnets: publicSubnets },
    })

    const listener = this.loadBalancer.addListener('Listener', {
      port: 80,
      // HTTP here because no certificate exists in a placeholder account. A real deployment adds
      // an ACM certificate and redirects 80 to 443; the security group above already only admits
      // 443, so this is the one line that changes.
      protocol: elbv2.ApplicationProtocol.HTTP,
    })

    listener.addTargets('Api', {
      port: CONTAINER_PORT,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [service],
      healthCheck: {
        // `/health`, not `/ready`, on purpose -- see `health_routes.py`. The database is shared, so
        // pointing this at readiness would deregister every task at once during a single RDS
        // failover, turning a database blip into a total blackhole with nothing left in service to
        // even return an honest 503.
        path: '/health',
        healthyHttpCodes: '200',
        interval: cdk.Duration.seconds(15),
        timeout: cdk.Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
      // Long enough for an in-flight transfer to finish committing rather than being cut mid-
      // request; short enough that a deploy is not measured in coffee breaks.
      deregistrationDelay: cdk.Duration.seconds(30),
    })

    // ------------------------------------------------------------------ autoscaling

    const scaling = service.autoScaleTaskCount({ minCapacity: 2, maxCapacity: MAX_TASKS })

    /**
     * Requests per target is the primary signal, not CPU.
     *
     * This service is I/O-bound: a transfer spends its time waiting on a row lock in PostgreSQL,
     * not burning CPU. Under real contention the tasks look *idle* while latency climbs, so a
     * CPU-driven policy scales late, or not at all, exactly when it is needed.
     */
    scaling.scaleOnRequestCount('RequestScaling', {
      requestsPerTarget: 1000,
      targetGroup: listener.node.tryFindChild('ApiGroup') as elbv2.ApplicationTargetGroup,
      scaleInCooldown: cdk.Duration.minutes(5),
      scaleOutCooldown: cdk.Duration.minutes(1),
    })

    // CPU stays as a backstop for the case the request-rate signal misses: expensive requests at a
    // low rate. It is deliberately not the primary policy.
    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.minutes(5),
      scaleOutCooldown: cdk.Duration.minutes(1),
    })

    // ------------------------------------------------------------------ outputs

    new cdk.CfnOutput(this, 'ServiceUrl', {
      value: `http://${this.loadBalancer.loadBalancerDnsName}`,
      description: 'Public entry point (add ACM + HTTPS for a real deployment)',
    })
    new cdk.CfnOutput(this, 'RepositoryUri', {
      value: this.repository.repositoryUri,
      description: 'Push the image built from the Dockerfile `runtime` target here',
    })
    new cdk.CfnOutput(this, 'MigrationCommand', {
      value: [
        'aws ecs run-task',
        `--cluster ${cluster.clusterName}`,
        `--task-definition ${migrationTaskDefinition.family}`,
        '--launch-type FARGATE',
        `--network-configuration 'awsvpcConfiguration={subnets=[${props.network.privateSubnetIds.join(',')}],securityGroups=[${serviceSecurityGroup.securityGroupId}]}'`,
      ].join(' '),
      description: 'Run migrations before shifting traffic — the deploy step the runtime image refuses to do on boot',
    })
  }
}
