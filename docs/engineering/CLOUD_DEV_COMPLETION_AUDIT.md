# Cloud Dev Completion Audit

This audit records the current `dev` cloud completion state for StockBrief.
It intentionally excludes toolchain migration work and FE-to-BE integration
implementation because those are owned by other teammates.

Audit date: 2026-06-29
AWS account: `560271561793`
Region: `ap-northeast-2`
Linked issues: `#211`, `#226`

Do not paste API keys, access tokens, secret values, raw provider payloads, raw
model answers, or user emails into PR evidence. Use the redacted helper outputs
and summarize only status fields.

## Scope Boundary

| Area | Owner | Audit treatment |
| --- | --- | --- |
| BE cloud runtime | This PR | Verify and document current operational state. |
| Terraform dev backend | This PR | Verify state access, outputs, cost-sensitive toggles, and drift. |
| Live ingestion | This PR | Verify scheduler, egress, raw archive, run ledger, evidence rows, and DLQ state. |
| Bedrock explanation | This PR | Verify direct Bedrock and deployed `/v1/chat` safety path. |
| FE-BE connection implementation | Other teammate | Track as an external dependency only. |
| mise, uv, pnpm tooling migration | Other teammate | Treat as already completed and out of scope. |

## Current Status Summary

| Category | Status | Evidence | Next action |
| --- | --- | --- | --- |
| Latest main baseline | 완료 | BE `main` fast-forwarded to `b536afa`; FE `main` fast-forwarded to `f2d11e9`. | Start all new work from latest `main`. |
| Terraform state access | 완료 | `AWS_PROFILE=stockbrief-dev terraform init -reconfigure -backend-config=backends/dev.hcl -input=false` succeeded. | Keep using `backends/dev.hcl` for local dev state checks. |
| API Gateway and Lambda API | 완료 | `GET /v1/health` returned `status=ok`, `service=stockbrief-api`, `environment=dev`. | Continue using deployed smoke before release or after resume. |
| Recommendation API | 완료 | `GET /v1/recommendations/candidates?limit=3` returned `count=3`, first ticker `005930`, evidence level `medium`, evidence count `42`. | Re-run deployed API smoke after recommendation, ingestion, or Lambda deploy changes. |
| Cognito | 완료 | Terraform outputs include user pool `ap-northeast-2_VPOccT5rI`, issuer, app client, and Hosted UI domain; BE #225 verified the full protected API smoke with a short-lived token. | Re-run full hosted auth smoke after Cognito, callback, account API, or Amplify domain changes. |
| Amplify hosted pages | 완료 | Hosted page smoke for `/`, `/account`, and `/auth/callback` returned HTTP 200 at `https://main.d20hgo2k8atldu.amplifyapp.com`. | Re-run hosted page smoke after FE deploy or Amplify config changes. |
| RDS | 완료 | `stockbrief-dev-postgres` is `available`, PostgreSQL `16.13`, deletion protection `false`, backup retention `1`. | Stop RDS during inactive cost windows per `DEPLOYMENT_BOOTSTRAP.md`. |
| RDS Proxy | 완료 | Terraform output `rds_proxy_endpoint` is empty and `enable_rds_proxy=false`. | Keep disabled until Lambda concurrency requires pooling. |
| Bedrock direct provider | 완료 | `scripts/check_bedrock_chat_smoke.py` returned `ok=true`, model `apac.amazon.nova-micro-v1:0`, `matched_terms=[]`. | Keep AgentCore Runtime out of this phase. |
| Deployed chat explanation | 완료 | `POST /v1/chat` returned `success=true`, `bedrock Agent` response, `policy_action=ALLOW`, citation count `2`. | Re-run after Lambda, IAM, or Bedrock config changes. |
| Live ingestion readiness | 완료 | `scripts/check_ingestion_smoke.py` returned `ok=true`, `ready_for_manual_ingestion=true`, `scheduler_enable_ready=true`. | Manual provider ingest is not re-run in this audit to avoid unnecessary provider calls. |
| Ingestion scheduler | 완료 | Current AWS checks returned no provider ingestion schedules; the reviewed OpenDART and NAVER `005930` job definitions remain in tfvars while `enable_ingestion_scheduler=false`. | Re-enable only for a reviewed live ingestion window. |
| Ingestion ledger and evidence | 완료 | Status snapshot showed `started=0`, `succeeded=10`, `failed=0`, latest evidence count `10`. | Investigate only if future runs show stale `started` rows or failures. |
| DLQ | 완료 | SQS attributes showed `ApproximateNumberOfMessages=0`, not visible `0`, delayed `0`. | Check after every scheduler or manual ingestion smoke. |
| NAT and scheduler cost state | 완료 | Current read-only AWS checks returned no Terraform-managed NAT Gateway and no provider ingestion schedules; tfvars keep `enable_lambda_nat_egress=false` and `enable_ingestion_scheduler=false`. | Re-enable only when live provider ingestion work continues. |
| Terraform drift classification | 완료 | `scripts/check_dev_terraform_plan.sh` with the reviewed alarm recipient input exited `2` with `0 to add, 5 to change, 0 to destroy`; all five in-place changes are classified below. | Do not apply blindly; use the classified drift as the reviewed baseline before the next deploy PR. |
| Full hosted auth API smoke | 완료 | BE #225 ran the redacted full smoke with a short-lived token: `/v1/me`, `/v1/me/preferences`, `/v1/me/watchlist`, and `/v1/me/chat-sessions` all passed with `blockers=[]`; the temporary Cognito user was deleted. | Keep using redacted output only. Never paste bearer tokens, emails, watchlist bodies, chat titles, or raw responses. |
| FE live evidence visibility | 완료 | FE #104 added the hosted smoke and the post-merge run for ticker `005930` returned `ok=true` with `/` and `/stocks/005930` both HTTP 200. | Re-run after FE detail/recommendation UI, API base, or Amplify deploy changes. |

## Redacted Smoke Evidence

Run these from the BE repository root unless noted otherwise.

### API smoke

```bash
API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com"

curl -fsS "$API_BASE_URL/v1/health"
curl -fsS "$API_BASE_URL/v1/recommendations/candidates?limit=3"
curl -fsS -X POST "$API_BASE_URL/v1/chat" \
  -H 'Content-Type: application/json' \
  --data '{"ticker":"005930","message":"왜 추천됐나요?"}'
```

Evidence captured on 2026-06-29:

- `/v1/health`: `status=ok`, `service=stockbrief-api`, `environment=dev`
- `/v1/recommendations/candidates?limit=3`: `count=3`, first ticker `005930`,
  first evidence level `medium`, first evidence count `42`
- `/v1/chat`: `success=true`, provider message `bedrock Agent 응답을 반환했습니다.`,
  citation count `2`, safety policy action `ALLOW`

Do not paste the full chat answer into PRs. The deployed smoke should summarize
only response status, citation count, and safety policy fields.

### Bedrock direct smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_bedrock_chat_smoke.py \
  --model-id apac.amazon.nova-micro-v1:0 \
  --region ap-northeast-2
```

Evidence captured on 2026-06-29:

- `ok=true`
- `answer_length=44`
- `answer_sha256_prefix=246e9a43b265`
- `matched_terms=[]`

The helper intentionally hashes the answer and does not print the raw model
text.

### Hosted auth smoke

Page-only hosted smoke can run without a bearer token:

```bash
STOCKBRIEF_HOSTED_URL="https://main.d20hgo2k8atldu.amplifyapp.com" \
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com/v1" \
uv run python scripts/check_hosted_auth_smoke.py --skip-auth-api
```

Page-only evidence captured on 2026-06-29:

- `/`: HTTP 200
- `/account`: HTTP 200
- `/auth/callback`: HTTP 200
- `auth_token_configured=false`

Full API auth smoke requires a short-lived browser session token:

```bash
export STOCKBRIEF_AUTH_BEARER_TOKEN="REPLACE_WITH_SHORT_LIVED_TOKEN"
uv run python scripts/check_hosted_auth_smoke.py
```

BE #225 captured a full hosted auth API smoke on 2026-06-29 after the helper
was updated to accept both wrapped and top-level protected API response shapes:

- hosted pages `/`, `/account`, and `/auth/callback`: HTTP 200
- `/v1/me`: authenticated summary passed
- `/v1/me/preferences`: preference summary passed
- `/v1/me/watchlist`: item count summary passed
- `/v1/me/chat-sessions`: session count summary passed
- `blockers=[]`
- the temporary Cognito smoke user was deleted after the run

Only paste the redacted JSON result. Never paste the bearer token, email,
watchlist item body, chat title, or raw protected API response body.

### FE hosted live evidence smoke

Run this from the FE repository root after a hosted FE deploy, detail page
change, recommendation card change, or API base URL change:

```bash
STOCKBRIEF_HOSTED_URL="https://main.d20hgo2k8atldu.amplifyapp.com" \
pnpm run smoke:hosted-evidence -- --ticker 005930
```

Evidence captured after FE #104 merged on 2026-06-29:

- `ok=true`
- `/`: HTTP 200
- `/stocks/005930`: HTTP 200
- detail page summary included score, evidence section, evidence ID, published
  date, and source reference
- `blockers=[]`

### Ingestion smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_ingestion_smoke.py \
  --function-name stockbrief-dev-api \
  --providers OpenDART NAVER_NEWS \
  --tickers 005930 \
  --status-limit 10
```

Evidence captured on 2026-06-29:

- `ok=true`
- `ready_for_manual_ingestion=true`
- `scheduler_enable_ready=true`
- provider egress reachable:
  - OpenDART endpoint returned HTTP 200
  - NAVER_NEWS endpoint returned HTTP 400, which still confirms endpoint
    reachability for the unauthenticated egress probe
- status summary:
  - `started=0`
  - `succeeded=10`
  - `partial_failed=0`
  - `failed=0`
  - latest evidence count `10`
- stale run dry-run:
  - `stale_count=0`
  - `updated_count=0`

This audit did not run `--run-provider-ingest`; it verified readiness, current
ledger state, egress, raw archive write, and scheduler gate without creating a
new provider data run.

### DLQ and scheduler checks

```bash
AWS_PROFILE=stockbrief-dev \
aws sqs get-queue-attributes \
  --queue-url "https://sqs.ap-northeast-2.amazonaws.com/560271561793/stockbrief-dev-ingestion-dlq" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed \
  --region ap-northeast-2

AWS_PROFILE=stockbrief-dev \
aws scheduler list-schedules \
  --name-prefix stockbrief-dev-provider-ingestion \
  --region ap-northeast-2 \
  --query 'Schedules[].{Name:Name, State:State}'

AWS_PROFILE=stockbrief-dev \
aws ec2 describe-nat-gateways \
  --region ap-northeast-2 \
  --filter Name=tag:Name,Values=stockbrief-dev-lambda-nat \
  --query 'NatGateways[].{NatGatewayId:NatGatewayId,State:State}'
```

Evidence captured on 2026-06-29:

- DLQ visible messages: `0`
- DLQ not-visible messages: `0`
- DLQ delayed messages: `0`
- provider ingestion schedules: `[]`
- Terraform-managed NAT Gateways: `[]`
- `infra/terraform/envs/dev/deploy.auto.tfvars.json` still keeps the reviewed
  OpenDART and NAVER `005930` schedule definitions as reactivation inputs while
  `enable_ingestion_scheduler=false`

## Terraform Drift Classification

The local dev plan is not currently a no-change plan, but the current drift is
classified and does not include create or destroy actions:

```bash
OPERATIONAL_ALARM_EMAILS_JSON='["REDACTED_OPERATIONAL_EMAIL"]' \
scripts/check_dev_terraform_plan.sh
```

The lower-level equivalent command remains:

```bash
cd infra/terraform
export TF_VAR_operational_alarm_email_addresses='["REDACTED_OPERATIONAL_EMAIL"]'
terraform plan -var-file=envs/dev/deploy.auto.tfvars.json -detailed-exitcode -no-color
```

Observed result on 2026-06-29:

- Exit code `2`
- Plan summary: `0 to add, 5 to change, 0 to destroy`
- No NAT Gateway, EventBridge Scheduler, SNS topic, SNS subscription, or alarm
  action removal is planned.

| Plan item | Classification | Action rule |
| --- | --- | --- |
| Amplify app in-place update | Accepted provider/computed drift. JSON plan review showed no frontend environment variable change and no repository/callback change. | Accept as reviewed drift unless a future plan shows environment variable or repository changes. |
| Amplify branch in-place update | Accepted provider/computed drift. Branch identity and build settings remain unchanged. | Accept as reviewed drift unless a future plan shows branch name, framework, stage, or auto-build changes. |
| Cognito web client in-place update | Accepted provider/computed drift. Callback URLs, logout URLs, OAuth flow, OAuth scopes, explicit auth flows, and identity provider list remain unchanged. | Accept as reviewed drift unless a future plan changes auth URLs, token behavior, scopes, or identity providers. |
| RDS instance in-place update | Accepted provider/computed drift. Security group, storage, instance class, backup retention, deletion protection, skip final snapshot, public access, and Multi-AZ posture remain unchanged. | Accept as reviewed drift unless a future plan changes cost, deletion, backup, storage, network, or availability posture. |
| Lambda function in-place update | Expected package artifact drift. The helper rebuilds `dist/stockbrief-api-lambda.zip`, so `source_code_hash` differs from the deployed artifact. | Apply only in a reviewed deploy PR that intentionally ships the current backend package. |

Do not apply this plan as-is or as a blind repair step. Before the next
infrastructure apply, re-run `scripts/check_dev_terraform_plan.sh` with the
reviewed alarm recipient input and classify any new item that is not listed
above.

This #221 follow-up records the current reviewed Terraform drift baseline and
must not apply infrastructure changes.

NAT/scheduler cost posture decided in #214:

- Use `scripts/check_dev_terraform_plan.sh` with the reviewed operational alarm
  recipient input before every dev apply.
- Pause `enable_lambda_nat_egress` and `enable_ingestion_scheduler` while no
  live provider ingestion work is active.
- Keep the reviewed OpenDART/NAVER `005930` job definitions in tfvars as
  reactivation inputs.
- Re-enable NAT and scheduler only in a reviewed PR after provider egress,
  scheduler gate, DLQ, and recent manual ingestion evidence are refreshed.
- Treat remaining Amplify, Cognito, RDS, and Lambda package hash in-place drift
  as separate classification work. Do not fold those into the NAT/scheduler cost
  pause unless the PR body explains each item.

## Cost And Resume Decision

Current cost-sensitive state after #214 is applied:

- RDS is running and available.
- NAT Gateway is removed by Terraform unless a live provider ingestion window is
  active. The 2026-06-29 read-only AWS check returned an empty NAT Gateway list
  for `stockbrief-dev-lambda-nat`.
- Scheduler jobs remain defined in tfvars but are not created while
  `enable_ingestion_scheduler=false`. The 2026-06-29 read-only AWS check
  returned an empty provider ingestion schedule list.
- RDS Proxy is disabled.
- AgentCore Runtime is disabled.

Decision rule:

- If live provider ingestion development resumes, re-enable NAT and scheduler
  in a reviewed PR and re-check ingestion status plus DLQ after each scheduler
  window.
- If no live provider ingestion work remains, keep
  `enable_lambda_nat_egress=false` and `enable_ingestion_scheduler=false`.
- Do not delete Terraform-managed resources from the AWS console.

## Completion Gate For Next Feature Work

Product-flow feature development may resume when a new feature has its own
issue, branch, and review plan. The cloud completion gates below are closed for
the current dev baseline:

1. The original audit PR was reviewed and merged.
2. FE #104 merged the hosted live evidence visibility smoke.
3. BE #225 merged the full hosted auth API smoke helper fix and passed the
   short-lived-token smoke.
4. Terraform plan drift is explicitly accepted in the reviewed #223
   infrastructure documentation.
5. NAT/scheduler cost posture is intentionally chosen for the current work
   window: NAT and scheduler stay disabled unless live provider ingestion work
   resumes in a reviewed PR.

Candidate next product checks after those gates:

- live evidence visibility in FE recommendation/detail screens
- account watchlist/auth smoke
- recommendation candidate quality criteria
