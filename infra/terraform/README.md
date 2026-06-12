# StockBrief Terraform Deployment Skeleton

This directory prepares the MVP deployment direction for AWS. It is a skeleton, not an instruction to create production resources immediately.

## Targets

- Frontend: AWS Amplify Hosting for the separate `StockBrief-fe` repository root.
- Backend: API Gateway HTTP API + Lambda running FastAPI through Mangum.
- DB: RDS PostgreSQL 16 target with optional RDS Proxy endpoint for Lambda.
- Secrets: AWS Secrets Manager.
- Auth: AWS Cognito User Pool with email-based signup/login and API Gateway JWT authorizer.
- Logs: CloudWatch log groups.
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
├── variables.tf
├── outputs.tf
├── providers.tf
└── versions.tf
```

## Deployment Order

Do not run `terraform apply` until AWS account, networking, repository connection, and secrets process are confirmed.

1. Package the backend Lambda zip:

   ```bash
   ./scripts/package_api_lambda.sh
   ```

   The script defaults `PYTHON_BIN` to `python3.13` to match `requires-python = ">=3.13"` in `pyproject.toml` and the `python3.13` Lambda runtime. It packages third-party dependencies as Linux `manylinux2014_x86_64` wheels for the Lambda zip, then copies the backend `app/` package into the zip root. If your system resolves a different interpreter as `python3.13`, override the variable explicitly:

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
   - `cors_allowed_origins`
   - `cognito_callback_urls`
   - `cognito_logout_urls`
   - `cognito_hosted_ui_domain_prefix`

   For the first backend-only deployment, keep `enable_amplify = false`. Enable
   it only after the target GitHub organization approves the Amplify GitHub App.

4. If deploying Amplify through Terraform, install the AWS Amplify GitHub App for
   the target region/account and provide a GitHub personal access token through
   an environment variable. Do not write the token into any tfvars file:

   ```bash
   export TF_VAR_amplify_access_token="<github-token>"
   ```

   For `ap-northeast-2`, install the GitHub App from:
   `https://github.com/apps/aws-amplify-ap-northeast-2/installations/new`

5. Initialize and validate Terraform:

   ```bash
   cd infra/terraform
   terraform init
   terraform fmt -check -recursive
   terraform validate
   terraform plan -var-file=envs/dev/terraform.tfvars
   ```

6. Review the plan. Apply only after placeholders, cost expectations, deletion protection, networking, and secret handling are approved:

   ```bash
   terraform apply -var-file=envs/dev/terraform.tfvars
   ```

7. After resources exist, update Secrets Manager values outside git and redeploy Lambda/Amplify as needed.

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

The script installs Lambda-compatible third-party dependencies into `dist/lambda-api`, copies the backend `app/` package into the zip root, removes Python cache directories, and zips the package. It does not include local `.env` files or secrets.

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

For Terraform-created Amplify apps, AWS requires the Amplify GitHub App to be installed and a GitHub access token to be supplied during app creation. Pass it through `TF_VAR_amplify_access_token`; do not commit it.

## RDS Proxy

The RDS module creates PostgreSQL when `db_subnet_ids` are provided. The `rds_proxy` module then creates:

- `aws_db_proxy`
- `aws_db_proxy_default_target_group`
- `aws_db_proxy_target`

RDS Proxy uses the RDS-managed master user secret when an RDS instance exists. Lambda receives:

- `DATABASE_SECRET_ARN`: secret containing either `DATABASE_URL` or RDS `username`/`password`
- `DATABASE_HOST`: RDS Proxy endpoint
- `DATABASE_PORT`
- `DATABASE_NAME`

At runtime the backend uses `DATABASE_URL` directly when present in the secret. Otherwise, it builds a PostgreSQL URL from `DATABASE_HOST` plus the secret's `username` and `password`.

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

## Current Limitations

- VPC, subnet, and security group modules are placeholders. Supply IDs through tfvars for now.
- Terraform creates placeholder Secrets Manager versions. Replace real values outside git.
- RDS resources are guarded by `subnet_ids`; empty subnet lists skip RDS creation in skeleton planning.
- This skeleton does not run migrations automatically. Use `StockBrief-be` Alembic commands after DB connectivity is confirmed.
- Cognito Hosted UI domain prefix must be globally unique when enabled.
- AgentCore Runtime creation requires a valid container image URI and regional AgentCore/Bedrock model access.
