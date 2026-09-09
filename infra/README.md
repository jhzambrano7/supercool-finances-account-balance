# Infrastructure — AWS CDK

What it takes to run this service in AWS: the database, the registry, the compute, and the way a
deploy actually happens.

```
npm install            # the CDK CLI only — AWS publishes none to PyPI
npx cdk synth          # works with no AWS account — see "Reading it without an account"
npx cdk deploy --all   # needs credentials, and real network ids
```

## Why CDK, in Python

**CDK over Terraform** because it is the tool actually known here. Infrastructure you cannot debug
under pressure is not infrastructure, it is a liability with nice syntax — and a take-home is a
poor place to learn a new IaC language in public.

**Python over TypeScript**, so the infrastructure is in the same language as the service it
deploys: the same `ruff`, the same strict `mypy`, one toolchain to learn instead of two, and a
reviewer who reads the service can read this without switching languages.

`aws-cdk-lib` lives in its own `infra` dependency group in the root `pyproject.toml`, deliberately
not in `dev`. `uv sync` does not install it and the image build (`uv sync --no-dev`) never sees it,
so putting the CDK app in this repository's toolchain costs the service nothing. The one npm
dependency is the CDK CLI itself, which AWS does not publish to PyPI.

## What is here

| Stack | Contains |
| --- | --- |
| `AccountBalanceData` | RDS PostgreSQL 16, Multi-AZ, encrypted, in isolated subnets; its generated and rotated Secrets Manager credentials; the database security group |
| `AccountBalanceService` | ECR repository; ECS cluster; the Fargate service behind an ALB; autoscaling; the migration runner that applies `alembic upgrade head` during deploy; log group; security groups |

**Two stacks, not one**, because the two halves have different lifecycles and different blast
radii. Compute rolls forward and back several times a day; the database holds the only copy of
every balance in the system. In one stack, a failed service deploy rolls back a template that also
owns the data, and `cdk destroy` on the thing you deploy daily reaches the thing you must never
destroy.

## The decisions worth reviewing

**Migrations run during `cdk deploy`, and gate the service.** The `runtime` image target
deliberately does not migrate on boot (see the repository's `Dockerfile`): a schema that changes
because a process happened to start is a change nobody gated, applied at a moment nobody chose,
concurrently by however many tasks scaled up at once.

That decision leaves a gap, and declaring a migration task definition does not close it — a
declared task that nothing invokes is the same as no migration at all, except that the template
looks like it has one. `MigrationRunner` (`stacks/migration_runner.py`) is a custom resource that
starts the task, polls it to completion, and **fails the deployment** if it exits non-zero. The ECS
service declares a dependency on it, so new tasks cannot start against a schema that was never
brought forward.

Two details make it behave under real conditions:

- **`imageTag` is a property of the custom resource.** CloudFormation only invokes a custom resource
  whose properties changed; without it, the second deploy of a new image would silently skip
  migrations — the same bug as never running them, and harder to notice because the first deploy
  worked.
- **The wait is a poll, not a long invocation.** CDK's `Provider` async pattern (`on_event` starts,
  `is_complete` polls) means Lambda's 15-minute ceiling is not something a table rewrite has to
  finish inside.

**Delete is a deliberate no-op.** There is no automated "down": rolling a schema back automatically
is how a rollback becomes data loss, and `cdk destroy` must not be the thing that decides to run
one.

In a repository with a CodePipeline this would usually be a pipeline stage instead. There is no
pipeline here, so putting it anywhere but the deployment itself would mean a deploy that is not a
deploy — a step a human has to remember, which is exactly the failure mode `docker-compose.yml`
already refuses locally.

**The scaling ceiling is derived from the database, not chosen.** `db.t4g.medium` gives roughly 450
connections; reserving 90 for rotation, migrations and a human with `psql` during an incident
leaves 360, and each task holds up to 15 (SQLAlchemy's `pool_size` 5 plus `max_overflow` 10, one
engine per process — AO5). That is 24 tasks.

Past that point, more tasks add no throughput — they exhaust the connection pool, and the failure
mode is not slower responses but *refused connections*, surfacing as errors on money movements that
were perfectly valid. Raising `maxCapacity` is a database sizing decision wearing a compute costume.

**Scaling is driven by request count, not CPU.** This service is I/O-bound: a transfer spends its
time waiting on a PostgreSQL row lock (PRD §5), not burning CPU. Under real contention the tasks
look *idle* while latency climbs, so a CPU-first policy scales late or not at all, exactly when it
is needed. CPU remains as a backstop for expensive requests at a low rate.

**The ALB health check points at `/health`, not `/ready`.** They answer different questions, and
readiness at the load balancer is the stricter-sounding, worse choice: the database is shared, so a
single RDS failover would deregister *every* task at once and leave nothing in service to return an
honest 503. See `health_routes.py` for the full argument.

**Credentials arrive as five values, not one URL.** ECS can inject one JSON key of a secret per
environment variable; it cannot assemble them. Since RDS generates and rotates that secret in a
fixed shape, the alternative is a second, hand-maintained URL secret that rotation silently
desynchronizes. The application composes the URL instead
(`Settings._compose_database_url_from_parts`), which is the version where rotation keeps working.

**ECR tags are immutable.** A tag that can be moved means two deploys can claim to be the same
version, and then a rollback is a guess rather than a return.

## Reading it without an account

`cdk synth` runs with no credentials and no `cdk.context.json`. That is deliberate: networking is
imported with `Vpc.fromVpcAttributes` (static) rather than `Vpc.fromLookup` (a live describe call),
so anyone can synthesize the templates and read exactly what would be created.

The placeholder ids in `lib/environment.ts` are obviously fake, and synth prints a warning naming
them. A real deployment substitutes them:

```
npx cdk deploy --all \
  -c vpcId=vpc-0abc… \
  -c availabilityZones=us-east-1a,us-east-1b \
  -c publicSubnetIds=subnet-…,subnet-… \
  -c privateSubnetIds=subnet-…,subnet-… \
  -c isolatedSubnetIds=subnet-…,subnet-… \
  -c imageTag=$(git rev-parse --short HEAD)
```

**The VPC is imported, never created**, for the same lifecycle reason the stacks are split: a VPC
is platform-team property measured in years, and `cdk destroy` on a service should not be able to
take the network with it.

## What is deliberately not here

- **The ops console is not deployed.** `web/` is a presentation facility with no tests, on purpose
  (`web/README.md`). Putting it in the infrastructure would make it something that has to be
  operated, monitored and secured — which is exactly what it was scoped out of being.
- **No HTTPS listener.** A placeholder account has no ACM certificate. The ALB security group
  already admits only 443; a real deployment adds the certificate and redirects 80, which is the
  one line that changes.
- **No pipeline.** CodePipeline is not modelled. Migrations do not depend on one — they run inside
  `cdk deploy` — but image build and push are still manual steps.
- **No metrics or alarms.** Observability is descoped (PRD §11, root `README.md`); `/health` and
  `/ready` exist because the load balancer cannot be created without them, not as a partial
  reversal of that decision.

## Deploy sequence

1. `docker build --target runtime -t <ecr-uri>:<tag> .` and push. The `runtime` target: non-root,
   no dev dependencies, no reloader, no migration step.
2. `npx cdk deploy AccountBalanceData` — first time only, or when the database changes.
3. `npx cdk deploy AccountBalanceService -c imageTag=<tag>`. Migrations run first, as part of the
   deploy, and a non-zero exit fails the stack rather than shipping code against a schema that
   never arrived. The circuit breaker then rolls back a bad image on its own rather than leaving it
   half-deployed.

The `MigrationTaskFamily` output names the task definition for the manual re-run an incident
occasionally needs — it is not part of the normal path.
