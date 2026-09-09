#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib'
import { DataStack } from '../lib/data-stack'
import { isPlaceholderNetwork, networkFromContext } from '../lib/environment'
import { ServiceStack } from '../lib/service-stack'

const app = new cdk.App()

const network = networkFromContext(app)

if (isPlaceholderNetwork(network)) {
  // Loud, and on stderr so it cannot be mistaken for part of the template. Synthesizing against
  // placeholders is the supported way to *read* this infrastructure; deploying against them is
  // not, and the difference should never be discovered at `cdk deploy` time.
  console.warn(
    '[account-balance] Synthesizing against PLACEHOLDER network ids. Pass real ones with\n' +
      '  cdk synth -c vpcId=vpc-… -c publicSubnetIds=… -c privateSubnetIds=… -c isolatedSubnetIds=…\n' +
      '  (see infra/README.md). Templates are valid to read, not to deploy.',
  )
}

/**
 * `env` is left unbound on purpose: these stacks are region-agnostic to synthesize, so a reader
 * with no AWS account still gets templates. A real deployment binds it through
 * `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION`, which the CLI populates from the active profile.
 */
const env =
  process.env.CDK_DEFAULT_ACCOUNT && process.env.CDK_DEFAULT_REGION
    ? { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION }
    : undefined

const data = new DataStack(app, 'AccountBalanceData', {
  env,
  network,
  description: 'account-balance: RDS PostgreSQL and its generated, rotated credentials',
})

const service = new ServiceStack(app, 'AccountBalanceService', {
  env,
  network,
  database: data.database,
  databaseSecurityGroupId: data.databaseSecurityGroup.securityGroupId,
  // A deploy *is* this value changing. Never `latest`: a moving tag makes a rollback a guess.
  imageTag: app.node.tryGetContext('imageTag') ?? 'latest',
  description: 'account-balance: ECR, ECS/Fargate behind an ALB, autoscaling, migration task',
})

// Explicit, not merely implied by the prop reference: the service cannot come up before the
// database it opens a connection pool against exists.
service.addStackDependency(data)

cdk.Tags.of(app).add('service', 'account-balance')
cdk.Tags.of(app).add('managed-by', 'cdk')
