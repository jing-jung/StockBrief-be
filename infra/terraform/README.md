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
- Agent: optional Amazon Bedrock AgentCore Runtime CloudFormation stack, enabled after an ECR agent image exists.
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
repository variables required by `.github/workflows/backend-dev-deploy.yml`.

1. Package the backend Lambda zip:

   ```bash
   ./scripts/package_api_lambda.sh
   ```

   The script defaults `PYTHON_BIN` to `python3.13` to match `requires-python = ">=3.13"` in `pyproject.toml` and the `python3.13` Lambda runtime. It packages third-party dependencies as Linux `manylinux2014_x86_64` wheels for the Lambda zip, then copies the backend `app/` package into the zip root. The zip is deterministic and includes regular files only; directory entries and symlinks are excluded by policy so Lambda packages do not depend on filesystem link behavior. If your system resolves a different interpreter as `python3.13`, override the variable explicitly:

   ```bash
   PYTHON_BIN=/usr/local/bin/python3.13 ./scripts/package_api_lambda.sh
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

   For the first backend-only deployment, keep `enable_amplify = false`. Enable
   it only after the target GitHub organization approves the Amplify GitHub App.
   Also keep `enable_ingestion_scheduler = false` until provider API credentials
   are stored in Secrets Manager and the target ticker list is reviewed.
   Keep `enable_lambda_nat_egress = false` until live provider ingestion is
   approved because NAT Gateway creates hourly and data processing charges.

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
| `staging` | `stockbrief/staging/terraform.tfstate` |
| `prod` | `stockbrief/prod/terraform.tfstate` |

Use `terraform init -reconfigure` when selecting a different backend that already
has the intended state or starts empty. Use `terraform init -migrate-state` only
when moving the same state to a new backend location. Before any apply after a
backend change, run:

```bash
terraform state list
terraform plan -var-file=envs/dev/deploy.auto.tfvars.json
```

For GitHub Actions, `backend-dev-deploy` initializes Terraform with the committed
`backend.tf` and `envs/dev/deploy.auto.tfvars.json`. Any environment expansion
must change backend config, tfvars, and workflow behavior together in one PR to
avoid applying one environment's variables to another environment's state.

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
npm ci
npm run build
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

`NEXT_PUBLIC_API_BASE_URL` is populated from the API Gateway output in Terraform. For local development, keep using `.env.example`.

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

Terraform can create a NAT Gateway path for the Lambda subnets only when all
three values are supplied:

```hcl
enable_lambda_nat_egress    = true
lambda_nat_public_subnet_id = "subnet-public-for-nat"
lambda_nat_route_subnet_ids = ["subnet-lambda-a", "subnet-lambda-b"]
```

Current dev live ingestion verification uses:

```hcl
enable_lambda_nat_egress    = true
lambda_nat_public_subnet_id = "subnet-0c816842b11dfd2e7"
lambda_nat_route_subnet_ids = [
  "subnet-08d89333a3c3e2924",
  "subnet-0e10680a556fa9ca8",
]
```

The public NAT subnet must keep a route to the VPC Internet Gateway. The route
subnet IDs are associated with a Terraform-managed private route table whose
default route points to the NAT Gateway. For the current dev account, choose a
public subnet that is not in `lambda_nat_route_subnet_ids` so the NAT Gateway
itself keeps direct Internet Gateway egress. Do not include
`lambda_nat_public_subnet_id` in `lambda_nat_route_subnet_ids`; Terraform
preconditions fail the plan when those inputs overlap.

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
`EXTERNAL_API_SECRET_ARN`, `OPENDART_API_KEY`, `NAVER_CLIENT_ID`, and
`NAVER_CLIENT_SECRET` are configured. It reports presence only and does not
return secret values. It also does not call external provider APIs, so outbound
internet egress must still be verified separately before scheduled ingestion is
enabled.

After readiness passes, verify outbound provider egress from the Lambda runtime:

```json
{
  "stockbrief_operation": "check_provider_egress",
  "providers": ["OpenDART", "NAVER_NEWS"]
}
```

This operation sends unauthenticated HTTPS checks to the provider endpoints.
HTTP responses such as `401`, `403`, or provider validation errors still prove
network reachability; DNS, connection, and timeout failures keep
`outbound_internet_egress_verified` effectively false for scheduler enable
purposes. It does not send API keys or client secrets.

The backend Lambda can run provider ingestion through the same handler used for
maintenance events:

```json
{
  "stockbrief_operation": "ingest_provider_batch",
  "provider": "OpenDART",
  "tickers": ["005930"],
  "source_date": "2026-06-18"
}
```

Supported providers are `OpenDART` and `NAVER_NEWS`. Each ticker run writes an
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
  `ingestion_schedule_tickers` is non-empty

The Lambda receives `INGESTION_RAW_BUCKET` and `EXTERNAL_API_SECRET_ARN`.
External API secret values must be stored under:

- `OPENDART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

Keep the scheduler disabled for the first dev apply. Manually invoke
`ingest_provider_batch` first, confirm `ingestion_runs`, normalized rows, S3 raw
objects, and DLQ behavior, then enable the scheduler in a separate reviewed PR.
If Lambda runs in private subnets, S3 raw archive requires the S3 Gateway VPC
Endpoint. Real external provider calls still require an outbound internet path
such as NAT or another approved egress design before enabling scheduled jobs.

### Scheduler Enable Gate

Do not set `enable_ingestion_scheduler = true` until all of these checks are
complete and recorded in the PR body:

- `OPENDART_API_KEY`, `NAVER_CLIENT_ID`, and `NAVER_CLIENT_SECRET` are populated
  in Secrets Manager outside git.
- A manual `ingest_provider_batch` run succeeds for the target provider and
  ticker list without `missing_api_key` fallback.
- Lambda outbound internet egress to the selected provider is verified with
  `check_provider_egress` from the deployed Lambda environment. S3 Gateway VPC
  Endpoint only covers raw archive writes to S3; it does not provide internet
  egress for OpenDART or Naver.
  If VPC Lambda egress is required, enable `enable_lambda_nat_egress` only for
  the smoke window and turn it off after the evidence is collected.
- S3 raw archive objects are written for the manual run and the SQS DLQ remains
  empty after the smoke test.
- The schedule expression, provider, and ticker list are reviewed for cost,
  rate-limit, and data freshness expectations.

If any check fails, keep the scheduler disabled and run ingestion manually until
the missing credential, network egress, or provider behavior is fixed.

## AgentCore Runtime

AgentCore Runtime is disabled by default because it requires a built agent container image in ECR.

Enable it after the `StockBriefAgent` image exists:

```hcl
agentcore_runtime_enabled       = true
agentcore_runtime_container_uri = "<account>.dkr.ecr.<region>.amazonaws.com/stockbrief-agent:<tag>"
agentcore_network_mode          = "PUBLIC"
```

The module creates an IAM runtime role and a CloudFormation stack containing:

- `AWS::BedrockAgentCore::Runtime`
- `AWS::BedrockAgentCore::RuntimeEndpoint`

The API Lambda role receives `bedrock-agentcore:InvokeAgentRuntime` only when the runtime ARN exists. Before applying, run the AgentCore preflight checks:

```bash
agentcore validate
agentcore deploy --dry-run
agentcore deploy --diff
```

## Secrets Manager

Secret values must be filled outside git. The placeholder secret names are:

| Secret name | Keys |
| --- | --- |
| `stockbrief-dev/database` | `DATABASE_URL` |
| `stockbrief-dev/external-api` | `OPENDART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `KRX_DATA_PATH` |

These keys match `.env.example` secret-like variables:

- `DATABASE_URL`
- `DATABASE_SECRET_ARN` (Lambda runtime pointer to the database secret)
- `OPENDART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `KRX_DATA_PATH`

Non-secret runtime values remain Lambda or Amplify environment variables:

- `APP_ENV`
- `LOG_LEVEL`
- `SERVICE_NAME`
- `SERVICE_VERSION`
- `API_BASE_PATH`
- `DATABASE_SECRET_ARN`
- `CORS_ALLOWED_ORIGINS`
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
before running live OpenDART or NAVER ingestion. Never commit real API keys,
tokens, or copied secret payloads.

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
   unset OPENDART_API_KEY NAVER_CLIENT_ID NAVER_CLIENT_SECRET KRX_DATA_PATH
   ```

7. Run one manual Lambda ingestion per provider before enabling any scheduler.
   Replace `YYYY-MM-DD` with the business date you want to verify:

   ```bash
   aws lambda invoke \
     --function-name stockbrief-dev-api \
     --payload '{"stockbrief_operation":"ingest_provider_batch","provider":"OpenDART","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
     --cli-binary-format raw-in-base64-out \
     /tmp/stockbrief-opendart-ingest-response.json \
     --profile stockbrief-dev \
     --region ap-northeast-2

   aws lambda invoke \
     --function-name stockbrief-dev-api \
     --payload '{"stockbrief_operation":"ingest_provider_batch","provider":"NAVER_NEWS","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
     --cli-binary-format raw-in-base64-out \
     /tmp/stockbrief-naver-ingest-response.json \
     --profile stockbrief-dev \
     --region ap-northeast-2
   ```

8. Treat credentials as present only after the Lambda responses no longer report
   `missing_api_key` for `OPENDART_API_KEY` or
   `NAVER_CLIENT_ID/NAVER_CLIENT_SECRET`. Credential presence is necessary but
   not sufficient for live ingestion; the Lambda private subnet must also have
   outbound internet egress to reach OpenDART and NAVER.

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

Set `operational_alarm_email_addresses` to subscribe email recipients through
SNS. Email subscriptions require each recipient to confirm the AWS SNS
subscription email before notifications are delivered. Leave the list empty to
create alarms without notification actions.

## GitHub Actions Dev Deployment

The dev backend deployment uses GitHub Actions OIDC instead of long-lived AWS
access keys. The `backend-dev-deploy` workflow runs on pushes to `main` and on
manual dispatch. The deploy job is attached to the GitHub Environment named
`dev`.

Because the job uses `environment: dev`, the IAM OIDC trust policy expects the
GitHub token subject to be:

```text
repo:80-hours-a-week/StockBrief-be:environment:dev
```

The `dev` GitHub Environment uses a custom deployment branch policy that allows
only the `main` branch. Keep that branch policy aligned with the IAM trust
policy whenever the workflow branch or environment name changes.

Bootstrap resources are generated per AWS account:

| Resource | Name |
| --- | --- |
| Terraform state bucket | `stockbrief-terraform-state-<account-id>-ap-northeast-2` |
| Terraform lock table | `stockbrief-terraform-locks` |
| GitHub OIDC provider | `token.actions.githubusercontent.com` |
| GitHub deploy role | `stockbrief-dev-github-actions-deploy` |

Required GitHub repository variables:

| Variable | Value |
| --- | --- |
| `AWS_DEV_DEPLOY_ROLE_ARN` | Deploy role ARN printed by the bootstrap script |
| `OPERATIONAL_ALARM_EMAILS_JSON` | JSON list of alarm recipient emails |

The workflow builds `dist/stockbrief-api-lambda.zip`, initializes Terraform with
the S3 backend, plans with `envs/dev/deploy.auto.tfvars.json`, and applies the
plan to dev.

`AWS_DEV_DEPLOY_ROLE_ARN` and `OPERATIONAL_ALARM_EMAILS_JSON` can remain
repository variables for the single dev environment. If staging or prod is added,
move each environment's values into the matching GitHub Environment variables.
Enable required reviewers on the `dev` environment only if the team wants manual
approval before every dev apply.

## Current Limitations

- VPC, subnet, and security group modules are placeholders. Supply IDs through tfvars for now.
- Terraform creates placeholder Secrets Manager versions. Replace real values outside git.
- RDS resources are guarded by `subnet_ids`; empty subnet lists skip RDS creation in skeleton planning.
- This skeleton does not run migrations automatically. Use `StockBrief-be` Alembic commands after DB connectivity is confirmed.
- Cognito Hosted UI domain prefix must be globally unique when enabled.
- AgentCore Runtime creation requires a valid container image URI and regional AgentCore/Bedrock model access.
