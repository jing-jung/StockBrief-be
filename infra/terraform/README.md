# StockBrief Terraform Deployment Skeleton

This directory prepares the MVP deployment direction for AWS. It is a skeleton, not an instruction to create production resources immediately.

## Targets

- Frontend: AWS Amplify Hosting for the separate `StockBrief-fe` repository root.
- Backend: API Gateway HTTP API + Lambda running FastAPI through Mangum.
- DB: RDS PostgreSQL 16 target with optional RDS Proxy endpoint for Lambda.
- Secrets: AWS Secrets Manager.
- Auth: AWS Cognito User Pool with email-based signup/login and API Gateway JWT authorizer.
- Logs: CloudWatch log groups.
- Ingestion: S3 raw provider archive, SQS DLQ, and optional EventBridge Scheduler.
- Agent: optional Amazon Bedrock AgentCore Runtime direct deploy, enabled after an ECR agent image exists.
- IaC: Terraform modules with `dev`, `staging`, and `prod` variable examples.

MVP deployment priority is `dev`. `staging` and `prod` are documented through example tfvars only.

## Directory Layout

```text
infra/terraform
├── envs/
│   ├── dev/terraform.tfvars.example
│   ├── staging/terraform.tfvars.example
│   └── prod/terraform.tfvars.example
├── modules/
│   ├── agentcore_runtime/
│   ├── amplify/
│   ├── api_lambda/
│   ├── cloudwatch/
│   ├── cognito/
│   ├── rds/
│   └── secrets/
├── main.tf
├── ingestion.tf
├── variables.tf
├── outputs.tf
├── providers.tf
└── versions.tf
```

## Deployment Order

Do not run `terraform apply` until AWS account, networking, repository connection, and secrets process are confirmed.

For a new AWS account or environment, first run the one-time GitHub OIDC and
Terraform state bootstrap documented in
`docs/engineering/DEPLOYMENT_BOOTSTRAP.md`. The bootstrap creates the remote
state bucket, lock table, GitHub Actions OIDC provider, deploy role, and GitHub
Environment variables required by `.github/workflows/backend-dev-deploy.yml`.

1. Package the backend Lambda zip:

   ```bash
   ./scripts/package_api_lambda.sh
   ```

   The script defaults `LAMBDA_PYTHON_VERSION` to `3.13` to match `requires-python = ">=3.13"` in `pyproject.toml` and the `python3.13` Lambda runtime. It exports locked production requirements from `uv.lock`, packages third-party dependencies as Linux `x86_64-manylinux2014` wheels for the Lambda zip, then copies the backend `app/` package into the zip root. The zip is deterministic and includes regular files only; directory entries and symlinks are excluded by policy so Lambda packages do not depend on filesystem link behavior. If you need to target a different Lambda runtime or architecture, override the packaging variables explicitly:

   ```bash
   LAMBDA_PYTHON_VERSION=3.13 LAMBDA_PYTHON_PLATFORM=aarch64-manylinux2014 ./scripts/package_api_lambda.sh
   ```

2. Create a local dev tfvars file from the example:

   ```bash
   cp infra/terraform/envs/dev/terraform.tfvars.example infra/terraform/envs/dev/terraform.tfvars
   ```

3. Fill placeholders in `infra/terraform/envs/dev/terraform.tfvars`:

   - `enable_amplify`
   - `amplify_repository_url`
   - `db_subnet_ids`
   - `db_security_group_ids`
   - `lambda_subnet_ids`
   - `lambda_security_group_ids`
   - `vpc_endpoint_route_table_ids`
   - `enable_lambda_nat_egress`
   - `lambda_nat_public_subnet_id`
   - `lambda_nat_route_subnet_ids`
   - `cors_allowed_origins`
   - `cognito_callback_urls`
   - `cognito_logout_urls`
   - `cognito_hosted_ui_domain_prefix`
   - `amplify_cognito_redirect_uri`
   - `enable_ingestion_scheduler`
   - `ingestion_schedule_provider`
   - `ingestion_schedule_tickers`
   - `ingestion_schedule_jobs`

   For the first backend-only deployment, keep `enable_amplify = false`. Enable
   it only after the target GitHub organization approves the Amplify GitHub App.
   The current dev profile has Amplify enabled for the reviewed hosted FE URL.
   Keep the committed dev `deploy.auto.tfvars.json` on paused-cost defaults:
   `enable_ingestion_scheduler = false` and
   `enable_lambda_nat_egress = false`. GitHub Environment `dev` tfvars are the
   deploy-time source of truth for `backend-dev-deploy`. During the reviewed
   live ingestion work window, those Environment tfvars intentionally enabled
   NAT egress and scheduler. After #275, GitHub Environment `dev` is back on the
   paused defaults so EventBridge does not invoke unattended provider jobs and
   the NAT Gateway is no longer billable.
   The committed dev `deploy.auto.tfvars.json` tracks the current 560 account,
   hosted FE callback/logout URLs, and the reviewed provider schedules.

4. If deploying Amplify through Terraform, install the AWS Amplify GitHub App for
   the target region/account and provide a GitHub personal access token through
   an environment variable. Do not write the token into any tfvars file:

   ```bash
   export TF_VAR_amplify_access_token="<github-token>"
   ```

   For `ap-northeast-2`, install the GitHub App from:
   `https://github.com/apps/aws-amplify-ap-northeast-2/installations/new`

5. Initialize and validate Terraform. For a new AWS account, first run the
   bootstrap flow in `docs/engineering/NEW_AWS_BOOTSTRAP.md`, then replace the
   placeholder bucket in `infra/terraform/backend.tf` with the generated state
   bucket. If you are preparing another environment, update the backend
   bucket/key first and follow `docs/engineering/DEPLOYMENT_BOOTSTRAP.md`.

   ```bash
   cd infra/terraform
   terraform init
   terraform fmt -check -recursive
   terraform validate
   terraform plan -var-file=envs/dev/deploy.auto.tfvars.json
   ```

6. Review the plan. Apply only after placeholders, cost expectations, deletion protection, networking, and secret handling are approved:

   ```bash
   terraform apply -var-file=envs/dev/deploy.auto.tfvars.json
   ```

7. After resources exist, update Secrets Manager values outside git and redeploy Lambda/Amplify as needed.

## Security Group Rule Apply Review

Managed dev networking keeps the Security Group resources stable and manages
their ingress and egress entries through separate `aws_security_group_rule`
resources. If a plan follows the older inline-rule state, Terraform can show
rule deletes and creates in the same apply even though the Security Groups
themselves stay in place.

Expected plan shape during the inline-to-standalone rule transition:

```text
# aws_security_group.lambda[0] will be updated in-place
~ resource "aws_security_group" "lambda" {
    # inline egress block removed from state
  }

# aws_security_group_rule.lambda_https_egress[0] will be created
+ resource "aws_security_group_rule" "lambda_https_egress" {
    type        = "egress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
  }
```

The same pattern can appear for these managed rules:

- `aws_security_group_rule.lambda_https_egress`
- `aws_security_group_rule.lambda_database_egress`
- `aws_security_group_rule.rds_proxy_from_lambda`
- `aws_security_group_rule.rds_proxy_to_rds`
- `aws_security_group_rule.rds_from_managed_database_client`
- `aws_security_group_rule.secretsmanager_endpoint_from_lambda`

Review the plan before applying:

```bash
terraform plan -var-file=envs/dev/deploy.auto.tfvars.json
```

The acceptable change is a rule-only replacement: the existing
`aws_security_group.lambda[0]`, `aws_security_group.rds_proxy[0]`,
`aws_security_group.rds[0]`, and
`aws_security_group.secretsmanager_endpoint[0]` resources should remain
in-place. Do not apply if Terraform plans to replace a Security Group, subnet,
RDS instance, RDS Proxy, or VPC endpoint unless that larger replacement is
intentional and reviewed separately.

AWS does not guarantee that every Security Group rule replacement is ordered as
create-before-delete. Apply this transition only when brief database or
Secrets Manager connectivity loss is acceptable: a quiet dev period, or a
defined maintenance window for staging and production. After apply, verify that
the Lambda path can still reach RDS or RDS Proxy and Secrets Manager before
enabling scheduled ingestion.

## Terraform Backend Operations

Terraform backend settings are not normal variables. They are read during
`terraform init`, so the selected state bucket and key must be reviewed before
planning or applying.

Backend template:

| Setting | Value |
| --- | --- |
| Bucket | `stockbrief-terraform-state-<account-id>-ap-northeast-2` |
| Key | `stockbrief/dev/terraform.tfstate` |
| Region | `ap-northeast-2` |
| Lock table | `stockbrief-terraform-locks` |

Environment key convention:

| Environment | State key |
| --- | --- |
| `dev` | `stockbrief/dev/terraform.tfstate` |
| `dev-<member>` | `stockbrief/dev-<member>/terraform.tfstate` |
| `staging` | `stockbrief/staging/terraform.tfstate` |
| `prod` | `stockbrief/prod/terraform.tfstate` |

Store account-specific backend files under `backends/`:

```text
backends/dev.hcl
backends/dev-<member>.hcl
```

Use `backends/dev-template.hcl.example` and
`envs/dev-template/deploy.auto.tfvars.json.example` when onboarding another
team member's AWS account.
Commit account-specific files only when the team explicitly accepts exposing
non-secret AWS identifiers such as account ID, VPC ID, subnet ID, route table
ID, Cognito domain prefix, and Amplify domain in the repository. Otherwise keep
the real profile in an internal handoff location and leave only templates here.

Use `terraform init -reconfigure` when selecting a different backend that already
has the intended state or starts empty. Use `terraform init -migrate-state` only
when moving the same state to a new backend location. Before any apply after a
backend change, run:

```bash
terraform init -reconfigure -backend-config=backends/<target_env>.hcl
terraform state list
terraform plan -var-file=envs/<target_env>/deploy.auto.tfvars.json
```

For GitHub Actions, `backend-dev-deploy` resolves `target_env`, initializes
Terraform with `backends/<target_env>.hcl`, and plans with
`envs/<target_env>/deploy.auto.tfvars.json`. For account-specific profiles,
prefer GitHub Environment variables `TF_BACKEND_CONFIG_HCL` and `TFVARS_JSON`;
the workflow creates both files on the runner when they are not committed.
After a dev backend/account transition PR merges, run `backend-dev-deploy` and
record the success or expected guard failure on #52 before treating the deploy
role hardening work as complete.

## Lambda Packaging

Backend Lambda entrypoint:

```text
app/lambda_handler.py
```

Handler:

```text
app.lambda_handler.handler
```

Packaging script:

```bash
./scripts/package_api_lambda.sh
```

Output zip:

```text
dist/stockbrief-api-lambda.zip
```

The script installs Lambda-compatible third-party dependencies into `dist/lambda-api`, copies the backend `app/` package into the zip root, removes Python cache directories, normalizes timestamps, and zips regular files in sorted order with `zip -X`. It intentionally omits directory entries and symlinks. It does not include local `.env` files or secrets.

## Amplify Hosting

The Amplify module expects the connected repository to be `StockBrief-fe`, so `appRoot` is `.` and the build uses:

```bash
nvm install 24
nvm use 24
corepack enable
corepack prepare pnpm@11.7.0 --activate
node -v
pnpm install --frozen-lockfile --store-dir .pnpm-store
node -v
pnpm run build
```

Build output target:

```text
StockBrief-fe/.next
```

Required frontend environment variables:

```bash
NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_APP_NAME
NEXT_PUBLIC_COGNITO_REGION
NEXT_PUBLIC_COGNITO_USER_POOL_ID
NEXT_PUBLIC_COGNITO_APP_CLIENT_ID
NEXT_PUBLIC_COGNITO_HOSTED_UI_DOMAIN
NEXT_PUBLIC_COGNITO_REDIRECT_URI
```

`NEXT_PUBLIC_API_BASE_URL` is populated from the API Gateway output in
Terraform. `NEXT_PUBLIC_COGNITO_HOSTED_UI_DOMAIN` should use the
`cognito_hosted_ui_domain` Terraform output as-is, including the `https://`
scheme. The frontend normalizes Hosted UI domains with or without the scheme,
but manual Amplify variables and local `.env.local` files should follow the
Terraform output shape so account switching stays predictable.

For local development against a deployed dev stack, regenerate
`StockBrief-fe/.env.local` from the active backend outputs:

```bash
cd ../StockBrief-fe
pnpm run sync:dev-env -- --terraform-dir ../StockBrief-be/infra/terraform
```

The generated `.env.local` contains only public frontend values and must remain
outside git.

For Terraform-created Amplify apps, AWS requires the Amplify GitHub App to be installed and a GitHub access token to be supplied during app creation. Pass it through `TF_VAR_amplify_access_token`; do not commit it. After the app exists, Terraform ignores `access_token` drift so GitHub Actions can update the app without storing a personal GitHub token as a repository secret.

Amplify Hosted UI callback setup is intentionally two-step:

1. Enable Amplify and apply once to create the app and read
   `amplify_default_domain`.
2. Add the branch URL, for example
   `https://main.<amplify_default_domain>/auth/callback`, to
   `cognito_callback_urls`, add the matching account URL to
   `cognito_logout_urls`, set `amplify_cognito_redirect_uri` to the hosted
   callback URL, and apply again.

## Lambda Provider Egress

Lambda functions attached to a VPC do not receive public IP addresses, even
when their subnet route table has an Internet Gateway route. Real OpenDART and
Naver ingestion therefore needs an explicit outbound path.

Terraform can create the NAT Gateway path for the Lambda subnets in two modes.
If the target VPC already has a public subnet, supply that subnet:

```hcl
enable_lambda_nat_egress    = true
lambda_nat_public_subnet_id = "subnet-public-for-nat"
lambda_nat_route_subnet_ids = ["subnet-lambda-a", "subnet-lambda-b"]
```

If the target VPC has no usable public subnet, let Terraform create the public
subnet, public route table, and route to the VPC Internet Gateway:

```hcl
enable_lambda_nat_egress                  = true
lambda_nat_create_public_subnet           = true
lambda_nat_public_subnet_cidr_block       = "10.0.100.0/24"
lambda_nat_public_subnet_availability_zone = "ap-northeast-2a" # optional
lambda_nat_route_subnet_ids = [
  "subnet-lambda-private-a",
  "subnet-lambda-private-b",
]
```

When `lambda_nat_create_public_subnet=true`, direct Terraform runs can discover
the VPC Internet Gateway automatically. For `backend-dev-deploy`, make this
choice explicit: set `lambda_nat_create_internet_gateway=true` when Terraform
should create and attach an Internet Gateway, or set
`lambda_nat_internet_gateway_id` to a reviewed existing Internet Gateway. The
deploy profile validation fails early without one of those values so operators
do not have to infer the fix from Terraform's `no matching Internet Gateway
found` data-source error.

Troubleshooting:

- `lambda_nat_create_public_subnet=true` with no IGW in the VPC: set
  `lambda_nat_create_internet_gateway=true` and review the plan for the new
  Internet Gateway and public route.
- `lambda_nat_create_public_subnet=true` with an existing reviewed IGW: set
  `lambda_nat_internet_gateway_id` explicitly instead of relying on auto
  discovery in the GitHub deploy workflow.
- `no matching Internet Gateway found`: the VPC has no discoverable attached
  IGW for the NAT public subnet route. Re-run with one of the two explicit IGW
  settings above.

Committed paused-cost template settings:

```hcl
enable_lambda_nat_egress    = false
lambda_nat_public_subnet_id = "subnet-0c816842b11dfd2e7"
lambda_nat_route_subnet_ids = [
  "subnet-08d89333a3c3e2924",
  "subnet-0e10680a556fa9ca8",
]
```

The committed dev template keeps the reviewed NAT subnet IDs in tfvars for the
next live ingestion window, but #214 pauses NAT egress by default in the
repository template to stop NAT Gateway hourly charges while no live provider
work is running. The NAT public subnet is intentionally not included in
`lambda_nat_route_subnet_ids`.

Current ingestion cost state:

- GitHub Environment `dev` tfvars, not the committed paused template, are the
  deploy input for `backend-dev-deploy`.
- BE #252 and BE #254 intentionally enabled NAT egress and EventBridge
  Scheduler for the OpenDART/NAVER `005930` work window.
- #275 returned GitHub Environment `dev` `enable_lambda_nat_egress` and
  `enable_ingestion_scheduler` to `false`.
- `backend-dev-deploy` run `28574501920` applied the pause on 2026-07-02.
- The former NAT Gateway `nat-06de3faa3d9831ce4` is `deleted`, its Elastic IP
  allocation `eipalloc-099e616e0e7f6d2a1` is no longer found, and the
  `stockbrief-dev-provider-ingestion-*` schedules are absent.
- Keep the reviewed job definitions and NAT subnet IDs as reactivation inputs
  only; do not set either toggle back to `true` without a new reviewed live
  provider work window.

The public NAT subnet must keep a route to the VPC Internet Gateway. When
Terraform creates that public subnet, it also creates the public route table and
subnet association. The route subnet IDs are associated with a
Terraform-managed private route table whose default route points to the NAT
Gateway. Do not include the public NAT subnet in
`lambda_nat_route_subnet_ids`; Terraform preconditions fail the plan when those
inputs overlap. In existing-public-subnet mode, this means do not include
`lambda_nat_public_subnet_id` in `lambda_nat_route_subnet_ids`.

When the S3 raw archive Gateway endpoint is enabled, Terraform also attaches
that endpoint to the managed NAT route table. This keeps Lambda raw archive
writes on the S3 Gateway endpoint path after the Lambda private subnets move
from the original route table to the NAT route table. Review plans for this
expected `aws_vpc_endpoint.s3` route table update together with the NAT route
table associations.

Keep this disabled unless a live provider ingestion smoke test is scheduled.
When enabled, NAT Gateway hourly and data processing costs continue until the
toggle is set back to `false` and Terraform is applied.

## RDS And RDS Proxy

The RDS module creates PostgreSQL when `db_subnet_ids` are provided. RDS Proxy is
controlled by `enable_rds_proxy`. Keep it `false` for the first low-cost dev
bootstrap, then enable it when Lambda concurrency requires connection pooling.

When `enable_rds_proxy = true`, the `rds_proxy` module creates:

- `aws_db_proxy`
- `aws_db_proxy_default_target_group`
- `aws_db_proxy_target`

RDS Proxy uses the RDS-managed master user secret when an RDS instance exists.
Lambda receives:

- `DATABASE_SECRET_ARN`: secret containing either `DATABASE_URL` or RDS `username`/`password`
- `DATABASE_HOST`: RDS Proxy endpoint when enabled, otherwise the RDS endpoint
- `DATABASE_PORT`
- `DATABASE_NAME`
- `DATABASE_POOL_SIZE`: SQLAlchemy pool size, default `5`
- `DATABASE_MAX_OVERFLOW`: extra connections above pool size, default `10`
- `DATABASE_POOL_RECYCLE_SECONDS`: idle connection recycle window, default `1800`
- `DATABASE_POOL_TIMEOUT_SECONDS`: checkout timeout, default `30`

At runtime the backend uses `DATABASE_URL` directly when present in the secret.
Otherwise, it builds a PostgreSQL URL from `DATABASE_HOST` plus the secret's
`username` and `password`.

## Provider Ingestion

Before running a provider job or enabling the scheduler, call the readiness
operation from the same Lambda maintenance handler:

```json
{
  "stockbrief_operation": "check_ingestion_readiness"
}
```

The readiness response reports whether `INGESTION_RAW_BUCKET`,
`EXTERNAL_API_SECRET_ARN`, `OPENDART_API_KEY`, `NAVER_CLIENT_ID`,
`NAVER_CLIENT_SECRET`, and `KRX_API_KEY` are configured. KRX daily endpoints
default to the checked-in KRX API spec URLs and may be overridden through
`KRX_DAILY_URL`, `KRX_KOSPI_DAILY_URL`, or `KRX_KOSDAQ_DAILY_URL` in the
external API secret. Readiness reports presence only and does not return secret
values. It also does not call external provider APIs, so outbound internet
egress must still be verified separately before scheduled ingestion is enabled.

After readiness passes, verify outbound provider egress from the Lambda runtime:

```json
{
  "stockbrief_operation": "check_provider_egress",
  "providers": ["OpenDART", "NAVER_NEWS", "KRX"]
}
```

This operation sends unauthenticated HTTPS checks to the provider endpoints.
HTTP responses such as `401`, `403`, or provider validation errors still prove
network reachability; DNS, connection, and timeout failures keep
`outbound_internet_egress_verified` effectively false for scheduler enable
purposes. It does not send API keys or client secrets.

The backend Lambda can run provider ingestion and score refresh through the
same handler used for maintenance events:

```json
{
  "stockbrief_operation": "refresh_score_snapshots",
  "provider": "OpenDART",
  "tickers": ["005930"],
  "source_date": "2026-06-18"
}
```

Supported providers are `OpenDART`, `NAVER_NEWS`, and `KRX`. Each ticker run writes an
`ingestion_runs` row before provider access, computes a stable request hash, and
skips duplicate successful runs as `replayed`. The replay check uses the
normalized `input_hash`, so the same provider/ticker/source date/request
parameter set is replayed even if a later manual request uses a different
explicit `run_id`. A partial unique index on active or succeeded `input_hash`
values prevents concurrent first-run workers from creating multiple active
ledger rows for the same normalized input. Available provider responses are
stored in RDS using these first baseline upsert keys:

- OpenDART disclosures: `provider + receipt_no`
- NAVER news: `source_url`, with the source document keyed by `source_name + source_url_hash`
- KRX prices: `ticker + trade_date + source`
- Source documents: `source_name + external_id`, fallback `content_hash`

Manual and scheduled ingestion requests are rejected before provider calls when
they exceed these dev operational limits:

- `tickers`: max 20 per batch
- `page_count`: max 100
- `news_display`: max 50

Terraform creates these ingestion resources:

- S3 raw archive bucket when `enable_ingestion_raw_archive = true`
- Customer-managed KMS key for S3 raw archive SSE-KMS encryption
- S3 Gateway VPC Endpoint for the Lambda subnet route tables when managed
  networking and raw archive are enabled
- SQS DLQ with SQS-managed server-side encryption for failed scheduled invocations
- EventBridge Scheduler only when `enable_ingestion_scheduler = true` and
  either `ingestion_schedule_jobs` or the legacy `ingestion_schedule_tickers`
  input is non-empty

The Lambda receives `INGESTION_RAW_BUCKET` and `EXTERNAL_API_SECRET_ARN`.
External API secret values must be stored under:

- `OPENDART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `KRX_API_KEY`

Optional KRX endpoint overrides:

- `KRX_API_KEY_HEADER`
- `KRX_DAILY_URL` (legacy KOSPI override)
- `KRX_KOSPI_DAILY_URL`
- `KRX_KOSDAQ_DAILY_URL`

Keep the scheduler disabled for the first dev apply. Manually invoke
`refresh_score_snapshots` with a provider first, confirm `ingestion_runs`,
normalized rows, refreshed recommendation scores, S3 raw objects, and DLQ
behavior, then enable the scheduler in a separate reviewed PR.
If Lambda runs in private subnets, S3 raw archive requires the S3 Gateway VPC
Endpoint. Real external provider calls still require an outbound internet path
such as NAT or another approved egress design before enabling scheduled jobs.

### Scheduler Enable Gate

Do not set `enable_ingestion_scheduler = true` until all of these checks are
complete and recorded in the PR body:

- `OPENDART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, and
  `KRX_API_KEY` are populated in Secrets Manager outside git.
- A manual `refresh_score_snapshots` run succeeds for the target provider and
  ticker list without `missing_api_key` fallback, and refreshed score rows
  include provider freshness metadata.
- Lambda outbound internet egress to the selected provider is verified with
  `check_provider_egress` from the deployed Lambda environment. S3 Gateway VPC
  Endpoint only covers raw archive writes to S3; it does not provide internet
  egress for OpenDART, Naver, or KRX.
  If VPC Lambda egress is required, enable `enable_lambda_nat_egress` only for
  the smoke window and turn it off after the evidence is collected.
- S3 raw archive objects are written for the manual run and the SQS DLQ remains
  empty after the smoke test.
- The schedule expression, provider job list, and ticker list are reviewed for
  cost, rate-limit, and data freshness expectations. Scheduled provider jobs
  call `refresh_score_snapshots`, which runs provider ingestion and then
  materializes only succeeded or replayed tickers. Use
  `ingestion_schedule_jobs` for more than one provider; the legacy
  `ingestion_schedule_provider` and `ingestion_schedule_tickers` variables are
  used only when `ingestion_schedule_jobs` is empty.
  If the dev tfvars already contain reviewed scheduler jobs, keep those jobs as
  reviewed reactivation inputs. Keep `enable_ingestion_scheduler = false` until
  a reviewer-approved scheduler change says live provider runs should resume.

Current dev scheduler review evidence for issues #199/#200:

- `check_ingestion_smoke.py --providers OpenDART NAVER_NEWS --tickers 000660`
  returned `ready_for_manual_ingestion=true` and `scheduler_enable_ready=true`.
- Provider egress was reachable from Lambda: OpenDART returned HTTP 200 and
  NAVER_NEWS returned an HTTP 400 validation response for the unauthenticated
  probe.
- Manual `refresh_score_snapshots` succeeded for `000660`, source date
  `2026-06-26`, with no `missing_api_key` fallback.
- NAVER_NEWS inserted 10 normalized evidence rows. OpenDART completed with 0
  inserts for the selected disclosure date.
- Raw archive objects were written with SSE-KMS encryption for both providers.
- The ingestion DLQ remained empty after the live smoke.
- `docs/engineering/CLOUD_DEV_PLAN.md` is intentionally excluded from git by
  `docs/engineering/CLOUD_*_PLAN.md`; this tracked Terraform README is the
  PR-visible replacement for the #199 cloud plan evidence.
- 2026-06-26 scheduler reactivation plan evidence for #199/#200:
  `terraform plan -var-file=envs/dev/deploy.auto.tfvars.json` planned 6 scheduler additions:
  scheduler IAM role, scheduler invoke policy, two Lambda scheduler
  permissions, and two EventBridge schedules.
- NAT Gateway, NAT route table, Lambda route table associations, and the S3
  Gateway endpoint route table attachment were already present in Terraform
  state and were not new changes in this scheduler reactivation plan.
- The full plan also showed existing Amplify, Lambda package hash, Cognito
  client, and RDS in-place drift. Classify those drift items before apply; do
  not apply if any drift is unexplained or implies replacement, cost, deletion
  protection, backup, or networking changes outside this PR.
- After #214, the current dev profile intentionally keeps the reviewed
  OpenDART/NAVER `005930` job definitions in tfvars but sets
  `enable_ingestion_scheduler = false` and `enable_lambda_nat_egress = false`.
  This pauses unattended provider calls and NAT Gateway cost while preserving a
  clear reactivation path for the next live ingestion window.
- After #204, the post-merge ingestion status snapshot for `005930` showed
  recent OpenDART and NAVER runs as `succeeded`, latest normalized evidence
  available, and an empty ingestion DLQ. Re-check these same signals before
  expanding providers, tickers, or schedule frequency.

Reviewed dev scheduler jobs:

```json
[
  {
    "provider": "OpenDART",
    "tickers": ["005930"],
    "schedule_expression": "cron(0 18 ? * MON-FRI *)"
  },
  {
    "provider": "NAVER_NEWS",
    "tickers": ["005930"],
    "schedule_expression": "cron(5 18 ? * MON-FRI *)"
  }
]
```

If any check fails, keep the scheduler disabled and run ingestion manually until
the missing credential, network egress, or provider behavior is fixed.

## AgentCore Runtime

AgentCore Runtime is disabled by default because it requires a built agent container image in ECR.

Enable it after the `StockBriefAgent` image exists:

```hcl
agentcore_runtime_enabled       = true
agentcore_runtime_container_uri = "<account>.dkr.ecr.<region>.amazonaws.com/stockbrief-agent:<tag>"
agentcore_runtime_external_arn  = "" # filled from SSM/direct deploy
agentcore_runtime_external_id   = "" # filled from SSM/direct deploy
agentcore_runtime_endpoint_name = "" # filled from SSM/direct deploy
agentcore_runtime_manage_with_cloudformation = false
agentcore_network_mode          = "PUBLIC"
```

Terraform creates the IAM runtime role and log group. The runtime and endpoint
are created or updated by `scripts/deploy_agentcore_runtime.py` through the
Bedrock AgentCore control-plane API, then the script writes SSM metadata under
`/stockbrief/<target-env>/agentcore/*` and patches the runner tfvars file with:

- `agentcore_runtime_external_arn`
- `agentcore_runtime_external_id`
- `agentcore_runtime_endpoint_name`

The API Lambda role receives `bedrock-agentcore:InvokeAgentRuntime` only when
the runtime ARN exists. `backend-dev-deploy` runs the repository preflight before
Terraform init:

```bash
scripts/check_agentcore_runtime_preflight.sh \
  --terraform-dir infra/terraform \
  --var-file envs/<target-env>/deploy.auto.tfvars.json \
  --region ap-northeast-2
```

The preflight is a no-op when `agentcore_runtime_enabled=false`. When it is
enabled, the check confirms that the configured ECR image exists and is readable
by the deploy role. On apply, the workflow:

1. hydrates existing runtime metadata from SSM when present;
2. applies the base Terraform plan to create/update Lambda, IAM, logs, and data
   resources;
3. runs `scripts/deploy_agentcore_runtime.py` with the Terraform-created runtime
   role ARN;
4. re-plans and re-applies Terraform so Lambda env and invoke policy reference
   the direct-deployed runtime ARN.

The module still contains a legacy CloudFormation fallback for state
compatibility behind `agentcore_runtime_manage_with_cloudformation=true`, but
the reviewed GitHub Actions path keeps that false and does not create
`AWS::BedrockAgentCore::Runtime` or
`AWS::BedrockAgentCore::RuntimeEndpoint`. The fallback stack has
`prevent_destroy=true`; if an old environment has that stack in Terraform state,
review and remove state deliberately instead of letting a direct-deploy migration
delete it.

## Direct Bedrock Chat Provider

The backend examples default to `chat_provider = "mock"` so local smoke tests do
not call external AI services. To validate the direct Bedrock provider, set:

```hcl
chat_provider         = "bedrock"
bedrock_chat_model_id = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
bedrock_chat_region   = "" # empty uses aws_region
```

When `chat_provider = "bedrock"`, the API Lambda role receives
`bedrock:InvokeModel` only for the configured foundation model ARN or inference
profile ARN. Use an `apac.*` or `global.*` inference profile ID when the selected
model does not support on-demand invocation in the target region.

For inference profile IDs, the Lambda policy is split into two statements:

- the configured inference profile ARN can be invoked directly;
- the associated foundation model ARNs can be invoked only when the request
  context includes the configured `bedrock:InferenceProfileArn`.

The Claude target `apac.anthropic.claude-3-5-sonnet-20241022-v2:0` profile currently routes to
`ap-southeast-2`, `ap-northeast-1`, `ap-south-1`, `ap-northeast-2`,
`ap-southeast-1`, and `ap-northeast-3`. If you change
`bedrock_chat_model_id` to another inference profile, update
`bedrock_chat_inference_profile_foundation_model_regions` from
`aws bedrock get-inference-profile` before applying. Global profiles can require
different foundation model ARN patterns; add those entries through
`bedrock_chat_inference_profile_extra_foundation_model_arns` after verifying the
AWS profile routing list and IAM examples. Keep the provider on `mock` unless
Bedrock model access, expected request volume, and cost are approved for the
day's validation.

The reviewed Claude baseline for direct Bedrock and AgentCore validation is
`apac.anthropic.claude-3-5-sonnet-20241022-v2:0` in `ap-northeast-2`. Keep the
profile on `mock` or the prior approved model until the target account has
completed the Anthropic use-case access step and the redacted direct smoke below
returns `ok=true`.

Before switching the deployed API to `chat_provider = "bedrock"` or deploying an
AgentCore Runtime with this Claude model, verify that the active AWS account can
invoke the selected Bedrock model:

```bash
uv run python scripts/check_bedrock_chat_smoke.py \
  --region ap-northeast-2 \
  --model-id apac.anthropic.claude-3-5-sonnet-20241022-v2:0
```

The smoke command prints a redacted JSON result with `ok`, `answer_length`,
`answer_sha256_prefix`, and `matched_terms`. It does not print the raw model
answer, so the output can be attached to PR or deployment evidence without
copying model text into review comments. If the smoke fails, keep
`chat_provider = "mock"` and resolve Bedrock model access, region, or IAM before
changing Terraform variables.

After applying a profile that switches the deployed API to
`chat_provider = "bedrock"`, record `/v1/chat` live smoke evidence on the PR or
linked issue before closing the Bedrock enablement work:

```bash
STOCKBRIEF_API_BASE_URL="https://<api-id>.execute-api.ap-northeast-2.amazonaws.com" \
uv run python scripts/check_deployed_chat_smoke.py --ticker 005930
```

The deployed chat smoke prints redacted scenario results with policy action,
answer length, `answer_sha256_prefix`, citation count, citation IDs, disclaimer
presence, and matched guard terms. It does not print the raw answer or provider
body. The post-deploy smoke must confirm:

- the existing `/v1/chat` response contract is preserved, including `data.answer`,
  `data.citations`, `data.safety.policy_action`, and `data.safety.disclaimer`;
- the citation guard remains active, so Bedrock answers cannot return unsupported
  or invented evidence IDs;
- Bedrock runtime failures, empty answers, unsafe output, and citation guard
  failures remain fail-closed as `CHAT_PROVIDER_UNAVAILABLE` instead of falling
  back silently to mock output.

The #204 post-merge deployed `/v1/chat` evidence returned HTTP 200, preserved
the existing response contract, and returned only supported evidence IDs in the
answer citations. For future Bedrock model, IAM, prompt, or citation guard
changes, repeat both the redacted direct Bedrock smoke and the deployed
`/v1/chat` smoke before closing the linked issue.

## Secrets Manager

Secret values must be filled outside git. The placeholder secret names are:

| Secret name | Keys |
| --- | --- |
| `stockbrief-dev/database` | `DATABASE_URL` |
| `stockbrief-dev/external-api` | `OPENDART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `KRX_API_KEY`, optional KRX endpoint overrides |

These keys match `.env.example` secret-like variables:

- `DATABASE_URL`
- `DATABASE_SECRET_ARN` (Lambda runtime pointer to the database secret)
- `OPENDART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `KRX_API_KEY`
- `KRX_API_KEY_HEADER`
- `KRX_KOSPI_DAILY_URL`
- `KRX_KOSDAQ_DAILY_URL`

Non-secret runtime values remain Lambda or Amplify environment variables:

- `APP_ENV`
- `LOG_LEVEL`
- `SERVICE_NAME`
- `SERVICE_VERSION`
- `API_BASE_PATH`
- `DATABASE_SECRET_ARN`
- `CORS_ALLOWED_ORIGINS`
- `CHAT_PROVIDER`
- `BEDROCK_CHAT_MODEL_ID`
- `BEDROCK_CHAT_REGION`
- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_APP_NAME`
- `COGNITO_USER_POOL_ID`
- `COGNITO_APP_CLIENT_ID`
- `COGNITO_ISSUER`
- `COGNITO_JWKS_URL`
- `NEXT_PUBLIC_COGNITO_REGION`
- `NEXT_PUBLIC_COGNITO_USER_POOL_ID`
- `NEXT_PUBLIC_COGNITO_APP_CLIENT_ID`
- `NEXT_PUBLIC_COGNITO_HOSTED_UI_DOMAIN`
- `NEXT_PUBLIC_COGNITO_REDIRECT_URI`

### External API Credential Update Runbook

Use this runbook after Terraform creates `stockbrief-dev/external-api` and
before running live OpenDART, NAVER, or KRX ingestion. Never commit real API
keys, tokens, endpoints, or copied secret payloads.

1. Resolve the Terraform-managed external API secret ARN:

   ```bash
   cd infra/terraform
   terraform output -raw external_api_secret_arn
   ```

2. Do not paste provider credential values into shared logs, shell history, PR
   comments, or issue comments. Use the script prompt mode when entering real
   values manually.

3. Validate the payload locally without calling AWS:

   ```bash
   scripts/update_external_api_secret.sh --prompt --dry-run
   ```

4. Update the current Secrets Manager value without printing the payload:

   ```bash
   scripts/update_external_api_secret.sh --prompt
   ```

   The script resolves `external_api_secret_arn` from Terraform output by
   default, writes a temporary JSON payload outside git, passes it to
   `aws secretsmanager update-secret` with `file://`, and deletes the temporary
   payload automatically. The script applies the selected profile and region to
   Terraform state lookup through `AWS_PROFILE`, `AWS_REGION`, and
   `AWS_DEFAULT_REGION`, and passes `--profile`/`--region` to AWS Secrets
   Manager calls. Use `--secret-id` to skip Terraform state lookup entirely when
   needed.

5. Verify metadata only. Do not use `get-secret-value` in shared logs or PR
   evidence because it prints secret material. The script prints this metadata
   after a successful update; to re-check it manually:

   ```bash
   aws secretsmanager describe-secret \
     --secret-id "$(terraform output -raw external_api_secret_arn)" \
     --profile stockbrief-dev \
     --region ap-northeast-2
   ```

6. If you used environment variables instead of `--prompt`, remove the provider
   credentials from the shell session:

   ```bash
   unset OPENDART_API_KEY NAVER_CLIENT_ID NAVER_CLIENT_SECRET KRX_API_KEY
   ```

7. Run one manual Lambda score refresh per provider before enabling any
   scheduler. Replace `YYYY-MM-DD` with the business date you want to verify:

   ```bash
   aws lambda invoke \
     --function-name stockbrief-dev-api \
     --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"OpenDART","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
     --cli-binary-format raw-in-base64-out \
     /tmp/stockbrief-opendart-ingest-response.json \
     --profile stockbrief-dev \
     --region ap-northeast-2

   aws lambda invoke \
     --function-name stockbrief-dev-api \
     --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"NAVER_NEWS","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
     --cli-binary-format raw-in-base64-out \
     /tmp/stockbrief-naver-ingest-response.json \
     --profile stockbrief-dev \
     --region ap-northeast-2

   aws lambda invoke \
     --function-name stockbrief-dev-api \
     --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"KRX","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
     --cli-binary-format raw-in-base64-out \
     /tmp/stockbrief-krx-ingest-response.json \
     --profile stockbrief-dev \
     --region ap-northeast-2
   ```

8. Treat credentials as present only after the Lambda responses no longer report
   `missing_api_key` for `OPENDART_API_KEY` or
   `NAVER_CLIENT_ID/NAVER_CLIENT_SECRET` or `KRX_API_KEY`. Credential presence is necessary but
   not sufficient for live ingestion; the Lambda private subnet must also have
   outbound internet egress to reach OpenDART, NAVER, and KRX.

## Cognito And API Gateway JWT Authorizer

The Cognito module creates:

- User Pool with email as username.
- Required email verification.
- Public web app client without a client secret.
- Optional Hosted UI domain when `cognito_hosted_ui_domain_prefix` is set.

The API Lambda module creates explicit JWT-protected API Gateway routes for:

- `GET /v1/me`
- `PATCH /v1/me`
- `GET /v1/me/preferences`
- `PUT /v1/me/preferences`
- `GET /v1/me/watchlist`
- `POST /v1/me/watchlist`
- `PATCH /v1/me/watchlist/{ticker}`
- `DELETE /v1/me/watchlist/{ticker}`
- `POST /v1/me/watchlist/import`
- `GET /v1/me/chat-sessions`
- `POST /v1/me/chat-sessions`

Public routes continue through the unprotected proxy route so the guest-first MVP remains available.

## CloudWatch Log Group Naming

Log groups use the pattern:

```text
/aws/<service>/<project>-<env>-<component>
```

Current skeleton names:

| Component | Log group |
| --- | --- |
| API Lambda | `/aws/lambda/stockbrief-dev-api` |
| API Gateway | `/aws/apigateway/stockbrief-dev-http-api` |
| RDS PostgreSQL | `/aws/rds/stockbrief-dev-postgres` |
| Amplify Web | `/aws/amplify/stockbrief-dev-web` |

Default retention:

- API Lambda: 30 days
- API Gateway: 30 days
- RDS: 30 days
- Amplify: 14 days

## Operational Alarms

Terraform creates baseline CloudWatch alarms when `enable_operational_alarms`
is `true`:

| Component | Alarm |
| --- | --- |
| API Lambda | Error rate > 5% for 2 of 3 minutes |
| API Lambda | Any throttled invocation |
| API Lambda | p99 duration above 80% of timeout for 2 of 3 minutes |
| API Gateway HTTP API | Any 5xx response within 3 minutes |
| API Gateway HTTP API | p99 latency > 5 seconds for 2 of 3 minutes |
| RDS PostgreSQL | CPU utilization > 80% for 2 of 3 minutes |
| RDS PostgreSQL | Free storage below 2 GiB for 2 of 3 minutes |
| RDS Proxy | Database connection borrow latency > 1 second for 2 of 3 minutes |
| RDS Proxy | Any database connection setup failure |
| RDS Proxy | Any client authentication setup failure |

Set `operational_alarm_email_addresses` to subscribe email recipients through
SNS. Email subscriptions require each recipient to confirm the AWS SNS
subscription email before notifications are delivered. Leave the list empty to
create alarms without notification actions.

RDS Proxy alarms are created only when `enable_rds_proxy = true` and RDS subnets
are configured. The proxy alarms use the CloudWatch `AWS/RDS` namespace with the
`ProxyName` dimension. Some RDS Proxy metrics are not visible until after the
first successful proxy connection, so verify metric ingestion after a deployed
Lambda request actually connects through the proxy.

Operational alarm rollout checklist:

- Confirm every SNS email subscription is in `Confirmed` status before relying
  on notifications.
- Prefer a team or operations group alias over a personal email address for
  `operational_alarm_email_addresses`.
- Remember that email endpoints appear in Terraform plan and state metadata.
- For local dev drift checks, prefer the guarded helper so local plans use the
  same alarm recipient input shape as deploy:

  ```bash
  OPERATIONAL_ALARM_EMAILS_JSON='["REPLACE_WITH_ALERT_EMAIL"]' \
    scripts/check_dev_terraform_plan.sh
  ```

  The helper exports `TF_VAR_operational_alarm_email_addresses` before running
  `terraform plan -detailed-exitcode`. Without that input, a local plan can
  propose removing the SNS topic, email subscriptions, and alarm actions created
  by deploy. Use `--allow-empty-alarm-emails` only when notification removal is
  intentional and reviewed.
- After each dev/staging/prod apply, verify that API Gateway, Lambda, RDS, and
  RDS Proxy alarms show recent metric datapoints in CloudWatch.
- Record whether the default thresholds need tuning for the environment's real
  traffic profile before enabling the same values in production.

## GitHub Actions Dev Deployment

The dev backend deployment uses GitHub Actions OIDC instead of long-lived AWS
access keys. The `backend-dev-deploy` workflow runs on pushes to `main` and on
manual dispatch. Pushes to `main` deploy `target_env=dev`; manual dispatch can
choose another dev profile such as `dev-junwoo`. This workflow rejects
non-dev profiles; staging and production must use separate workflows and
approval policies.

Because the job uses `environment: <target_env>`, the IAM OIDC trust policy
expects the GitHub token subject to be:

```text
repo:80-hours-a-week/StockBrief-be:environment:<target_env>
```

Each GitHub Environment uses a custom deployment branch policy that allows only
the `main` branch. Keep that branch policy aligned with the IAM trust policy
whenever the workflow branch or environment name changes.

Bootstrap resources are generated per AWS account:

| Resource | Name |
| --- | --- |
| Terraform state bucket | `stockbrief-terraform-state-<account-id>-ap-northeast-2` |
| Terraform lock table | `stockbrief-terraform-locks` |
| GitHub OIDC provider | `token.actions.githubusercontent.com` |
| GitHub deploy role | `stockbrief-<target_env>-github-actions-deploy` |

Required GitHub Environment variables:

| Variable | Value |
| --- | --- |
| `AWS_<TARGET_ENV>_DEPLOY_ROLE_ARN` | Deploy role ARN printed by the bootstrap script |
| `OPERATIONAL_ALARM_EMAILS_JSON` | JSON list of alarm recipient emails |
| `TF_BACKEND_CONFIG_HCL` | Terraform backend HCL for the selected GitHub Environment |
| `TFVARS_JSON` | Terraform variable JSON for the selected GitHub Environment |

For rotating team AWS accounts, keep these values in the matching GitHub
Environment, for example `Settings > Environments > dev-junwoo > Environment
variables`. Do not put account-specific deploy role ARNs, backend config, or
tfvars in repository-level variables; global variables make it too easy to run
one team member's `target_env` against another team member's AWS account.

When building `TFVARS_JSON`, keep `amplify_cognito_redirect_uri` empty for the
console-managed Amplify flow unless Terraform creates Amplify for that
environment. Keep `agentcore_runtime_container_uri` empty unless
`agentcore_runtime_enabled` is true and an AgentCore ECR image URI is ready.
Leave `agentcore_runtime_external_arn`, `agentcore_runtime_external_id`, and
`agentcore_runtime_endpoint_name` empty for first deploy; the workflow hydrates
them from SSM when a runtime already exists and patches the runner tfvars after
direct deploy. If
`chat_provider` is `bedrock` or `agentcore` and `lambda_subnet_ids` attaches the
API Lambda to private subnets, either enable Terraform-managed NAT egress with
`enable_lambda_nat_egress=true` and reviewed NAT subnet IDs, set
`lambda_nat_create_public_subnet=true` with a non-overlapping public subnet
CIDR, or keep the NAT subnet IDs recorded in `TFVARS_JSON` so the existing
route-table egress can be reviewed before apply.

The workflow builds `dist/stockbrief-api-lambda.zip`, initializes Terraform with
the selected S3 backend config, plans with the selected tfvars file, applies the
base plan, direct-deploys AgentCore when enabled, then applies a second plan with
the direct runtime metadata. SSM metadata must contain
`runtime_arn`, `runtime_id`, and `endpoint_name` together; partial metadata fails
fast during hydrate so a previous interrupted deploy does not silently produce a
half-wired Lambda. If hydrate fails on incomplete metadata, verify the
AgentCore Runtime and endpoint in the AWS control plane, then rewrite all three
SSM parameters from the verified values or remove the stale set under a reviewed
cleanup before rerunning `backend-dev-deploy`.

`AWS_DEV_DEPLOY_ROLE_ARN` remains supported for the default `dev` profile only.
For rotating team accounts, use explicit Environment variables such as
`AWS_DEV_JUNWOO_DEPLOY_ROLE_ARN` inside the matching GitHub Environment. Enable
required reviewers on dev environments only if the team wants manual approval
before every apply.

## Current Limitations

- VPC, subnet, and security group modules are placeholders. Supply IDs through tfvars for now.
- Terraform creates placeholder Secrets Manager versions. Replace real values outside git.
- RDS resources are guarded by `subnet_ids`; empty subnet lists skip RDS creation in skeleton planning.
- This skeleton does not run migrations automatically. Use `StockBrief-be` Alembic commands after DB connectivity is confirmed.
- Cognito Hosted UI domain prefix must be globally unique when enabled.
- AgentCore Runtime direct deploy requires a valid container image URI, regional AgentCore/Bedrock model access, and deploy-role permissions for Bedrock AgentCore control-plane plus SSM metadata writes.
