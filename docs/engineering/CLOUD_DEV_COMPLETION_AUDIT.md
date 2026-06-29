# Cloud Dev Completion Audit

This audit records the current `dev` cloud completion state for StockBrief.
It intentionally excludes toolchain migration work and FE-to-BE integration
implementation because those are owned by other teammates.

Audit date: 2026-06-29
AWS account: `560271561793`
Region: `ap-northeast-2`
Linked issue: `#211`

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
| Latest main baseline | 완료 | BE `main` fast-forwarded to `25a281b`; FE `main` fast-forwarded to `be3ca4e`. | Start all new work from latest `main`. |
| Terraform state access | 완료 | `AWS_PROFILE=stockbrief-dev terraform init -reconfigure -backend-config=backends/dev.hcl -input=false` succeeded. | Keep using `backends/dev.hcl` for local dev state checks. |
| API Gateway and Lambda API | 완료 | `GET /v1/health` returned `status=ok`, `service=stockbrief-api`, `environment=dev`. | Continue using deployed smoke before release or after resume. |
| Recommendation API | 완료 | `GET /v1/recommendations/candidates?limit=3` returned `count=3`, first ticker `005930`, evidence level `medium`, evidence count `42`. | FE display validation waits for the teammate-owned FE-BE connection work. |
| Cognito | 완료 | Terraform outputs include user pool `ap-northeast-2_VPOccT5rI`, issuer, app client, and Hosted UI domain. | Full hosted auth API smoke still needs a short-lived bearer token after a signed-in browser session. |
| Amplify hosted pages | 완료 | Hosted page smoke for `/`, `/account`, and `/auth/callback` returned HTTP 200 at `https://main.d20hgo2k8atldu.amplifyapp.com`. | FE-BE integration remains external to this PR. |
| RDS | 완료 | `stockbrief-dev-postgres` is `available`, PostgreSQL `16.13`, deletion protection `false`, backup retention `1`. | Stop RDS during inactive cost windows per `DEPLOYMENT_BOOTSTRAP.md`. |
| RDS Proxy | 완료 | Terraform output `rds_proxy_endpoint` is empty and `enable_rds_proxy=false`. | Keep disabled until Lambda concurrency requires pooling. |
| Bedrock direct provider | 완료 | `scripts/check_bedrock_chat_smoke.py` returned `ok=true`, model `apac.amazon.nova-micro-v1:0`, `matched_terms=[]`. | Keep AgentCore Runtime out of this phase. |
| Deployed chat explanation | 완료 | `POST /v1/chat` returned `success=true`, `bedrock Agent` response, `policy_action=ALLOW`, citation count `2`. | Re-run after Lambda, IAM, or Bedrock config changes. |
| Live ingestion readiness | 완료 | `scripts/check_ingestion_smoke.py` returned `ok=true`, `ready_for_manual_ingestion=true`, `scheduler_enable_ready=true`. | Manual provider ingest is not re-run in this audit to avoid unnecessary provider calls. |
| Ingestion scheduler | 완료 | The audit found EventBridge Scheduler jobs enabled for OpenDART and NAVER_NEWS on ticker `005930`; #214 pauses the scheduler while preserving reviewed job definitions in tfvars. | Re-enable only for a reviewed live ingestion window. |
| Ingestion ledger and evidence | 완료 | Status snapshot showed `started=0`, `succeeded=10`, `failed=0`, latest evidence count `10`. | Investigate only if future runs show stale `started` rows or failures. |
| DLQ | 완료 | SQS attributes showed `ApproximateNumberOfMessages=0`, not visible `0`, delayed `0`. | Check after every scheduler or manual ingestion smoke. |
| NAT cost state | 우리 후속 필요 | The audit found NAT Gateway `nat-0c302c1bf173385d2` available; #214 sets `enable_lambda_nat_egress=false` for the next reviewed apply. | Re-enable only when live provider ingestion work continues. |
| Terraform no-change plan | 우리 후속 필요 | `terraform plan -detailed-exitcode` exited `2`; do not apply as-is. | Re-run with the same operational alarm email input used by deploy, and with the intended Lambda package artifact, then classify any remaining drift. |
| Full hosted auth API smoke | 다른 팀원 담당 이후 재검증 | Page-only hosted smoke passed without `STOCKBRIEF_AUTH_BEARER_TOKEN`. | After FE auth callback work is merged, run full `check_hosted_auth_smoke.py` with a short-lived token and redact output. |
| FE detail/recommendation display | 다른 팀원 담당 | FE-BE connection implementation is explicitly out of this PR. | Resume product flow validation after that PR merges. |

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

Evidence captured on 2026-06-29:

- `/`: HTTP 200
- `/account`: HTTP 200
- `/auth/callback`: HTTP 200
- `auth_token_configured=false`

Full API auth smoke requires a short-lived browser session token:

```bash
export STOCKBRIEF_AUTH_BEARER_TOKEN="REPLACE_WITH_SHORT_LIVED_TOKEN"
uv run python scripts/check_hosted_auth_smoke.py
```

This checks `/v1/me`, `/v1/me/preferences`, `/v1/me/watchlist`, and
`/v1/me/chat-sessions`. Only paste the redacted JSON result. Never paste the
bearer token, email, watchlist item body, or raw protected API response body.

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
aws scheduler get-schedule \
  --name stockbrief-dev-provider-ingestion-opendart \
  --region ap-northeast-2 \
  --query '{Name:Name, State:State, ScheduleExpression:ScheduleExpression}'

AWS_PROFILE=stockbrief-dev \
aws scheduler get-schedule \
  --name stockbrief-dev-provider-ingestion-naver-news \
  --region ap-northeast-2 \
  --query '{Name:Name, State:State, ScheduleExpression:ScheduleExpression}'
```

Evidence captured on 2026-06-29:

- DLQ visible messages: `0`
- DLQ not-visible messages: `0`
- DLQ delayed messages: `0`
- OpenDART schedule: `ENABLED`, `cron(0 18 ? * MON-FRI *)`
- NAVER_NEWS schedule: `ENABLED`, `cron(5 18 ? * MON-FRI *)`

## Terraform Drift Finding

The local dev plan is not currently a no-change plan:

```bash
cd infra/terraform
AWS_PROFILE=stockbrief-dev \
terraform plan -var-file=envs/dev/deploy.auto.tfvars.json -detailed-exitcode -no-color
```

Observed result on 2026-06-29:

- Exit code `2`
- Plan summary: `0 to add, 12 to change, 2 to destroy`
- Planned destroy includes the operational alert SNS topic and its email
  subscription because local `envs/dev/deploy.auto.tfvars.json` does not carry
  `operational_alarm_email_addresses`.
- Planned alarm updates remove SNS actions from CloudWatch alarms for the same
  reason.
- Planned Lambda update includes a local package hash difference from the
  currently deployed artifact.

Do not apply this plan as-is. Before the next infrastructure apply:

1. Provide the reviewed operational alarm recipient list through the same
   non-git path used by deploy, or explicitly accept alarm notification removal
   in a separate reviewed PR.
2. Build the intended Lambda package artifact with `./scripts/package_api_lambda.sh`.
3. Re-run the plan and classify every remaining change in the PR body.
4. If NAT egress is no longer needed, remove it in a separate reviewed
   cost-control change instead of mixing it into this audit PR.

Track Terraform drift classification and NAT/scheduler cost posture in follow-up
issue `#214`; this audit PR records the current state and must not apply those
infrastructure changes.

Follow-up decision for #214:

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
  active.
- Scheduler jobs remain defined in tfvars but are not created while
  `enable_ingestion_scheduler=false`.
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

Move to product-flow feature development only after:

1. This audit PR is reviewed and merged.
2. The teammate-owned FE-BE connection PR is merged.
3. Full hosted auth API smoke passes with a short-lived token.
4. Terraform plan drift is either resolved or explicitly accepted in a reviewed
   infrastructure PR.
5. NAT/scheduler cost posture is intentionally chosen for the next work window.

Candidate next product checks after those gates:

- live evidence visibility in FE recommendation/detail screens
- account watchlist/auth smoke
- recommendation candidate quality criteria
