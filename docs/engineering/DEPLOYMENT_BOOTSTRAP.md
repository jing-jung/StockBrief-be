# Deployment Bootstrap

This document explains how to prepare a new AWS account or environment so the
backend can deploy from GitHub Actions without long-lived AWS access keys.

The previous dev AWS account is no longer assumed to exist. Treat each new dev
environment as a fresh AWS account bootstrap unless the active state bucket,
lock table, and deploy role have been confirmed in the target account.

For the current reset path, follow `docs/engineering/NEW_AWS_BOOTSTRAP.md`
before running any Terraform plan or apply against real AWS resources.

## Why Bootstrap Is One-Time

GitHub Actions can deploy after it can assume an AWS IAM role through OIDC. The
first OIDC provider, IAM role, Terraform state bucket, and lock table cannot be
created by that role because it does not exist yet.

For a new AWS account, run the bootstrap script once with an administrator or
platform-admin AWS identity. After that, pushes to `main` can deploy through
GitHub Actions.

AWS recommends using an IAM OIDC provider and short-term role credentials for
GitHub Actions instead of storing long-lived IAM user keys. The provider URL must
be lowercase: `https://token.actions.githubusercontent.com`, and the audience is
`sts.amazonaws.com`.

## Prerequisites

- AWS CLI authenticated to the target AWS account.
- GitHub CLI authenticated with permission to write GitHub Environment variables.
- Permission to create or update IAM OIDC providers, IAM roles, S3 buckets, and
  DynamoDB tables in the target AWS account.
- Permission to set variables on `80-hours-a-week/StockBrief-be`.

Check the active AWS account before running:

```bash
aws sts get-caller-identity
```

Check GitHub CLI authentication:

```bash
gh auth status -h github.com
```

## Bootstrap Command

Run from the backend repository root:

```bash
scripts/bootstrap_github_oidc.sh \
  --environment dev \
  --region ap-northeast-2 \
  --github-owner 80-hours-a-week \
  --github-repo StockBrief-be \
  --dry-run \
  --alarm-emails-json '["REPLACE_WITH_ALERT_EMAIL"]'
```

Run without `--dry-run` only after reviewing the planned changes. Dry-run mode
keeps AWS and GitHub write actions as terminal logs. If existing GitHub
Environment branch policies would be removed, the script prints each obsolete
policy's branch name and policy ID before the delete step.

The script creates or updates:

- S3 remote Terraform state bucket.
- DynamoDB Terraform lock table.
- IAM OIDC provider for GitHub Actions.
- IAM deploy role scoped to `80-hours-a-week/StockBrief-be` `main`.
- GitHub Environment named `dev` with a custom deployment branch policy that
  allows only `main`.
- GitHub Environment variables:
  - `AWS_DEV_DEPLOY_ROLE_ARN`
  - `OPERATIONAL_ALARM_EMAILS_JSON`

The deploy role policy is intentionally broad enough for the current dev
Terraform deployment. Tighten it after the deployment surface stabilizes.

`--alarm-emails-json` must be valid JSON and must be an array of strings. The
script validates this with Python before writing the GitHub Environment variable.

## After Bootstrap

Make sure Terraform uses the state backend printed by the script. Terraform
backend configuration is evaluated during `terraform init`, before regular
variables are loaded, so the selected backend is an operational choice that must
be checked explicitly.

```hcl
terraform {
  backend "s3" {
    bucket         = "stockbrief-terraform-state-<account-id>-<region>"
    key            = "stockbrief/dev/terraform.tfstate"
    region         = "<region>"
    dynamodb_table = "stockbrief-terraform-locks"
    encrypt        = true
  }
}
```

Environment state convention:

| Environment | Backend key | Expected use |
| --- | --- | --- |
| `dev` | `stockbrief/dev/terraform.tfstate` | Current automated backend deployment |
| `staging` | `stockbrief/staging/terraform.tfstate` | Future pre-production environment |
| `prod` | `stockbrief/prod/terraform.tfstate` | Future production environment |

Use a separate account, bucket, or key per environment. Do not point staging or
prod at the dev state key.

When switching an existing local checkout to a different backend:

- Use `terraform init -reconfigure` when the target backend is empty or you are
  intentionally selecting a different existing state.
- Use `terraform init -migrate-state` only when moving the same state to a new
  backend location.
- Never run `terraform apply` after changing `backend.tf` until
  `terraform state list` shows the expected environment resources.

Then check the dev deploy variable file:

```text
infra/terraform/envs/dev/deploy.auto.tfvars.json
```

Confirm these values match the target AWS account:

- `aws_region`
- `vpc_id`
- `db_subnet_ids`
- `lambda_subnet_ids`
- `cors_allowed_origins`
- `cognito_callback_urls`
- `cognito_logout_urls`
- `cognito_hosted_ui_domain_prefix`

For the first backend-only deployment, keep this value:

```json
"enable_amplify": false
```

Amplify Hosting is managed from the AWS console. Backend resources, RDS, Lambda,
API Gateway, Cognito, Secrets Manager, and alarms are managed by Terraform.

## Deployment Flow After Bootstrap

1. Merge backend changes into `main`, or manually run `backend-dev-deploy`.
2. GitHub Actions resolves a dev deploy profile. Pushes to `main` use
   `target_env=dev`; manual runs can choose another dev profile such as
   `dev-junwoo`. This workflow accepts only `dev` or `dev-*`; staging and prod
   must use dedicated workflows.
3. The workflow runs in the GitHub Environment named after `target_env`.
4. The workflow assumes `AWS_<TARGET_ENV>_DEPLOY_ROLE_ARN` through OIDC. The
   legacy `AWS_DEV_DEPLOY_ROLE_ARN` fallback is allowed only for `target_env=dev`.
5. The workflow packages Lambda, initializes Terraform with
   `backends/<target_env>.hcl`, plans with
   `envs/<target_env>/deploy.auto.tfvars.json`, and applies the selected stack
   for pushes to `main`.
   If those profile files are not committed, the workflow creates them at
   runtime from the selected GitHub Environment variables
   `TF_BACKEND_CONFIG_HCL` and `TFVARS_JSON`.
6. Update Secrets Manager values outside git when keys or DB connection values
   change.

Manual workflow dispatch defaults to plan-only validation with `apply=false`.
Use this mode after account handoff, action version updates, deploy role policy
changes, or tfvars edits to verify OIDC, backend selection, Lambda packaging,
Terraform init, and Terraform plan without changing AWS resources. Re-run with
`apply=true` only after reviewing the plan and confirming the selected
`target_env` points at the intended dev account.

Because `backend-dev-deploy` uses the selected GitHub Environment, the OIDC
trust policy uses this subject pattern:

```text
repo:80-hours-a-week/StockBrief-be:environment:<target_env>
```

The branch restriction is enforced by the GitHub Environment deployment branch
policy. The bootstrap script configures the selected environment to allow only the
`main` branch.

The dev workflow uses profile files instead of editing `backend.tf` for every
handoff:

```text
infra/terraform/backends/<target_env>.hcl
infra/terraform/envs/<target_env>/deploy.auto.tfvars.json
```

Add a profile pair before a team member account is eligible for manual deploy.
For team member accounts, prefer storing the real profile body in GitHub
Environment variables and letting the workflow create those files at runtime.
Do not point two target environments at the same state key unless the team is
intentionally sharing the same Terraform state.

Before Terraform init, `backend-dev-deploy` compares the account assumed from
the resolved deploy role with the account encoded in the selected Terraform
state bucket name. The workflow stops immediately if those accounts differ, so
a deploy role variable update cannot accidentally deploy against a backend that
still belongs to another AWS account.
During account transition work, this failure is the expected guardrail when
`AWS_<TARGET_ENV>_DEPLOY_ROLE_ARN` points at one account but
`backends/<target_env>.hcl` still points at another state bucket. Treat the
failure as a configuration handoff signal, not as a deployment regression.

`AWS_<TARGET_ENV>_DEPLOY_ROLE_ARN` and `OPERATIONAL_ALARM_EMAILS_JSON` must live
in the matching GitHub Environment variables. Do not store team-specific deploy
role ARNs, backend config, tfvars, or alarm recipients in repository-level
variables. Add GitHub Environment required reviewers later if the team wants
manual approval before dev apply.

## Dev Cost Pause And Resume Runbook

Use this runbook when the dev AWS account must be paused overnight or during an
inactive review window. This is an operator action, not a Terraform replacement:
pause only runtime activity, keep Terraform state, IAM, Cognito, Secrets
Manager, and the state backend intact.

Before pausing, record the current target account and Terraform state:

```bash
aws sts get-caller-identity --profile stockbrief-dev

cd infra/terraform
terraform init -reconfigure -backend-config=backends/dev.hcl
terraform state list
terraform output api_base_url
terraform output ingestion_dlq_url
```

Pause checklist:

- Confirm no PR is waiting for a live AWS smoke test.
- Keep only reviewed `enable_ingestion_scheduler` jobs. Disable schedules before
  a long pause unless the team explicitly wants the reviewed provider jobs to
  keep running.
- Stop the dev RDS instance when database work is done:

  ```bash
  aws rds stop-db-instance \
    --db-instance-identifier stockbrief-dev-postgres \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

  RDS stop is a short-term pause control, not a permanent shutdown state. For a
  multi-day pause, re-check the DB status before the next billing window and
  stop it again if AWS has returned it to `available`.

- Block accidental API Lambda execution while the database is stopped:

  ```bash
  aws lambda put-function-concurrency \
    --function-name stockbrief-dev-api \
    --reserved-concurrent-executions 0 \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

- If Amplify preview or production-like web checks are not needed, disable
  branch auto build in the Amplify console. Do not delete the app unless the
  team has approved a full environment teardown.
- If `enable_lambda_nat_egress` was enabled for live provider ingestion, set it
  back to `false` and apply Terraform to remove the NAT Gateway and EIP:

  ```bash
  cd infra/terraform
  terraform apply -var-file=envs/dev/deploy.auto.tfvars.json
  ```

- Do not delete Terraform-managed resources from the AWS console. Console
  deletion creates drift that the next apply must repair or import.

Resume checklist:

- Start RDS and wait until it is available:

  ```bash
  aws rds start-db-instance \
    --db-instance-identifier stockbrief-dev-postgres \
    --profile stockbrief-dev \
    --region ap-northeast-2

  aws rds wait db-instance-available \
    --db-instance-identifier stockbrief-dev-postgres \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

- Restore normal Lambda execution:

  ```bash
  aws lambda delete-function-concurrency \
    --function-name stockbrief-dev-api \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

- Re-enable Amplify branch auto build only if frontend deploy validation is part
  of the day's work.
- Keep `enable_lambda_nat_egress = false` unless the day's work includes live
  OpenDART or NAVER ingestion. When it is enabled, run the provider smoke test
  and turn it off again before pausing the environment.
- Run a guarded Terraform plan before new infrastructure work. Use the same
  operational alarm recipient input that the deploy workflow receives through
  the GitHub Environment. The helper builds the Lambda package first, exports
  `TF_VAR_operational_alarm_email_addresses`, and then runs a detailed-exitcode
  plan:

  ```bash
  OPERATIONAL_ALARM_EMAILS_JSON='["REPLACE_WITH_ALERT_EMAIL"]' \
    scripts/check_dev_terraform_plan.sh
  ```

  If notification removal is intentional, pass
  `--allow-empty-alarm-emails` and record that choice in the PR. Do not apply a
  plan that removes the SNS topic, SNS subscriptions, or alarm actions unless
  that removal has been reviewed.

- Run smoke checks after RDS and Lambda are healthy:

  ```bash
  aws lambda invoke \
    --function-name stockbrief-dev-api \
    --payload '{"stockbrief_operation":"migrate"}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-migrate-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2

  curl -i "$(terraform output -raw api_base_url)/v1/recommendations/candidates?limit=3"
  ```

- After a hosted Cognito callback or Amplify domain change, verify the hosted
  auth path with a short-lived bearer token from the signed-in browser session:

  ```bash
  export STOCKBRIEF_HOSTED_URL="https://main.d20hgo2k8atldu.amplifyapp.com"
  export STOCKBRIEF_API_BASE_URL="$(terraform output -raw api_base_url)/v1"
  export STOCKBRIEF_AUTH_BEARER_TOKEN="REPLACE_WITH_SHORT_LIVED_TOKEN"

  uv run python scripts/check_hosted_auth_smoke.py
  ```

  The helper checks `/`, `/account`, `/auth/callback`, `GET /v1/me`,
  `GET /v1/me/preferences`, `GET /v1/me/watchlist`, and
  `GET /v1/me/chat-sessions`. PR evidence should include only the redacted JSON
  result. Do not paste the bearer token, email, or raw response body. Do not
  paste watchlist item bodies.

Current dev resume baseline:

- The active dev profile currently uses `chat_provider = "bedrock"` with
  `apac.amazon.nova-micro-v1:0`. After a resume that touches Lambda, IAM, or
  Bedrock settings, run the redacted direct smoke and deployed `/v1/chat` smoke
  before treating AI explanation work as healthy.
- After #214, the active dev profile keeps the reviewed OpenDART and NAVER job
  definitions for ticker `005930`, but NAT egress and EventBridge Scheduler are
  paused by default. Re-enable both only when the day's work includes live
  provider ingestion, then verify ingestion status for `005930`, confirm recent
  scheduler runs are `succeeded`, and confirm the DLQ remains empty.
- If the day's work does not require live provider ingestion, keep
  `enable_lambda_nat_egress = false` and `enable_ingestion_scheduler = false`.

If the no-change plan reports drift after a pause, stop and inspect the drift
before applying. Do not use `terraform apply` as a blind repair step.

### NAT Egress Plan Review Checklist

Before enabling `enable_lambda_nat_egress`, classify every Terraform plan change
that is not one of these expected NAT egress resources:

- NAT Gateway
- Elastic IP for the NAT Gateway
- NAT route table
- Lambda subnet route table associations
- S3 Gateway endpoint route table update that adds the NAT route table

Record the classification in the PR body before apply:

| Plan item | Required classification |
| --- | --- |
| Amplify in-place update | Confirm whether it is intentional frontend configuration drift. |
| Cognito client in-place update | Confirm callback/logout URL drift or planned auth setting change. |
| RDS in-place update | Confirm the change is not a cost, deletion, backup, or networking regression. |
| Lambda package hash update | Confirm it is caused by the current backend artifact build. |

Useful read-only checks:

The AWS CLI examples below use `dev` environment values. Before running them
for another target environment, adjust `--profile`, `--region`, and `Name` tag
values to match that environment's Terraform resources.

```bash
cd infra/terraform

terraform state show module.amplify.aws_amplify_app.this
terraform state show module.cognito.aws_cognito_user_pool_client.client
terraform state show module.rds.aws_db_instance.this
terraform state show module.api_lambda.aws_lambda_function.api

aws ec2 describe-nat-gateways \
  --filter "Name=tag:Name,Values=stockbrief-dev-lambda-egress-nat" \
  --profile stockbrief-dev \
  --region ap-northeast-2

aws ec2 describe-route-tables \
  --filters "Name=tag:Name,Values=stockbrief-dev-lambda-nat-egress-rt" \
  --profile stockbrief-dev \
  --region ap-northeast-2

aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<lambda_nat_public_subnet_id>" \
  --query 'RouteTables[].Routes[]' \
  --profile stockbrief-dev \
  --region ap-northeast-2

aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<lambda_nat_route_subnet_id_a>,<lambda_nat_route_subnet_id_b>" \
  --query 'RouteTables[].Associations[].SubnetId' \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

If any non-NAT item is unexplained, do not apply. Either update the PR with the
expected reason, split the unrelated change into its own PR, or restore the
drifted value before creating the NAT Gateway.

## Deploy Role Permission Model

The bootstrap script creates a GitHub Actions deploy role for Terraform-driven
backend changes. The role is separate from the administrator identity used to run
bootstrap:

- Bootstrap identity: creates the first state bucket, lock table, OIDC provider,
  deploy role, and GitHub Environment variables.
- Deploy role: assumed by GitHub Actions jobs that target the `dev` Environment.
  The `dev` Environment branch policy allows only `main` deployments.

The deploy role keeps Terraform state bucket and lock table permissions scoped
to exact ARNs. Service deployment actions are enumerated instead of using broad
service wildcards such as `lambda:*` or `iam:*`.

The bootstrap policy also splits actions with predictable StockBrief resource
names into resource-scoped statements. The role can manage matching
`stockbrief-<environment>-*` Lambda functions, IAM roles, CloudWatch alarms,
CloudWatch log groups, Secrets Manager secrets, SNS topics, SQS queues,
EventBridge Scheduler schedules, and the ingestion raw archive bucket. The
`iam:PassRole` permission is restricted to matching role names and the AWS
services that need those roles.

Some services remain in the wildcard fallback statement because Terraform needs
create, describe, or provider refresh APIs whose resource ARN is unknown before
creation or not consistently supported by the AWS API. This currently includes
API Gateway, Amplify, CloudFormation, Cognito, EC2 networking, KMS, RDS,
CloudWatch alarm reads, log group creation/listing, API Gateway access log
delivery registration, SNS subscription cleanup, and STS caller identity.
API Gateway stage creation can fail with an `apigateway:TagResource`
AccessDenied error when Terraform applies tags to the `$default` stage, but
Access Analyzer reports `apigateway:TagResource` and
`apigateway:UntagResource` as invalid IAM actions. Keep the API Gateway
management plane on `apigateway:*` in the fallback statement until AWS exposes
an Analyzer-valid narrower action set that still covers stage tagging.
HTTP API access logging also calls CloudWatch Logs delivery APIs such as
`logs:CreateLogDelivery`, `logs:PutResourcePolicy`, and
`logs:UpdateLogDelivery` while creating or updating
`aws_apigatewayv2_stage.default`. AWS documents these logging activation
permissions with `Resource: "*"`, so keep them in the fallback statement unless
AWS exposes resource-level support that still works with API Gateway HTTP API
access logs.

PR #164 covers only the apply blocker found after the new dev account
transition. It does not close #52 by itself. The `logs:TagResource` addition
stays in the wildcard fallback for the current unblock and must remain tracked
as a future narrowing candidate in #52. The
`DeployRdsManagedMasterUserSecret` statement is intentionally scoped to
`arn:aws:secretsmanager:<region>:<account-id>:secret:rds!db-*` because RDS
managed master user password secrets are created under AWS's `rds!db-*` naming
scheme instead of the StockBrief `stockbrief-<environment>-*` prefix.
The deploy role also includes a narrow `iam:CreateServiceLinkedRole` permission
for `AWSServiceRoleForRDS`; first-time DB instance creation in a new AWS account
can fail without that RDS service-linked role permission.

Terraform refresh also needs read permissions for every managed resource type.
When ingestion raw archive or provider egress resources are enabled, the deploy
role must be able to describe KMS keys, read S3 bucket public access/lifecycle
configuration, read SQS queue attributes, and inspect NAT Gateway/EIP address
attributes/route table state before it can safely plan. Terraform apply and
rollback paths for the raw archive must also be able to remove S3 bucket public
access block, encryption, and lifecycle configuration when those managed
resources are disabled or destroyed.

After changing Terraform-managed service permissions, re-run:

```bash
scripts/bootstrap_github_oidc.sh --dry-run --alarm-emails-json '["REPLACE_WITH_ALERT_EMAIL"]'
scripts/bootstrap_github_oidc.sh --alarm-emails-json '["REPLACE_WITH_ALERT_EMAIL"]'
```

Then verify the updated role with a real `backend-dev-deploy` workflow run. If
the workflow fails with `AccessDenied`, inspect the denied action and resource
before widening the wildcard fallback. Prefer adding a narrow
`stockbrief-<environment>-*` ARN statement when the AWS service supports it.
For policy edits, also validate the generated IAM policy with AWS Access
Analyzer and resolve `ERROR` findings before applying it to the deploy role.
After PR #164 merges, record on #52 that the bootstrap rerun updated the live
deploy role inline policy, the new account/backend `backend-dev-deploy` run
succeeded or failed with an expected guard, Terraform apply no longer fails on
`logs:TagResource`, `logs:CreateLogDelivery`, or `rds!db-*` Secrets Manager
permissions, and the `rds!db-*` exception remains part of the least-privilege
tracking rationale.
Keep the least-privilege hardening issue open until the bootstrap rerun and
`backend-dev-deploy` verification are complete, then record the result on that
issue before deciding whether it is done.

## New Environment Checklist

- Run the bootstrap script once in the target AWS account.
- Add or update `infra/terraform/backends/<target_env>.hcl` for that account and
  region.
- Add or update `infra/terraform/envs/<target_env>/deploy.auto.tfvars.json` for
  that network, cost posture, and frontend URL.
- Run `terraform init -reconfigure` or `terraform init -migrate-state` according
  to the backend change type.
- Confirm `terraform state list` points to the intended environment before
  applying.
- Confirm SNS alert email subscriptions after Terraform creates them.
- Fill secret values in AWS Secrets Manager. Do not commit secret values.
- Keep FE Amplify app setup in the console unless the team decides to manage
  Amplify with Terraform later.
