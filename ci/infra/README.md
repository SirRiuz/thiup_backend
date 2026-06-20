# Thiup infrastructure & CI (`ci/infra/ecs.yml`)

One CloudFormation stack that provisions the whole backend runtime **and** its
CodeBuild CI:

```
Internet → ALB (stable DNS) → Fargate ECS service (gunicorn)
                                   ├─ static/media served from an external S3-compatible bucket (Cloudflare R2)
                                   └─ momentum recompute: EventBridge Scheduler → ephemeral Fargate task (every 10 min)
CI: CodeBuild project (build image → collectstatic to the bucket → deploy) — role fully permissioned
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

Therefore the template declares the GitHub source (`Type: GITHUB`, the repo
`Location`, and **`BuildSpec: buildspec.yml`** so builds run the repo's buildspec),
but **omits `Auth` and `Triggers`**. After every stack creation, wire the two
SCP-blocked pieces **by hand in the CodeBuild console**:

1. **Connect GitHub** — Project `…-ci` → **Edit → Source** → provider **GitHub** →
   the account's **AWS managed GitHub App** credential (account-level; reused by
   every project). Repository: `https://github.com/SirRiuz/thiup_backend`.
2. **Enable the webhook** — same screen, *Primary source webhook events* →
   **“Rebuild every time a code change is pushed”** → **Update**.

Connections console:
<https://us-east-1.console.aws.amazon.com/codesuite/settings/connections?region=us-east-1>

> **Do NOT set `Source.Type: NO_SOURCE`** to "remove the URL": that forces an inline
> buildspec, so builds ignore `buildspec.yml` and run only the placeholder (a 14s
> "Succeeded" that deploys nothing). `GITHUB` + `BuildSpec: buildspec.yml` is what
> makes builds use the repo. The GitHub App credential is **account-level**, so a
> stack **Update** re-applying the GitHub source does not drop it — no reconnect.
> The webhook is the only piece to redo when you recreate the stack.

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
4. On **CREATE_COMPLETE**, do the **manual GitHub steps** above (connect GitHub + webhook).
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
| `StorageBucketName` / `StoragePublicDomain` | Bucket name + its public domain (R2). |
| `StorageEndpointUrl` | R2: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. |
| `StorageAccessKeyId` / `StorageSecretAccessKey` | R2 API token keys. NoEcho. |
| `ImageTag` | Mutable tag the service runs (default `test`). |
| `MomentumScheduleExpression` | Default `rate(10 minutes)`. |

Static port is fixed at **8000** (not a parameter).

## CI pipeline (`buildspec.yml`)

- **pre_build:** ECR login.
- **build:** `ecs-deploy build` → `docker build` (base image from **ECR Public**,
  avoids Docker Hub 429) → push **two** tags: `:${IMAGE_TAG}` (mutable, used for
  cache + the momentum scheduler) and `:${CODEBUILD_RESOLVED_SOURCE_VERSION}`
  (immutable per-commit).
- **post_build (in order):**
  1. `ecs-deploy register-task` — clone the service's task def, swap the image to
     the immutable tag, register a new revision (ARN saved to a temp file). The
     service keeps running the **old** image until step 5.
  2. `ecs-deploy collectstatic` — run-task on the **new** revision → static to the bucket.
  3. `ecs-deploy automigrate` — run-task on the **old** image: roll the DB back to
     the migration common to the DB and this branch (see below).
  4. `ecs-deploy migrate` — run-task on the **new** revision: apply this branch's
     migrations forward.
  5. `ecs-deploy deploy` — switch the service to the new revision, re-point the
     momentum schedule at the same revision, and wait.

`CLUSTER`/`SERVICE` are **auto-wired** to this stack via the project's env vars —
they can never go stale. `AWS_ACCOUNT_ID`/`AWS_DEFAULT_REGION` come from CodeBuild.

### Migrations & rollback
Why two image tags? To **reverse** a migration Django needs that migration's file.
The immutable per-commit tag keeps the previously deployed image addressable, so
`automigrate` runs `migrate app <ancestor>` on the **old** image (which still has
the files to reverse) while `migrate` runs forward on the **new** image.

`automigrate` is **gated**: it is a no-op unless the CodeBuild project has env var
**`AUTOMIGRATE_ROLLBACK=true`**. Set it **only on dev/test stacks** — rolling back
a migration drops whatever it created. With it off, deploys only migrate *forward*.

> **CFN drift:** a stack **Update** re-creates the task def pointing at the mutable
> `:${ImageTag}` and resets the momentum schedule to it; the next CodeBuild run
> re-registers an immutable revision and re-points the schedule. Between an update
> and the next deploy the schedule runs `:${ImageTag}` (still the latest image).

### CodeBuild role permissions (all included)
Logs (write own + read `/ecs/<stack>-web`) · ECR auth + push/pull ·
`ecs:UpdateService/RunTask/RegisterTaskDefinition/DescribeServices/DescribeTasks/DescribeTaskDefinition/ListTasks`
· `scheduler:GetSchedule/UpdateSchedule` (re-point the momentum cron) ·
`iam:PassRole` (exec+task roles to ECS, scheduler role to EventBridge) ·
`ec2:DescribeNetworkInterfaces` ·
`codeconnections:UseConnection/GetConnectionToken` (to clone once bound).

## Static & media (S3-compatible: AWS S3 or Cloudflare R2)

`USE_AWS_STORAGE=True` routes **static and media** to an external S3-compatible
bucket via `django-storages`/`S3Boto3Storage`. The runtime container is
gunicorn-only (no nginx/whitenoise), so assets are served from the bucket's public
domain (`AWS_S3_CUSTOM_DOMAIN`), unsigned URLs.

**The stack does NOT create the bucket** — you provide one. Provider is chosen by
config, no code change:

| Var | AWS S3 | Cloudflare R2 |
|---|---|---|
| `AWS_S3_ENDPOINT_URL` | *(empty)* | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `AWS_S3_REGION_NAME` | bucket region | `auto` (the settings default) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | optional (task role) | **required** (R2 has no IAM) |
| `AWS_STORAGE_BUCKET_NAME` / `AWS_S3_CUSTOM_DOMAIN` | bucket + its domain | R2 bucket + its public/r2.dev domain |

`settings.py` sends boto3 checksums only `when_required` (R2 rejects the defaults)
and never sets ACLs (`AWS_DEFAULT_ACL=None`) — R2 doesn't support them. The bucket
must be **publicly readable** on `AWS_S3_CUSTOM_DOMAIN` (unsigned URLs). These
values come from SSM (config) + Secrets Manager (the two keys), seeded by the
stack parameters — edit them in the console like any other var.

## Environment variables & secrets

App env vars are **not** in the image and **not** edited in `.env` (that's local
only). They live in AWS and are injected into the task at launch:

| Store | Holds | Examples |
|---|---|---|
| **SSM Parameter Store** (`/<stack>/<VAR>`) | non-secret config | `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_HOST/NAME/USER/PORT`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_CUSTOM_DOMAIN`, `AWS_S3_ENDPOINT_URL`, … |
| **Secrets Manager** (`/<stack>/<VAR>`) | secrets | `SECRET_KEY`, `DATABASE_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| **Task def `Environment`** (in `ecs.yml`) | stack-computed / may-be-empty | `SERVER_PORT`, `PGSSLMODE`, `USE_AWS_STORAGE`, `MEDIA_BASE_URL`, `CORS/CSRF` |

Console links are in the stack **Outputs**: `EnvConfigConsole` (SSM) and
`EnvSecretsConsole` (Secrets Manager).

**Golden rule:** changing env vars **never needs a build/recompile** — the image
isn't touched. New tasks read the values **at launch**, so after any change you
**force a new deployment** to apply it (ECS → Service → Update → ☑ *Force new
deployment*, or `aws ecs update-service --force-new-deployment`, or
`ci/scripts/ecs-deploy deploy`).

### Edit the value of an existing var → console only, NO CloudFormation
1. **Config** → Systems Manager → Parameter Store → `/<stack>/<VAR>` → Edit → Save.
   **Secret** → Secrets Manager → the secret → Retrieve value → Edit → Save.
2. **Force a new deployment.** Done. (No build, no stack update.)

> Editing a value in the console is NOT overwritten by an unrelated stack update
> (CFN only re-applies a parameter if its template value changed).

### Add a BRAND-NEW variable → create in console + 1 line + Update Stack
ECS injects only what the task def lists (no auto-discovery), so a new var must
be mapped once:
1. **Create** it in the console: secret → Secrets Manager; config → SSM, named
   `/<stack>/<VAR>`.
2. **Map it** in `ci/infra/ecs.yml`, one line under the container's `Secrets`:
   ```yaml
   - { Name: MY_NEW_VAR, ValueFrom: <secret-ARN or SSM-parameter-ARN> }
   ```
   (SSM ARN form: `arn:aws:ssm:<region>:<account>:parameter/<stack>/MY_NEW_VAR`.)
3. **Update Stack** → rolling deploy. (Still NOT a build.)

Permissions: the ExecutionRole already allows `ssm:GetParameters` on
`parameter/<stack>/*`, so new SSM params are covered automatically. New Secrets
Manager secrets need their ARN allowed for `secretsmanager:GetSecretValue` (the
role lists the secret ARNs — add the new one, or scope it to `secret:/<stack>/*`).

### Quick reference
| Action | CloudFormation? | Build? |
|---|---|---|
| Edit an existing value (config or secret) | ❌ no | ❌ no — just force-new-deployment |
| Just store a secret/param in the console | ❌ no | ❌ no |
| Make a NEW var reach the app | ✅ 1 line in task def + Update | ❌ no |
| Change app CODE | ✅/— deploy via CodeBuild | ✅ yes |

## Momentum (For You ranking)

EventBridge Scheduler runs `python manage.py recompute_momentum` every 10 min as an
ephemeral Fargate task (same task definition, command overridden). The `deploy` step
re-points the schedule at the **deployed immutable revision** (after `migrate`), so
the cron runs exactly the deployed, already-migrated code — no pre-migration window.
No broker/worker.

## Teardown

CloudFormation → **Delete stack**. The stack no longer owns an S3 bucket, so the
delete is clean.

The **storage bucket (R2/S3) is external** — the stack never created it, so its
assets are untouched. The **GitHub connection is NOT deleted** (account-level,
reused). The ECR repo and the external DB are untouched too.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `DOWNLOAD_SOURCE`: *Connection … not found* | Old connection deleted — re-bind source (step 1). |
| `DOWNLOAD_SOURCE`: *Access denied to connection* | Role missing `UseConnection` (it's in the template — update the stack). |
| `CreateWebhook` / `PassConnection` *not authorized* | SCP blocks it — do the webhook by hand (step 2). |
| Build `429 Too Many Requests` pulling python image | Base image already uses ECR Public; if it recurs, it's a different pull. |
| ALB target **unhealthy** | DB unreachable (open `5432` from the VPC) or app boot env missing. Logs: `/ecs/<stack>-web`. |
| `collectstatic` `AccessDenied ecs:RunTask` | CodeBuild role — included in template; update the stack. |
