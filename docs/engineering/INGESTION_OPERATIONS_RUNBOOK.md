# Ingestion Operations Runbook

This runbook describes how to manually verify StockBrief provider ingestion and
how to re-check reviewed scheduled ingestion in the dev AWS account. It assumes
the backend Terraform stack already exists and the operator is authenticated
with the `stockbrief-dev` AWS CLI profile.

Do not paste API keys, Secrets Manager values, access tokens, or full provider
payloads into PR comments, shared logs, or issue comments.

## Preconditions

- AWS account and region are confirmed:

  ```bash
  aws sts get-caller-identity --profile stockbrief-dev
  aws configure get region --profile stockbrief-dev
  ```

- Terraform state points at the intended dev backend:

  ```bash
  cd infra/terraform
  terraform init -reconfigure
  terraform state list
  terraform output api_base_url
  terraform output ingestion_raw_bucket_name
  terraform output ingestion_dlq_url
  terraform output external_api_secret_arn
  ```

- Keep the repository `deploy.auto.tfvars.json` on paused-cost defaults:
  `enable_ingestion_scheduler=false` and `enable_lambda_nat_egress=false`.
  GitHub Environment `dev` tfvars are the deploy-time source of truth for
  `backend-dev-deploy`. BE #252 and BE #254 intentionally enabled NAT egress and
  EventBridge Scheduler there for the reviewed OpenDART/NAVER `005930` jobs.
  #275 paused GitHub Environment `dev` again with
  `enable_ingestion_scheduler=false` and `enable_lambda_nat_egress=false`, then
  applied `backend-dev-deploy` run `28574501920`. Treat the committed job
  definitions as reactivation inputs, not as permission to run unattended
  provider calls outside a reviewed live window.
- External API credentials are stored in Secrets Manager outside git. Use the
  repository helper so the secret payload is written to a temporary file and
  removed automatically.
- KRX daily endpoints default to the KRX API spec URLs (`stk_bydd_trd` for
  KOSPI and `ksq_bydd_trd` for KOSDAQ); use secret endpoint overrides only when
  the provider issues a different dev endpoint.

  ```bash
  scripts/update_external_api_secret.sh --prompt --dry-run
  scripts/update_external_api_secret.sh --prompt
  ```

  The script prints Secrets Manager metadata only. Do not use
  `get-secret-value` in shared logs because it prints the secret payload. If
  Terraform state access fails, pass the external API secret ARN with
  `--secret-id` to skip state lookup.
- Lambda has outbound internet egress for OpenDART, NAVER, and KRX. Verify it from the
  Lambda runtime after the readiness check:

  NAT may be enabled only during the live ingestion smoke window. The current
  dev cost-pause state has the former NAT Gateway `nat-06de3faa3d9831ce4`
  deleted and its Elastic IP allocation released. When NAT is enabled again,
  `lambda_nat_public_subnet_id` must point at a public subnet with an Internet
  Gateway route, and `lambda_nat_route_subnet_ids` must point at the Lambda
  private subnets in `infra/terraform/envs/dev/deploy.auto.tfvars.json`. The NAT
  public subnet must not be included in the route subnet list. Turn NAT off
  again before pausing the dev environment if no live provider work remains.
  When the S3 raw archive Gateway endpoint is enabled, the managed NAT route
  table is also attached to that endpoint so raw archive writes continue through
  the Gateway endpoint after the Lambda subnet route table association changes.

  ```bash
  aws lambda invoke \
    --function-name stockbrief-dev-api \
    --payload '{"stockbrief_operation":"check_provider_egress","providers":["OpenDART","NAVER_NEWS","KRX"]}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-provider-egress-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

  The operation does not send API keys or client secrets. KRX uses the checked-in
  KRX spec KOSPI endpoint unless `KRX_DAILY_URL` or `KRX_KOSPI_DAILY_URL`
  overrides it. HTTP responses such as `401`, `403`, or provider validation errors still
  prove network reachability. DNS, connection, and timeout failures mean
  provider egress is not ready. An S3 Gateway endpoint only covers raw archive
  writes to S3.
- For repeated checks, use the redacted smoke helper instead of copying full
  Lambda responses into shared logs:

  ```bash
  uv run python scripts/check_ingestion_smoke.py \
    --function-name stockbrief-dev-api \
    --profile stockbrief-dev \
    --providers OpenDART NAVER_NEWS KRX \
    --tickers 005930
  ```

  The helper runs readiness, raw archive write, provider egress, ingestion
  status, and scheduler gate checks. It redacts secret-like fields and prints
  only the status fields needed for PR evidence.
- RDS is available and the latest migration has run:

  ```bash
  aws lambda invoke \
    --function-name stockbrief-dev-api \
    --payload '{"stockbrief_operation":"migrate"}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-migrate-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

## Local Patched Adapter Verification

Run these commands from `StockBrief-be` before requesting AWS dev smoke:

```bash
uv run pytest tests/test_ingestion_pipeline.py
uv run pytest tests/test_external_adapters.py
uv run pytest tests/test_recommendation_materializer.py
```

The local KRX adapter test does not require live credentials. It patches the KRX
provider client, ingests one successful ticker and one fallback ticker, then
verifies that score refresh runs only for the eligible ticker and records
`data_freshness.providers.KRX.status = partial_failed`.

## Score Refresh Operation

Use `refresh_score_snapshots` when a provider ingest should immediately refresh
eligible score snapshots. The operation runs provider ingestion first when
`provider` is present, sends only succeeded or replayed tickers to the
materializer, and records provider freshness on refreshed snapshots.
EventBridge Scheduler provider jobs use this operation so scheduled collection
does not leave stale recommendation scores behind.

Local patched adapter shape:

```json
{
  "stockbrief_operation": "refresh_score_snapshots",
  "provider": "KRX",
  "tickers": ["005930"],
  "source_date": "YYYY-MM-DD"
}
```

Expected result:

- `successful_tickers[]` contains only `succeeded` or `replayed` ingestion rows.
- `failed_tickers[]` contains `failed` or `partial_failed` rows.
- `refresh.processed` equals the number of eligible tickers that the
  materializer processed.
- `provider_status` is `success`, `partial_failed`, `failed`, or `stale`.
- Refreshed score rows include `data_freshness.providers`.

For market-wide precomputation without a live provider call, omit `provider`
and select a stored universe. `tier_a` defaults to the top 100 active stocks,
`tier_b` to the top 300, ordered by the latest KRX `market_cap` and
`trading_value`. `stock_limit` and `stock_offset` still cap each Lambda batch.

```json
{
  "stockbrief_operation": "refresh_score_snapshots",
  "source_date": "YYYY-MM-DD",
  "score_universe": "tier_a",
  "markets": ["KOSPI", "KOSDAQ"],
  "stock_limit": 100,
  "stock_offset": 0
}
```

Tier C is intentionally just the full active universe (`score_universe="all"`)
with explicit market and offset batches. Keep embedding out of the score
refresh path until a vector store and retention policy are reviewed; use
`evidence_chunks` as stored evidence for now.

## Manual Provider Smoke

Use one ticker first. Replace `YYYY-MM-DD` with the business date you want to
verify. Keep the response files in `/tmp` and summarize only the non-secret
status fields in PR evidence.

Seed stock universe master rows before provider refresh. This operation writes
only `stocks` and OpenDART `company_identifiers`; source documents, evidence,
prices, and recommendation scores are created by provider ingestion and score
materialization.

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"seed_stock_universe","tickers":["005930"]}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-seed-stock-universe-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

OpenDART refresh:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"OpenDART","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-opendart-refresh-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

The OpenDART provider refresh persists both disclosure evidence and the latest
two completed annual financial statement years from
`fnlttSinglAcntAll.json`. Those real `financial_statements` rows feed
`financial_stability`, `profitability`, `growth`, and the earnings/book portion
of `valuation`. If OpenDART has no statement rows for the selected company/year,
keep the component `missing_data` visible rather than substituting mock or
fallback financial rows.

OpenDART disclosure search uses a one-year receipt-date window ending at
`source_date`. This avoids the provider default of only checking the current
day and supplies `disclosure_event.inputs` from historical filings that were
published before the score `as_of` date. If an older successful ingestion run
needs to be bypassed after a payload-shape change, pass a reviewed explicit
`run_id`; the idempotency hash includes that value so the operation performs a
fresh provider call while repeated identical `run_id` calls remain duplicate
safe.

NAVER news refresh:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"NAVER_NEWS","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-naver-refresh-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

KRX price refresh:

Run this only after the developer confirms `KRX_API_KEY` is present in Secrets
Manager and Lambda egress is approved for the smoke window. The default KRX
daily endpoints come from the workspace KRX API specs under
`../docs/engineering/api-specs/krx_api_spec/`.

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"KRX","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-krx-refresh-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Ticker-level KRX refresh also recalculates the latest row's `momentum_20d` and
`volatility_20d` once the ticker has at least 21 real KRX close-price rows.
Use one refresh per trading day for a small smoke target, or
`seed_krx_stock_universe.source_dates` for market-wide history.

KRX market-wide bootstrap:

Use `seed_krx_stock_universe` to backfill KRX stock master rows and daily price
metrics before market-wide score refresh. Pass `source_dates` when enough
trading-day history is available; once a ticker has 21 price rows, the latest
`price_metrics` row receives `momentum_20d` and `volatility_20d`, which removes
`momentum_volatility.inputs` from the score result.

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"seed_krx_stock_universe","source_dates":["YYYY-MM-DD","YYYY-MM-DD"],"markets":["KOSPI","KOSDAQ"]}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-krx-universe-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

When credentials and egress are ready, the same helper can run one manual
provider refresh per selected provider:

```bash
uv run python scripts/check_ingestion_smoke.py \
  --function-name stockbrief-dev-api \
  --profile stockbrief-dev \
  --providers OpenDART NAVER_NEWS KRX \
  --tickers 005930 \
  --source-date YYYY-MM-DD \
  --run-provider-ingest
```

With `--run-provider-ingest`, the helper first verifies readiness, raw archive
write, and provider egress. Only when those preflight checks pass does it invoke
`seed_stock_universe`, followed by one `refresh_score_snapshots` operation per
selected provider. When preflight fails, it reports `preflight_not_ready` and
does not call seed or refresh operations.

Expected result:

- Lambda invoke status is `200`.
- Response `ok` is `true`, or a provider-specific partial status is understood
  and documented.
- Refreshed recommendation rows include `data_freshness.providers.<provider>`.
- Missing credential errors such as `missing_api_key` are absent.
- Re-running the same input returns a replayed or duplicate-safe result instead
  of creating uncontrolled duplicate rows.

## Ingestion Status Snapshot

After a manual provider run, ask the deployed Lambda for a non-secret status
snapshot before opening SQL clients. This operation does not call provider APIs
and returns recent `ingestion_runs` plus the latest normalized evidence rows.

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"get_ingestion_status","tickers":["005930"],"providers":["NAVER_NEWS"],"limit":10}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-ingestion-status-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected result:

- Response `ok` is `true`.
- `summary.run_status_counts.succeeded` increases after successful provider
  runs.
- `summary.ticker_filter` and `summary.provider_filter` match the requested
  smoke scope.
- `recent_runs[].provider`, `recent_runs[].status`, `recent_runs[].ticker`, and
  `recent_runs[].source_date` match the manual smoke input.
- `latest_evidence[]` includes recent `evidence_id`, `ticker`, `source_name`,
  `source_type`, `published_at`, and `fetched_at` fields for the requested
  ticker.
- The response does not include API keys, client secrets, tokens, or full raw
  provider payloads.

## Stale Started Run Reconciliation

If `get_ingestion_status` shows old `started` rows, first run reconciliation in
dry-run mode. This checks stale rows without changing the database:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"reconcile_stale_ingestion_runs","tickers":["005930"],"providers":["NAVER_NEWS","OpenDART"],"max_age_minutes":60,"dry_run":true}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-stale-ingestion-dry-run-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected dry-run result:

- Response `ok` is `true`.
- `dry_run` is `true`.
- `stale_runs[]` contains only the requested ticker/provider scope.
- `updated_count` is `0`.

After reviewing the dry-run output, mark stale `started` runs as `failed` only
when they are older than the chosen threshold and no Lambda invocation is still
running:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"reconcile_stale_ingestion_runs","tickers":["005930"],"providers":["NAVER_NEWS","OpenDART"],"max_age_minutes":60,"dry_run":false}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-stale-ingestion-apply-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected apply result:

- `updated_count` matches the reviewed stale row count.
- Updated runs have `status = failed` and
  `error_summary.code = stale_started_run_reconciled`.
- A follow-up `get_ingestion_status` no longer shows those runs as `started`.

## Database Verification

Use a read-only SQL client or a temporary operator session. Do not write manual
rows to production-like tables.

Minimum checks:

```sql
select run_id, provider, status, source_date, result_counts, completed_at
from ingestion_runs
where provider in ('OpenDART', 'NAVER_NEWS')
order by started_at desc
limit 10;

select ticker, source_name, source_type, external_id, created_at
from source_documents
where ticker = '005930'
order by created_at desc
limit 10;

select evidence_id, ticker, evidence_type, published_at, source_url
from evidence_chunks
where ticker = '005930'
order by published_at desc nulls last, fetched_at desc
limit 10;
```

Provider-specific checks:

```sql
select ticker, provider, receipt_no, disclosed_at, title
from disclosures
where ticker = '005930'
order by disclosed_at desc nulls last, created_at desc
limit 10;

select ticker, source_name, title, published_at, source_url
from news_items
where ticker = '005930'
order by published_at desc nulls last, created_at desc
limit 10;
```

Expected result:

- At least one `ingestion_runs` row exists for each manual provider run.
- Successful rows end in `succeeded` or a documented `partial_failed` state.
- Normalized rows reference source documents where applicable.
- Provider rows create `evidence_chunks` so stock evidence and candidate summary
  APIs can surface live news and disclosure evidence.
- Raw provider payloads are referenced through metadata, not copied into PR
  evidence.

## Raw Archive Verification

Before provider credentials and outbound internet egress are ready, verify that
the deployed Lambda can write a small raw archive probe through its S3 path:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"stockbrief_operation":"check_raw_archive_write"}' \
  /tmp/stockbrief-raw-archive-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected result:

- The response has `ok=true`.
- `checks.raw_archive.write_verified` is `true`.
- `checks.raw_archive.raw_archive_uri` points at the Terraform-managed raw
  archive bucket.
- The probe payload is intentionally small and does not include provider data or
  secrets. Do not copy object bodies into PR comments.

After a manual provider run, confirm the raw archive bucket exists and new
objects were written for the exact manual run. Do not inspect the bucket root
because older objects can make a stale archive look current.

```bash
aws s3api list-objects-v2 \
  --bucket "$(terraform output -raw ingestion_raw_bucket_name)" \
  --prefix "raw/provider=OpenDART/ticker=005930/" \
  --query "Contents[?contains(Key, 'run_id=')].[Key,LastModified,Size]" \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

If the Lambda response includes `raw_archive_uri`, verify the exact object key
instead of relying on a prefix listing:

```bash
aws s3api head-object \
  --bucket "$(terraform output -raw ingestion_raw_bucket_name)" \
  --key "raw/provider=OpenDART/ticker=005930/run_id=REPLACE_WITH_RUN_ID.json" \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Repeat the same check with the `raw/provider=NAVER_NEWS/ticker=005930/` prefix
or the exact `raw_archive_uri` returned by the NAVER manual run.

Expected result:

- New S3 objects exist under the provider/ticker prefix or the exact
  `raw_archive_uri` key returned by the provider run.
- Objects use the Terraform-managed raw archive bucket.
- Object bodies are not copied into PR comments because they may include
  provider payload details.

## DLQ And CloudWatch Verification

The DLQ should stay empty for manual smoke runs:

```bash
aws sqs get-queue-attributes \
  --queue-url "$(terraform output -raw ingestion_dlq_url)" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Check recent Lambda logs for ingestion errors without printing secret material:

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/stockbrief-dev-api \
  --filter-pattern 'ingestion error failed missing_api_key timeout' \
  --limit 20 \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected result:

- `ApproximateNumberOfMessages` is `0`.
- `ApproximateNumberOfMessagesNotVisible` is `0`.
- CloudWatch logs do not contain API keys, client secrets, tokens, or database
  passwords.
- Any provider failure is captured as a provider or network issue, not as an
  unhandled Lambda crash.

## Scheduler Enable Gate

Do not enable EventBridge Scheduler until all conditions are true:

- Run the combined scheduler gate operation from the deployed Lambda and confirm
  `scheduler_enable_ready=true`:

  ```bash
  aws lambda invoke \
    --function-name stockbrief-dev-api \
    --payload '{"stockbrief_operation":"check_ingestion_scheduler_enable_gate","providers":["OpenDART","NAVER_NEWS","KRX"],"tickers":["005930"],"limit":10}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-scheduler-gate-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

- OpenDART, NAVER, and KRX manual smoke runs have completed with understood
  results. The scheduler gate treats each selected `provider + ticker` pair as
  ready only after `get_ingestion_status` shows a recent `succeeded` run for
  that pair. `partial_failed`, `failed`, `started`, or missing runs return the
  `manual_ingestion_smoke_missing` blocker.
- Stale `started` ingestion runs have been reviewed with
  `reconcile_stale_ingestion_runs` dry-run and reconciled if needed.
- `ingestion_runs`, normalized provider tables, `source_documents`, S3 raw
  archive, DLQ, and CloudWatch logs have been checked.
- Provider rate limits, ticker count, and expected execution frequency have
  been reviewed.
- The reviewed dev scheduler job list is explicit. The current reviewed dev
  jobs use `OpenDART` and `NAVER_NEWS` for ticker `005930` with weekday KST
  expressions `cron(0 18 ? * MON-FRI *)` and
  `cron(5 18 ? * MON-FRI *)`. After #214, those jobs stay in tfvars but
  `enable_ingestion_scheduler` stays `false` until provider egress and the
  scheduler gate pass again for the next live ingestion window.
- Lambda outbound internet egress is confirmed by `check_provider_egress`.
- The scheduler change is reviewed in a separate PR.

If any check fails, keep `enable_ingestion_scheduler = false`, record the
blocking condition, and fix the smallest failing layer first. If the current
dev scheduler is already enabled and a re-check fails, pause the affected job or
NAT-dependent schedule in a reviewed Terraform change before continuing
unattended runs.
