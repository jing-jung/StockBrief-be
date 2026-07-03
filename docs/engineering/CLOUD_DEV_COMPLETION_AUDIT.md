# Cloud Dev Completion Audit

This audit records the current `dev` cloud completion state for StockBrief.
It intentionally excludes toolchain migration work and FE-to-BE integration
implementation because those were owned by other teammates.

Audit date: 2026-07-03
AWS account: `560271561793`
Region: `ap-northeast-2`
Linked issues: `#211`, `#226`, `#253`, `#255`, `#275`, `#284`, `#286`

Do not paste API keys, access tokens, secret values, raw provider payloads, raw
model answers, user emails, watchlist item bodies, or chat titles into PR
evidence. Use the redacted helper outputs and summarize only status fields.

## Scope Boundary

| Area | Owner | Audit treatment |
| --- | --- | --- |
| BE cloud runtime | This PR | Verify and document current operational state. |
| Terraform dev backend | This PR | Verify state access, outputs, cost-sensitive toggles, and deploy profile source. |
| Live ingestion | This PR | Verify scheduler, egress, raw archive, run ledger, evidence rows, and DLQ state. |
| Bedrock explanation | This PR | Verify direct Bedrock and deployed `/v1/chat` safety path. |
| FE-BE connection implementation | Other teammate | Track as an external dependency only. |
| mise, uv, pnpm tooling migration | Other teammate | Treat as already completed and out of scope. |

## Current Status Summary

| Category | Status | Evidence | Next action |
| --- | --- | --- | --- |
| Latest main baseline | 완료 | BE `main` is at `f156e4f` after BE #285; FE `main` is at `b103515` after FE #109. The latest hosted product smoke evidence remains the FE #118 search-page run. | Start all new BE work from latest `main`. |
| Deploy profile source | 완료 | BE #252 made GitHub Environment `TFVARS_JSON` and `TF_BACKEND_CONFIG_HCL` the source of truth for `backend-dev-deploy`. | Keep runner-rendered tfvars/backend files out of the repository. |
| Terraform apply | 완료 | `backend-dev-deploy` run `28560982229` applied NAT/scheduler resources for the live window; run `28561585744` deployed the #254 Lambda package; run `28574501920` applied the #275 cost pause. | Inspect the deploy run after every merge or Environment tfvars change. |
| API Gateway and Lambda API | 완료 | `GET /v1/health` returned `status=ok`, `service=stockbrief-api`, `environment=dev`. | Continue using deployed smoke before release or after resume. |
| Recommendation API | 완료 | `GET /v1/recommendations/candidates?limit=3` returned `count=3`, first ticker `005930`, evidence level `medium`, evidence count `42`. | Re-run deployed API smoke after recommendation, ingestion, or Lambda deploy changes. |
| Recommendation quality | 완료 | `scripts/check_recommendation_quality_smoke.py --limit 3 --max-detail-tickers 3` returned `ok=true`; selected tickers `005930`, `207940`, `000660`; each detail returned 8 score components with weight sum `100`; blockers `[]`. | Re-run before product-flow work that changes candidate quality or evidence joins. |
| Cognito | 완료 | Terraform outputs include user pool `ap-northeast-2_VPOccT5rI`, issuer, app client, and Hosted UI domain; BE #225 verified the full protected API smoke with a short-lived token. | Re-run full hosted auth smoke after Cognito, callback, account API, or Amplify domain changes. |
| Amplify hosted pages | 완료 | Page-only hosted smoke for `/`, `/account`, and `/auth/callback` returned HTTP 200 at `https://main.d20hgo2k8atldu.amplifyapp.com`. | Re-run hosted page smoke after FE deploy or Amplify config changes. |
| FE live evidence visibility | 완료 | FE hosted evidence/watchlist/auth/search-page smoke returned `ok=true` with `/`, `/stocks/005930`, `/search?q=삼성전자`, `/watchlist`, `/account`, and `/auth/callback` all HTTP 200 and no missing page markers. | Re-run after FE detail/recommendation/search/watchlist/account UI, auth callback, API base, or Amplify deploy changes. |
| RDS | 완료 | `stockbrief-dev-postgres` is available, PostgreSQL `16.13`, deletion protection `false`, backup retention `1`. | Stop RDS during inactive cost windows per `DEPLOYMENT_BOOTSTRAP.md`. |
| RDS Proxy | 완료 | Terraform output `rds_proxy_endpoint` is empty and `enable_rds_proxy=false`. | Keep disabled until Lambda concurrency requires pooling. |
| Bedrock direct provider | 완료 | `scripts/check_bedrock_chat_smoke.py` returned `ok=true`, model `apac.amazon.nova-micro-v1:0`, `matched_terms=[]`. | Keep AgentCore Runtime out of this phase. |
| Deployed chat explanation | 완료 | `POST /v1/chat` returned `success=true`, answer present, `data.safety.policy_action=ALLOW`, `data.citations` count `2`. | Re-run after Lambda, IAM, or Bedrock config changes. |
| Live ingestion readiness | 완료 | Post-#254 `scripts/check_ingestion_smoke.py` returned `ok=true`, `ready_for_manual_ingestion=true`, `scheduler_enable_ready=true`, blockers `[]`. | Manual provider ingest is not re-run in this audit to avoid unnecessary provider calls. |
| Ingestion scheduler | 완료 | After #275, `aws scheduler list-schedules --name-prefix stockbrief-dev-provider-ingestion` returned an empty list. | Keep disabled until the next reviewed live provider ingestion window. |
| Ingestion ledger and evidence | 완료 | Status snapshot showed `started=0`, `succeeded=10`, `failed=0`, latest evidence count `10`. | Investigate only if future runs show stale `started` rows or failures. |
| DLQ | 완료 | SQS attributes showed visible `0`, not-visible `0`, delayed `0`. | Check after every scheduler or manual ingestion smoke. |
| NAT and scheduler cost state | 완료 | GitHub Environment `dev` has `enable_lambda_nat_egress=false` and `enable_ingestion_scheduler=false`; NAT Gateway `nat-06de3faa3d9831ce4` is `deleted`; EIP allocation `eipalloc-099e616e0e7f6d2a1` is not found; scheduler jobs are absent. | Re-enable only through a reviewed live ingestion window. |
| AgentCore Runtime | 완료 | `agentcore_runtime_enabled=false`; Terraform outputs for AgentCore runtime ARN and endpoint are empty. | Keep disabled until direct Bedrock is stable and a runtime need is proven. |

## Redacted Smoke Evidence

Run these from the BE repository root unless noted otherwise.

### Deployment Evidence

BE #252 and #254 are the current deployment boundary:

- `backend-dev-deploy` run `28560982229` on commit `88bfb21`
  - `Plan: 11 to add, 1 to change, 1 to destroy`
  - `Apply complete! Resources: 11 added, 1 changed, 1 destroyed.`
  - Created NAT Gateway, EIP, route table associations, ingestion scheduler
    role, scheduler jobs, and scheduler Lambda permissions.
- `backend-dev-deploy` run `28561585744` on commit `dde9eeb`
  - `Plan: 0 to add, 2 to change, 0 to destroy`
  - `Apply complete! Resources: 0 added, 2 changed, 0 destroyed.`
  - Updated `stockbrief-dev-api` Lambda package with the provider-scoped
    ingestion scheduler gate from BE #254.
- `backend-dev-deploy` run `28574501920` on `main`
  - Triggered with GitHub Environment `dev` `enable_lambda_nat_egress=false`
    and `enable_ingestion_scheduler=false`.
  - Terraform apply succeeded after the deploy role policy was updated with
    `ec2:DisassociateAddress` and `iam:ListInstanceProfilesForRole`.
  - Completed the #275 cost pause after the earlier run `28574284317` had
    already destroyed scheduler jobs, route associations, and the NAT Gateway
    but failed on the final EIP/IAM cleanup permissions.

### API Smoke

```bash
API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com"

curl -fsS "$API_BASE_URL/v1/health"
curl -fsS "$API_BASE_URL/v1/recommendations/candidates?limit=3"
curl -fsS -X POST "$API_BASE_URL/v1/chat" \
  -H 'Content-Type: application/json' \
  --data '{"ticker":"005930","message":"왜 추천됐나요?"}'
```

Evidence captured on 2026-07-02:

- `/v1/health`: `status=ok`, `service=stockbrief-api`, `environment=dev`
- `/v1/recommendations/candidates?limit=3`: `count=3`, first ticker `005930`,
  first evidence level `medium`, first evidence count `42`
- `/v1/chat`: `success=true`, answer present, `data.safety.policy_action=ALLOW`,
  `data.citations` count `2`

Do not paste the full chat answer into PRs. The deployed smoke should summarize
only response status, citation count, and safety policy fields.

### Bedrock Direct Smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_bedrock_chat_smoke.py \
  --model-id apac.amazon.nova-micro-v1:0 \
  --region ap-northeast-2
```

Evidence captured on 2026-07-02:

- `ok=true`
- `model_id=apac.amazon.nova-micro-v1:0`
- `answer_sha256_prefix=a61995047cfd`
- `matched_terms=[]`

The helper intentionally hashes the answer and does not print the raw model
text.

### Ingestion Smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_ingestion_smoke.py \
  --function-name stockbrief-dev-api \
  --providers OpenDART NAVER_NEWS \
  --tickers 005930 \
  --status-limit 10
```

Evidence captured after BE #254 deployed on 2026-07-02:

- `ok=true`
- `ready_for_manual_ingestion=true`
- `scheduler_enable_ready=true`
- `blockers=[]`
- `observations=[]`
- readiness is scoped to selected providers:
  - OpenDART configured
  - NAVER_NEWS client ID and secret configured
  - KRX is not treated as a blocker for the OpenDART/NAVER scheduler gate
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

### DLQ, NAT, And Scheduler Checks

```bash
AWS_PROFILE=stockbrief-dev \
aws sqs get-queue-attributes \
  --queue-url "https://sqs.ap-northeast-2.amazonaws.com/560271561793/stockbrief-dev-ingestion-dlq" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed \
  --region ap-northeast-2

AWS_PROFILE=stockbrief-dev \
aws ec2 describe-nat-gateways \
  --region ap-northeast-2 \
  --filter Name=vpc-id,Values=vpc-07b9f3920d93b65e1 \
  --query 'NatGateways[].{NatGatewayId:NatGatewayId,State:State,SubnetId:SubnetId}'

AWS_PROFILE=stockbrief-dev \
aws scheduler list-schedules \
  --name-prefix stockbrief-dev-provider-ingestion \
  --query 'Schedules[].{Name:Name,State:State}' \
  --region ap-northeast-2

AWS_PROFILE=stockbrief-dev \
aws ec2 describe-addresses \
  --region ap-northeast-2 \
  --allocation-ids eipalloc-099e616e0e7f6d2a1
```

Evidence captured on 2026-07-02:

- DLQ visible messages: `0`
- DLQ not-visible messages: `0`
- DLQ delayed messages: `0`
- GitHub Environment `dev`: `enable_lambda_nat_egress=false`,
  `enable_ingestion_scheduler=false`
- NAT Gateway: `nat-06de3faa3d9831ce4`, state `deleted`
- EIP allocation `eipalloc-099e616e0e7f6d2a1`: `InvalidAllocationID.NotFound`
- EventBridge Scheduler prefix `stockbrief-dev-provider-ingestion`: empty list

### Recommendation Quality Smoke

```bash
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com" \
uv run python scripts/check_recommendation_quality_smoke.py \
  --limit 3 \
  --max-detail-tickers 3
```

Evidence captured on 2026-07-02:

- `ok=true`
- candidate list: target `/recommendations/candidates?limit=3`, count `3`,
  first ticker `005930`, selected tickers `005930`, `207940`, `000660`, as-of
  `2026-06-09`
- candidate detail: targets `/recommendations/candidates/{ticker}`, evidence
  level `medium`, evidence counts `42`, `2`, and `22`, risk tag count `1`,
  missing data count `0`, reason count `1`
- score components: all selected details returned component count `8` and
  component weight sum `100`
- stock evidence: targets `/stocks/{ticker}/evidence`; source metadata coverage
  passed for selected tickers, including internal `SCORE` evidence metadata for
  `207940`
- `blockers=[]`

### Hosted Auth Smoke

Page-only hosted smoke can run without a bearer token:

```bash
STOCKBRIEF_HOSTED_URL="https://main.d20hgo2k8atldu.amplifyapp.com" \
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com" \
uv run python scripts/check_hosted_auth_smoke.py --skip-auth-api
```

Page-only evidence captured on 2026-07-02:

- `/`: HTTP 200
- `/account`: HTTP 200
- `/auth/callback`: HTTP 200
- `auth_token_configured=false`
- `blockers=[]`

Full API auth smoke requires a short-lived browser session token:

```bash
install -m 600 /dev/null /tmp/stockbrief-auth-token.txt
$EDITOR /tmp/stockbrief-auth-token.txt

uv run python scripts/check_hosted_auth_smoke.py \
  --token-file /tmp/stockbrief-auth-token.txt

rm -f /tmp/stockbrief-auth-token.txt
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
token file path, watchlist item body, chat title, or raw protected API response
body. Delete the temporary token file after the smoke finishes.

### FE Hosted Live Evidence Smoke

Run this from the FE repository root after a hosted FE deploy, detail page
change, recommendation card change, or API base URL change:

```bash
pnpm run smoke:hosted-evidence -- \
  --hosted-url https://main.d20hgo2k8atldu.amplifyapp.com \
  --ticker 005930 \
  --search-query 삼성전자 \
  --search-result-name 삼성전자 \
  --search-result-ticker 005930
```

Evidence captured on 2026-07-03 after FE #118 merged:

- `ok=true`
- `/`: HTTP 200, product name and candidate copy present
- `/stocks/005930`: HTTP 200, score, evidence section, evidence ID, published
  date, and source reference present
- `/search?q=삼성전자`: HTTP 200, search heading, search copy, `삼성전자`
  result name, and `/stocks/005930` result link present
- `/watchlist`: HTTP 200, watchlist heading and guest localStorage copy present
- `/account`: HTTP 200, account heading, guest continuity copy, and auth entry
  or config-state marker present
- `/auth/callback`: HTTP 200, callback heading, recovery copy, and account
  recovery link present
- `blockers=[]`

FE #104 originally added this smoke. FE #110 aligned the recommendation
contract, and FE #112 expanded the hosted smoke to include the guest watchlist
page. FE #116 expanded it again to include hosted account and auth callback
page markers. FE #118 added the P0 stock search page check and split the search
query from the expected result ticker so company-name searches can still verify
the canonical `/stocks/005930` detail link. The hosted
evidence/watchlist/auth/search-page smoke still passes against the current
hosted FE. Re-run it after any later FE deploy,
detail/recommendation/search/watchlist/account runtime UI change, auth callback
change, or API base URL change.

## Terraform Profile And Drift Notes

Current deploy behavior:

- GitHub Environment `dev` variables are the source of truth for deploy-time
  backend config and tfvars.
- The repository `infra/terraform/envs/dev/deploy.auto.tfvars.json` remains a
  local paused-cost template. It is not the deploy source when GitHub Environment
  `TFVARS_JSON` is present.
- The current GitHub Environment tfvars keep:
  - `enable_lambda_nat_egress=false`
  - `enable_ingestion_scheduler=false`
  - OpenDART and NAVER_NEWS scheduler jobs for ticker `005930`
- `backend-dev-deploy` run `28574501920` applied the current cost-pause state
  successfully.

Terraform drift classification is now tied to the deploy profile source being
reviewed first. The GitHub Environment tfvars are the reviewed deploy input, not
the paused-cost repository template, so inspect that Environment value before
classifying a plan.

Before any infrastructure apply, inspect the deploy run plan. Do not apply a
plan blindly. Any create, destroy, or cost-sensitive in-place change must be
classified in the PR body before merge.

Historical note: the 2026-06-29 #221 follow-up recorded a paused-cost local
baseline with `0 to add, 5 to change, 0 to destroy`. BE #252 and BE #254
temporarily superseded it for the live provider window; #275 returned the
deploy-time Environment values to the pause-first baseline.

After #214 and #275, the default cost posture is pause-first.

NAT/scheduler cost posture decided in #214 still applies as the default rule:

- Pause `enable_lambda_nat_egress` and `enable_ingestion_scheduler` while no
  live provider ingestion work is active.
- Keep the reviewed OpenDART/NAVER `005930` job definitions as reactivation inputs
  for the next reviewed pause/resume cycle.
- Re-enable or pause NAT and scheduler only through a reviewed PR and GitHub
  Environment tfvars.
- Treat remaining Amplify, Cognito, RDS, and Lambda package hash in-place drift
  as separate classification work. Do not fold those into the NAT/scheduler cost
  pause unless the PR body explains each item.

## Cost And Resume Decision

Current cost-sensitive state after #275:

- RDS is running and available.
- NAT Gateway `nat-06de3faa3d9831ce4` is deleted.
- Elastic IP allocation `eipalloc-099e616e0e7f6d2a1` is released.
- EventBridge Scheduler jobs for OpenDART and NAVER_NEWS are absent.
- RDS Proxy is disabled.
- AgentCore Runtime is disabled.

Decision rule:

- Keep NAT and scheduler enabled only while live provider ingestion development
  is active.
- If work pauses, create a reviewed cost-pause PR that sets GitHub Environment
  `enable_lambda_nat_egress=false` and `enable_ingestion_scheduler=false`, then
  verify that Terraform removes the NAT Gateway/EIP and scheduler jobs.
- Do not delete Terraform-managed resources from the AWS console.

## Completion Gate For Next Feature Work

Product-flow feature development may resume when a new feature has its own
issue, branch, and review plan. The cloud completion gates below are closed for
the current dev baseline:

1. The original audit PR was reviewed and merged.
2. FE #104 merged the hosted live evidence visibility smoke.
3. BE #225 merged the full hosted auth API smoke helper fix and passed the
   short-lived-token smoke.
4. BE #252 fixed deploy profile source-of-truth handling and deployed NAT plus
   scheduler from GitHub Environment tfvars.
5. BE #254 fixed provider-scoped ingestion scheduler readiness and the
   post-deploy smoke returned `scheduler_enable_ready=true`.
6. FE #110 merged the recommendation candidate contract cleanup without changing
   runtime API behavior.
7. Recommendation quality and hosted page auth smoke returned `ok=true` on
   2026-07-02; FE hosted evidence/watchlist/auth/search-page smoke returned
   `ok=true` after FE #118 merged on 2026-07-03.
8. NAT/scheduler cost posture is pause-first for the current dev baseline:
   both are disabled after BE #275, and reactivation requires a reviewed live
   provider ingestion window.

Candidate next product checks after those gates:

- account watchlist/auth smoke with a short-lived token
- recommendation candidate quality criteria for additional tickers
- live evidence/watchlist visibility after future FE runtime UI changes merge
