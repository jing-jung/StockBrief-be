# Ingestion Operations Runbook

This runbook describes how to manually verify StockBrief provider ingestion in
the dev AWS account before enabling any scheduled ingestion. It assumes the
backend Terraform stack already exists and the operator is authenticated with
the `stockbrief-dev` AWS CLI profile.

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

- `enable_ingestion_scheduler` remains `false`.
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

  NAT is intentionally disabled in the low-cost dev bootstrap. Before live
  provider ingestion, set `enable_lambda_nat_egress = true`, choose a public
  subnet for `lambda_nat_public_subnet_id`, and set
  `lambda_nat_route_subnet_ids` to the Lambda private subnets in
  `infra/terraform/envs/dev/deploy.auto.tfvars.json`. The NAT public subnet must
  not be included in the route subnet list.

  ```bash
  aws lambda invoke \
    --function-name stockbrief-dev-api \
    --payload '{"stockbrief_operation":"check_provider_egress","providers":["OpenDART","NAVER_NEWS"]}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/stockbrief-provider-egress-response.json \
    --profile stockbrief-dev \
    --region ap-northeast-2
  ```

  The operation does not send API keys or client secrets. HTTP responses such as
  `401`, `403`, or provider validation errors still prove network reachability.
  DNS, connection, and timeout failures mean provider egress is not ready. An
  S3 Gateway endpoint only covers raw archive writes to S3.
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
  --payload '{"stockbrief_operation":"get_ingestion_status","tickers":["005930"],"limit":10}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/stockbrief-ingestion-status-response.json \
  --profile stockbrief-dev \
  --region ap-northeast-2
```

Expected result:

- Response `ok` is `true`.
- `summary.run_status_counts.succeeded` increases after successful provider
  runs.
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

- Both OpenDART and NAVER manual smoke runs have completed with understood
  results.
- Stale `started` ingestion runs have been reviewed with
  `reconcile_stale_ingestion_runs` dry-run and reconciled if needed.
- `ingestion_runs`, normalized provider tables, `source_documents`, S3 raw
  archive, DLQ, and CloudWatch logs have been checked.
- Provider rate limits, ticker count, and expected execution frequency have
  been reviewed.
- The reviewed dev scheduler job list is explicit. For the first scheduled
  rollout, use `OpenDART` and `NAVER_NEWS` for ticker `005930` with the weekday
  18:00 KST expression `cron(0 18 ? * MON-FRI *)`.
- Lambda outbound internet egress is confirmed by `check_provider_egress`.
- The scheduler change is reviewed in a separate PR.

If any check fails, keep `enable_ingestion_scheduler = false`, record the
blocking condition, and fix the smallest failing layer first.
