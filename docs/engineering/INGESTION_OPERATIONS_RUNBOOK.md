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

- `enable_ingestion_scheduler` matches the reviewed dev tfvars. Keep it `false`
  while Lambda provider egress is unavailable. When a live provider smoke window
  needs scheduled runs, enable NAT egress, review the exact provider/ticker
  schedule, verify `check_ingestion_scheduler_enable_gate`, then apply the
  scheduler change. After NAT egress is turned off, pause the dev scheduler
  again so scheduled jobs do not fail on provider network access.
  The current dev profile keeps the reviewed OpenDART and NAVER scheduler job
  definitions for ticker `005930`, but #214 pauses `enable_ingestion_scheduler`
  and `enable_lambda_nat_egress` by default. Treat the job definitions as
  reactivation inputs, not as permission to run unattended provider calls.
- External API credentials are stored in Secrets Manager outside git. Use the
  repository helper so the secret payload is written to a temporary file and
  removed automatically:

  ```bash
  scripts/update_external_api_secret.sh --prompt --dry-run
  scripts/update_external_api_secret.sh --prompt
  ```

  The script prints Secrets Manager metadata only. Do not use
  `get-secret-value` in shared logs because it prints the secret payload. If
  Terraform state access fails, pass the external API secret ARN with
  `--secret-id` to skip state lookup.
- Lambda has outbound internet egress for OpenDART and NAVER. Verify it from the
  Lambda runtime after the readiness check:

  NAT may be enabled only during the live ingestion smoke window. When it is
  enabled, `lambda_nat_public_subnet_id` must point at a public subnet with an
  Internet Gateway route, and `lambda_nat_route_subnet_ids` must point at the
  Lambda private subnets in `infra/terraform/envs/dev/deploy.auto.tfvars.json`.
  The NAT public subnet must not be included in the route subnet list. Turn NAT
  off again before pausing the dev environment if no live provider work remains.
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

  The operation does not send API keys or client secrets. KRX requires
  `KRX_DAILY_URL` to be configured before this check can probe its endpoint.
  HTTP responses such as `401`, `403`, or provider validation errors still
  prove network reachability. DNS, connection, and timeout failures mean
  provider egress is not ready. An S3 Gateway endpoint only covers raw archive
  writes to S3.
- For repeated checks, use the redacted smoke helper instead of copying full
  Lambda responses into shared logs:

  ```bash
  AWS_PROFILE=stockbrief-dev \
  uv run python scripts/check_ingestion_smoke.py \
    --function-name stockbrief-dev-api \
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

## Local Mocked Verification

Run these commands from `StockBrief-be` before requesting AWS dev smoke:

```bash
rtk uv run pytest tests/test_ingestion_pipeline.py
rtk uv run pytest tests/test_external_adapters.py
rtk uv run pytest tests/test_recommendation_materializer.py
```

The mocked KRX path does not require live credentials. The local tests patch the
KRX provider client, ingest one successful ticker and one fallback ticker, then
verify that score refresh runs only for the eligible ticker and records
`data_freshness.providers.KRX.status = partial_failed`.

## Score Refresh Operation

Use `refresh_score_snapshots` when a provider ingest should immediately refresh
eligible score snapshots. The operation runs provider ingestion first when
`provider` is present, sends only succeeded or replayed tickers to the
materializer, and records provider freshness on refreshed snapshots.

Local mocked shape:

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

## Manual Provider Smoke

Use one ticker first. Replace `YYYY-MM-DD` with the business date you want to
verify. Keep the response files in `/tmp` and summarize only the non-secret
status fields in PR evidence.

OpenDART:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"ingest_provider_batch","provider":"OpenDART","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-opendart-ingest-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

NAVER news:

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"ingest_provider_batch","provider":"NAVER_NEWS","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-naver-ingest-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

KRX price refresh:

Run this only after the developer confirms the KRX dev endpoint and credential
are present in Secrets Manager and Lambda egress is approved for the smoke
window.

```bash
aws lambda invoke \
  --function-name stockbrief-dev-api \
  --payload '{"stockbrief_operation":"refresh_score_snapshots","provider":"KRX","tickers":["005930"],"source_date":"YYYY-MM-DD"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-krx-refresh-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

When credentials and egress are ready, the same helper can run one manual
provider ingest per selected provider:

```bash
AWS_PROFILE=stockbrief-dev \
uv run python scripts/check_ingestion_smoke.py \
  --function-name stockbrief-dev-api \
  --providers OpenDART NAVER_NEWS \
  --tickers 005930 \
  --source-date YYYY-MM-DD \
  --run-provider-ingest
```

Expected result:

- Lambda invoke status is `200`.
- Response `ok` is `true`, or a provider-specific partial status is understood
  and documented.
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
    --payload '{"stockbrief_operation":"check_ingestion_scheduler_enable_gate","providers":["OpenDART","NAVER_NEWS"],"tickers":["005930"],"limit":10}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-scheduler-gate-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

- Both OpenDART and NAVER manual smoke runs have completed with understood
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
