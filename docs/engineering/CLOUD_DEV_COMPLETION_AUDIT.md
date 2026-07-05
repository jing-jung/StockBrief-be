# Cloud Dev Completion Audit

This audit records the current `dev` cloud completion state for StockBrief.
It intentionally excludes toolchain migration work and FE-to-BE integration
implementation because those were owned by other teammates.

Audit date: 2026-07-05
AWS account: `560271561793`
Region: `ap-northeast-2`
Linked issues: `#211`, `#226`, `#253`, `#255`, `#275`, `#284`, `#286`, `#290`, `#292`, `#293`, `#303`

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
| Latest main baseline | 완료 | BE `main` is at `be31b32` after BE #302; FE `main` is at `a7f1b9f` after FE #122. FE has open PR #123, so hosted FE evidence remains the FE #118/#122 main baseline until that PR is reviewed and merged. | Start all new BE work from latest `main`; re-run FE hosted smoke after FE #123 or any later FE runtime UI change merges. |
| Deploy profile source | 완료 | BE #252 made GitHub Environment `TFVARS_JSON` and `TF_BACKEND_CONFIG_HCL` the source of truth for `backend-dev-deploy`. | Keep runner-rendered tfvars/backend files out of the repository. |
| Terraform apply | 완료 | Latest `backend-dev-deploy` run `28741696267` succeeded on BE `be31b32` after #302. Earlier runs `28730540713`, `28731075489`, and `28731527860` deployed #300/#301 AgentCore and Bedrock changes. | Inspect the deploy run after every merge or Environment tfvars change. |
| API Gateway and Lambda API | 완료 | `GET /v1/health` returned `status=ok`, `service=stockbrief-api`, `environment=dev`. | Continue using deployed smoke before release or after resume. |
| Recommendation API | 완료 | On 2026-07-05 the first post-#302 smoke found `count=0`. Running `seed_stock_universe` and `refresh_score_snapshots` for `005930`, `207940`, and `000660` restored score snapshots without external provider calls. Follow-up `GET /v1/recommendations/candidates?limit=3` returned `count=2`, first ticker `000660`, and included `005930`. | Re-run deployed API smoke after recommendation, ingestion, score materializer, or Lambda deploy changes. |
| Recommendation quality | 완료 | `scripts/check_recommendation_quality_smoke.py --limit 5 --max-detail-tickers 3 --expected-ticker 005930 --expected-ticker 000660` returned `ok=true`; selected tickers `005930`, `000660`; each detail returned 8 score components with weight sum `100`; `risk_tags` arrays were present and empty; blockers `[]`. | Re-run before product-flow work that changes candidate quality or evidence joins. |
| Cognito | 완료 | Terraform outputs include user pool `ap-northeast-2_VPOccT5rI`, issuer, app client, and Hosted UI domain; BE #225 verified the full protected API smoke, and BE #292 verified the full protected API plus watchlist write-cycle with a temporary Cognito smoke user. | Re-run full hosted auth smoke after Cognito, callback, account API, or Amplify domain changes. |
| Amplify hosted pages | 완료 | Page-only hosted smoke for `/`, `/account`, and `/auth/callback` returned HTTP 200 at `https://main.d20hgo2k8atldu.amplifyapp.com`. | Re-run hosted page smoke after FE deploy or Amplify config changes. |
| FE live evidence visibility | 완료 | FE hosted evidence/watchlist/auth/search-page smoke returned `ok=true` with `/`, `/stocks/005930`, `/search?q=삼성전자`, `/watchlist`, `/account`, and `/auth/callback` all HTTP 200 and no missing page markers. | Re-run after FE detail/recommendation/search/watchlist/account UI, auth callback, API base, or Amplify deploy changes. |
| RDS | 완료 | `stockbrief-dev-postgres` is available, PostgreSQL `16.13`, deletion protection `false`, backup retention `1`. | Stop RDS during inactive cost windows per `DEPLOYMENT_BOOTSTRAP.md`. |
| RDS Proxy | 완료 | Terraform output `rds_proxy_endpoint` is empty and `enable_rds_proxy=false`. | Keep disabled until Lambda concurrency requires pooling. |
| Bedrock direct provider | 완료 | `scripts/check_bedrock_chat_smoke.py` returned `ok=true` for `apac.amazon.nova-micro-v1:0` and `apac.anthropic.claude-3-5-sonnet-20241022-v2:0`; both returned `matched_terms=[]`. | Keep AgentCore Runtime optional until a runtime need is proven beyond direct Bedrock. |
| Deployed chat explanation | 완료 | `scripts/check_deployed_chat_smoke.py` returned `ok=true`; allowed explanation returned `policy_action=ALLOW`, redirected advice request returned `policy_action=REDIRECT`, both had citation count `4`, disclaimer present, and `matched_terms=[]`. | Re-run after Lambda, IAM, Bedrock, AgentCore, or recommendation candidate gate changes. |
| Live ingestion readiness | 조건부 | `get_ingestion_status` returned `started=0`, `succeeded=10`, `failed=0`, latest evidence count `10`; raw archive write passed. Full readiness is currently blocked because `KRX_API_KEY` is missing and provider egress timed out while NAT is disabled. | Keep scheduler disabled. For the next live provider window, add reviewed KRX credential if KRX is in scope and enable NAT through reviewed `TFVARS_JSON` before provider egress or scheduler gate checks. |
| Ingestion scheduler | 완료 | After #275, `aws scheduler list-schedules --name-prefix stockbrief-dev-provider-ingestion` returned an empty list. | Keep disabled until the next reviewed live provider ingestion window. |
| Ingestion ledger and evidence | 완료 | Status snapshot showed `started=0`, `succeeded=10`, `failed=0`, latest evidence count `10`. | Investigate only if future runs show stale `started` rows or failures. |
| DLQ | 완료 | SQS attributes showed visible `0`, not-visible `0`, delayed `0`. | Check after every scheduler or manual ingestion smoke. |
| NAT and scheduler cost state | 완료 | GitHub Environment `dev` has `enable_lambda_nat_egress=false` and `enable_ingestion_scheduler=false`; NAT Gateway `nat-06de3faa3d9831ce4` is `deleted`; EIP allocation `eipalloc-099e616e0e7f6d2a1` is not found; scheduler jobs are absent. | Re-enable only through a reviewed live ingestion window. |
| AgentCore Runtime | 완료 | `agentcore_runtime_enabled=false`; Terraform outputs for runtime ARN/ID/role are empty, while `agentcore_runtime_endpoint_name` resolves to the deterministic default `stockbrief_dev_default`. | Keep disabled until direct Bedrock is stable and a runtime need is proven. |

## Redacted Smoke Evidence

Run these from the BE repository root unless noted otherwise.

### Deployment Evidence

BE #300, #301, and #302 are the current deployed BE main boundary:

- `backend-dev-deploy` run `28730540713` on commit `e5d40f3`
  - Deployed BE #300 AgentCore Claude Bedrock profile preparation.
  - The run completed successfully after the runtime metadata model ID fallback
    fix was merged into the PR.
- `backend-dev-deploy` run `28731527860` on commit `22ed8b1`
  - Deployed BE #301 AgentCore invoke and citation guard preservation.
  - `backend-ci` for the same commit also completed successfully.
- `backend-dev-deploy` run `28741696267` on commit `be31b32`
  - Deployed BE #302 recommendation candidate list gate and chat answer display
    normalization recovery.
  - This is the latest deployed BE main evidence for the 2026-07-05 audit.

Earlier live ingestion and cost-pause deployment evidence:

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

Evidence captured on 2026-07-05 after BE #302 deployed:

- `/v1/health`: `status=ok`, `service=stockbrief-api`, `environment=dev`
- First post-#302 `/v1/recommendations/candidates?limit=3` smoke returned
  `count=0`, which made `/v1/chat` return `STOCK_NOT_FOUND` for `005930`.
- The dev data was restored without external provider calls:
  - `seed_stock_universe` for `005930`, `207940`, and `000660`: `ok=true`,
    `stocks=3`, `identifiers=6`, unknown tickers `[]`.
  - `refresh_score_snapshots` for the same tickers with `source_date=2026-07-05`:
    `ok=true`, provider status `stale`, processed `3`, created `3`, reasons
    `9`, provider freshness annotated `3`.
- Follow-up `/v1/recommendations/candidates?limit=3`: `count=2`, first ticker
  `000660`, listed tickers included `000660` and `005930`.
- Follow-up deployed `POST /v1/chat` smoke passed through
  `scripts/check_deployed_chat_smoke.py`: allowed explanation returned
  `policy_action=ALLOW`; advice-like prompt returned `policy_action=REDIRECT`;
  both responses had citation count `4`, disclaimer present, and
  `matched_terms=[]`.

Do not paste the full chat answer into PRs. The deployed smoke should summarize
only response status, citation count, and safety policy fields.

### Bedrock Direct Smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_bedrock_chat_smoke.py \
  --model-id apac.amazon.nova-micro-v1:0 \
  --region ap-northeast-2

AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_bedrock_chat_smoke.py \
  --model-id apac.anthropic.claude-3-5-sonnet-20241022-v2:0 \
  --region ap-northeast-2
```

Evidence captured on 2026-07-05:

- Nova direct smoke: `ok=true`, model `apac.amazon.nova-micro-v1:0`,
  answer hash prefix `246e9a43b265`, `matched_terms=[]`.
- Claude direct smoke: `ok=true`, model
  `apac.anthropic.claude-3-5-sonnet-20241022-v2:0`, answer hash prefix
  `ef071a9324d2`, `matched_terms=[]`.

The helper intentionally hashes the answer and does not print the raw model
text.

### Ingestion Smoke

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_ingestion_smoke.py \
  --function-name stockbrief-dev-api \
  --providers OpenDART NAVER_NEWS KRX \
  --tickers 005930 \
  --status-limit 10
```

Evidence captured on 2026-07-05 after BE #302 deployed:

- `ok=false`
- `ready_for_manual_ingestion=false`
- `scheduler_enable_ready=false`
- blockers:
  - `missing_provider_credential`, field `KRX_API_KEY`
  - `provider_egress` `ReadTimeoutError`
- observations:
  - `scheduler_gate` `ReadTimeoutError`
- readiness details:
  - raw archive is configured and write verification passed
  - OpenDART and NAVER_NEWS credentials are configured
  - KRX endpoints are configured, but `KRX_API_KEY` is missing
  - provider egress timed out while NAT is disabled
- status summary:
  - `started=0`
  - `succeeded=10`
  - `partial_failed=0`
  - `failed=0`
  - latest evidence count `10`

This audit did not run `--run-provider-ingest`; full provider ingestion remains
blocked until the next reviewed live provider window enables NAT and either
adds a reviewed KRX credential or scopes the smoke to providers with complete
credentials.

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

Evidence captured on 2026-07-05:

- DLQ visible messages: `0`
- DLQ not-visible messages: `0`
- DLQ delayed messages: `0`
- GitHub Environment `dev`: `enable_lambda_nat_egress=false`,
  `enable_ingestion_scheduler=false`
- NAT Gateway: `nat-06de3faa3d9831ce4`, state `deleted`
- EIP allocation `eipalloc-099e616e0e7f6d2a1`: `InvalidAllocationID.NotFound`
- EventBridge Scheduler prefix `stockbrief-dev-provider-ingestion`: empty list

### Recommendation Quality Smoke

The 2026-07-05 post-#302 smoke used this command:

```bash
STOCKBRIEF_API_BASE_URL="https://hazfha7995.execute-api.ap-northeast-2.amazonaws.com" \
uv run python scripts/check_recommendation_quality_smoke.py \
  --limit 5 \
  --max-detail-tickers 3 \
  --expected-ticker 005930 \
  --expected-ticker 000660
```

Evidence captured on 2026-07-05:

- `ok=true`
- candidate list: target `/recommendations/candidates?limit=5`, count `2`,
  first ticker `000660`, expected tickers `005930`, `000660`, missing expected
  tickers `[]`, selected tickers `005930`, `000660`, as-of `2026-07-05`
- candidate detail: targets `/recommendations/candidates/{ticker}`, evidence
  level `medium`, evidence counts `40` and `20`, risk tag count `0`, missing
  data count `2`, reason count `3`
- score components: all selected details returned component count `8` and
  component weight sum `100`
- stock evidence: targets `/stocks/{ticker}/evidence`; source metadata coverage
  passed for selected tickers with provider `NEWS` evidence
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

BE #292 captured the current full hosted auth API smoke and watchlist write-cycle
on 2026-07-03:

- hosted pages `/`, `/account`, and `/auth/callback`: HTTP 200
- `/v1/me`: authenticated summary passed, email present, email verified
- `/v1/me/preferences`: preference summary passed
- `/v1/me/watchlist`: item count summary passed
- `/v1/me/chat-sessions`: session count summary passed
- watchlist write-cycle: created, updated, deleted, and cleanup confirmed
- `blockers=[]`
- the temporary Cognito smoke user was deleted after the run
- the temporary token file and user tracking file were removed
- the Cognito app client auth flows were restored to
  `ALLOW_REFRESH_TOKEN_AUTH` and `ALLOW_USER_SRP_AUTH`
- the Cognito user pool user count was `0` after cleanup

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
- `backend-dev-deploy` run `28741696267` applied the latest BE #302 package
  successfully. The deploy profile still keeps NAT and scheduler disabled.

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

Current cost-sensitive state on 2026-07-05:

- RDS is running and available.
- NAT Gateway `nat-06de3faa3d9831ce4` remains deleted.
- Elastic IP allocation `eipalloc-099e616e0e7f6d2a1` remains released.
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
5. BE #254 fixed provider-scoped ingestion scheduler readiness. The 2026-07-05
   re-check shows the historical ingestion ledger is healthy, but full provider
   readiness is currently gated by NAT egress and `KRX_API_KEY` if KRX is in
   scope.
6. FE #110 merged the recommendation candidate contract cleanup without changing
   runtime API behavior.
7. Recommendation quality with expected tickers `005930` and `000660` returned
   `ok=true` on 2026-07-05 after dev score snapshots were restored from
   existing data; hosted page auth smoke returned `ok=true` on 2026-07-02; FE
   hosted evidence/watchlist/auth/search-page smoke returned `ok=true` after FE
   #118 merged on 2026-07-03.
8. NAT/scheduler cost posture is pause-first for the current dev baseline:
   both are disabled after BE #275, and reactivation requires a reviewed live
   provider ingestion window.
9. BE #292 verified the current full hosted auth API smoke and watchlist
   write-cycle on 2026-07-03, then deleted the temporary Cognito smoke user and
   token file.
10. BE #300, #301, and #302 are deployed to dev. Bedrock direct smoke passes for
    Nova and Claude, and deployed `/v1/chat` passes both allowed explanation and
    advice-redirection smoke scenarios.

Candidate next product checks after those gates:

- live evidence/watchlist visibility after future FE runtime UI changes merge
- reviewed live provider ingestion window after NAT and KRX credential scope are
  explicitly decided
