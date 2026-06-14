# Thiup infrastructure & CI (`ci/infra/ecs.yml`)

One CloudFormation stack that provisions the whole backend runtime **and** its
CodeBuild CI:

```
Internet → ALB (stable DNS) → Fargate ECS service (gunicorn)
                                   ├─ static/media served from S3 (public bucket)
                                   └─ momentum recompute: EventBridge Scheduler → ephemeral Fargate task (every 10 min)
CI: CodeBuild project (build image → collectstatic to S3 → deploy) — role fully permissioned
```

- Uses the account's **default VPC** + **2 public subnets** (no VPC/RDS created).
- **External PostgreSQL** (you provide host/credentials).
- **One backend secret** (`SecretKey`); `API_SECRET_KEY` is derived from it at boot.
- **No Celery/RabbitMQ** — momentum runs as an ephemeral scheduled task.

---

## ⚠️ READ THIS FIRST — GitHub is configured BY HAND

**CloudFormation does NOT and CANNOT configure the GitHub source/webhook in this
account.** The account's Organizations **SCP blocks** `codeconnections:PassConnection`
and `codeconnections:CreateWebhook`, so any attempt to bind a connection or create
a webhook from CFN fails with:

```
User is not authorized to access connection arn:aws:codeconnections:...:connection/...
```

(this fails **even as root**, which only happens with an SCP).

Therefore the template intentionally **omits** `Source.Auth` and `Triggers`. After
every stack creation you must do **two manual steps in the CodeBuild console**:

1. **Bind the GitHub connection** — Project `…-ci` → **Edit → Source** → Source
   provider **GitHub** → select the existing GitHub App connection
   (**“SirRiuz GH Conection”**, `connection/099f5062-…`, status *Available*) →
   Repository `https://github.com/SirRiuz/thiup_backend` → **Update**.
2. **Enable the webhook** — same screen, *Primary source webhook events* →
   **“Rebuild every time a code change is pushed”** → **Update**.

Connections console:
<https://us-east-1.console.aws.amazon.com/codesuite/settings/connections?region=us-east-1>

> **Drift caveat:** if you recreate the stack (new `…-vN`), the new `…-ci` project
> is born WITHOUT a working source binding — **redo the two steps above**. The
> CodeBuild **role already has** `codeconnections:UseConnection`/`GetConnectionToken`,
> so once bound, builds clone fine.

### Want it fully turnkey (zero manual steps)?
The only way is to have an Org admin **allow** `codeconnections:PassConnection`
and `codeconnections:CreateWebhook` in the account's SCP. Once allowed, re-add to
the `CodeBuildProject` Source: `Auth: { Type: CODECONNECTIONS, Resource: <arn> }`
and `Triggers: { Webhook: true, FilterGroups: [[{Type: EVENT, Pattern: PUSH}]] }`.

### "Connection not found" on a build?
The GitHub connection was deleted/recreated and the project still points at the
old ARN. Re-do step 1 (bind to the current connection). This affects **every**
project bound to the old connection (including older ones).

---

## Prerequisites

- A **default VPC** with at least **2 public subnets in different AZs** (the ALB
  requires two AZs).
- An **external PostgreSQL** reachable from the default VPC (its security group /
  firewall must allow `5432` from the tasks). If unreachable, the ALB target stays
  *unhealthy* (`/health/` does a `SELECT 1`).
- The **GitHub App connection** already authorized (Developer Tools → Settings →
  Connections), status *Available*.
- The **ECR repository** `thiup-backend` already exists (the stack does not create it).

## Deploy (CloudFormation console)

1. **Create stack → With new resources** → upload `ci/infra/ecs.yml`.
2. **Stack name**, e.g. `thiup-prod` (or `thiup-test-…`).
3. Fill parameters (see below). Acknowledge **IAM capabilities**. **Submit**.
4. On **CREATE_COMPLETE**, do the **two manual GitHub steps** above.
5. Trigger a build in `…-ci` (or push). Pipeline: **build → collectstatic → deploy**.
6. Open the app at the **`LoadBalancerURL`** output.

### Key parameters

| Parameter | Notes |
|---|---|
| `VpcId`, `PublicSubnet1`, `PublicSubnet2` | Default VPC + 2 subnets in **different AZs**. |
| `SecretKey` | The only backend secret (`API_SECRET_KEY` is derived). NoEcho. |
| `GatewaySeed` | **Required.** Public obfuscation; must match the frontend's `REACT_APP_GATEWAY_SEED`. |
| `InternalAdminUrl` | Obfuscated admin path, ends with `/`, not `admin/`. |
| `DatabaseHost/Name/User/Password/Port` | Your external Postgres. |
| `ImageTag` | Mutable tag the service runs (default `test`). |
| `MomentumScheduleExpression` | Default `rate(10 minutes)`. |
| `GitHubRepoUrl` | Repo URL (the connection itself is bound by hand). |

Static port is fixed at **8000** (not a parameter).

## CI pipeline (`buildspec.yml`)

- **pre_build:** ECR login.
- **build:** `ci/scripts/ecs-deploy build` → `docker build` (base image from **ECR
  Public**, avoids Docker Hub 429) → push `:${IMAGE_TAG}`.
- **post_build:** `ci/scripts/ecs-deploy collectstatic` (ephemeral run-task uploads
  static to S3) → `ci/scripts/ecs-deploy deploy` (force-new-deployment + wait).

`CLUSTER`/`SERVICE` are **auto-wired** to this stack via the project's env vars —
they can never go stale. `AWS_ACCOUNT_ID`/`AWS_DEFAULT_REGION` come from CodeBuild.

### CodeBuild role permissions (all included)
Logs · ECR auth + push/pull · `ecs:UpdateService/RunTask/DescribeServices/DescribeTasks/ListTasks`
· `iam:PassRole` (exec+task roles) · `ec2:DescribeNetworkInterfaces`
· `codeconnections:UseConnection/GetConnectionToken` (to clone once bound).

## Static & media (S3)

`USE_AWS_STORAGE=True` routes **static and media** to the **public-read** bucket
`…-assets-<account>`. The runtime container is gunicorn-only (no nginx/whitenoise),
so assets are served from S3 (`AWS_S3_CUSTOM_DOMAIN`), unsigned URLs. The app uses
the **task role** for S3 (no AWS keys in the task def).

## Momentum (For You ranking)

EventBridge Scheduler runs `python manage.py recompute_momentum` every 10 min as an
ephemeral Fargate task (same task definition, command overridden). Mutable image tag
+ Fargate fresh pull → each run uses the **latest** pushed image. No broker/worker.

## Teardown

⚠️ **Empty the S3 bucket first.** S3 won't delete a non-empty bucket and
CloudFormation won't empty it, so a stack delete **fails** on the bucket otherwise:

1. S3 → `…-assets-<account>` → **Empty**.
2. CloudFormation → **Delete stack**.

The **GitHub connection is NOT deleted** (account-level, reused). The ECR repo and
the external DB are untouched.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `DOWNLOAD_SOURCE`: *Connection … not found* | Old connection deleted — re-bind source (step 1). |
| `DOWNLOAD_SOURCE`: *Access denied to connection* | Role missing `UseConnection` (it's in the template — update the stack). |
| `CreateWebhook` / `PassConnection` *not authorized* | SCP blocks it — do the webhook by hand (step 2). |
| Build `429 Too Many Requests` pulling python image | Base image already uses ECR Public; if it recurs, it's a different pull. |
| ALB target **unhealthy** | DB unreachable (open `5432` from the VPC) or app boot env missing. Logs: `/ecs/<stack>-web`. |
| `collectstatic` `AccessDenied ecs:RunTask` | CodeBuild role — included in template; update the stack. |
