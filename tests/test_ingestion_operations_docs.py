from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = REPOSITORY_ROOT / "docs/engineering/INGESTION_OPERATIONS_RUNBOOK.md"


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_ingestion_operations_runbook_exists_and_covers_manual_smoke() -> None:
    runbook = _runbook()

    assert "# Ingestion Operations Runbook" in runbook
    assert "aws sts get-caller-identity --profile stockbrief-dev" in runbook
    assert "terraform output ingestion_raw_bucket_name" in runbook
    assert "terraform output ingestion_dlq_url" in runbook
    assert "terraform output external_api_secret_arn" in runbook
    assert "enable_ingestion_scheduler" in runbook
    assert '"stockbrief_operation":"check_provider_egress"' in runbook
    assert '"providers":["OpenDART","NAVER_NEWS"]' in runbook
    assert "does not send API keys or client secrets" in runbook
    assert "DNS, connection, and timeout failures" in runbook
    assert "aws lambda invoke" in runbook
    assert "scripts/check_ingestion_smoke.py" in runbook
    assert "--run-provider-ingest" in runbook
    assert "It redacts secret-like fields" in runbook
    assert '"stockbrief_operation":"ingest_provider_batch"' in runbook
    assert '"provider":"OpenDART"' in runbook
    assert '"provider":"NAVER_NEWS"' in runbook
    assert '"source_date":"YYYY-MM-DD"' in runbook
    assert '"stockbrief_operation":"get_ingestion_status"' in runbook
    assert '"providers":["NAVER_NEWS"]' in runbook
    assert "summary.run_status_counts.succeeded" in runbook
    assert "summary.ticker_filter" in runbook
    assert "summary.provider_filter" in runbook
    assert "latest_evidence[]" in runbook
    assert '"stockbrief_operation":"reconcile_stale_ingestion_runs"' in runbook
    assert '"dry_run":true' in runbook
    assert '"dry_run":false' in runbook
    assert "stale_started_run_reconciled" in runbook
    assert "OpenDART` and `NAVER_NEWS` for ticker `005930`" in runbook
    assert "cron(0 18 ? * MON-FRI *)" in runbook
    assert "The current reviewed dev" in runbook
    assert "jobs use `OpenDART` and `NAVER_NEWS`" in runbook
    assert "keep the job list empty in dev tfvars" in runbook
    assert "until" in runbook
    assert "provider egress and the scheduler gate pass again" in runbook
    assert "The scheduler change is reviewed in a separate PR" in runbook
    assert '"stockbrief_operation":"check_ingestion_scheduler_enable_gate"' in runbook
    assert "scheduler_enable_ready=true" in runbook
    assert "Replace `YYYY-MM-DD`" in runbook
    assert "with the business date you want to" in runbook
    assert "missing_api_key" in runbook
    assert "replayed or duplicate-safe result" in runbook


def test_ingestion_operations_runbook_covers_storage_dlq_and_logs() -> None:
    runbook = _runbook()

    assert "select run_id, provider, status, source_date" in runbook
    assert "from ingestion_runs" in runbook
    assert "from source_documents" in runbook
    assert "from evidence_chunks" in runbook
    assert "Provider rows create `evidence_chunks`" in runbook
    assert "from disclosures" in runbook
    assert "from news_items" in runbook
    assert "aws s3api list-objects-v2" in runbook
    assert '"stockbrief_operation":"check_raw_archive_write"' in runbook
    assert "checks.raw_archive.write_verified" in runbook
    assert '--prefix "raw/provider=OpenDART/ticker=005930/"' in runbook
    assert "raw/provider=NAVER_NEWS/ticker=005930/" in runbook
    assert "raw_archive_uri" in runbook
    assert "aws s3api head-object" in runbook
    assert "REPLACE_WITH_RUN_ID" in runbook
    assert "aws sqs get-queue-attributes" in runbook
    assert "ApproximateNumberOfMessages" in runbook
    assert "aws logs filter-log-events" in runbook
    assert "/aws/lambda/stockbrief-dev-api" in runbook


def test_ingestion_operations_runbook_keeps_secret_handling_safe() -> None:
    runbook = _runbook()

    assert "Do not paste API keys" in runbook
    assert "full provider" in runbook
    assert "payloads into PR comments" in runbook
    assert "not copied into PR comments" in runbook
    assert "scripts/update_external_api_secret.sh --prompt --dry-run" in runbook
    assert "scripts/update_external_api_secret.sh --prompt" in runbook
    assert "--secret-id" in runbook
    assert "get-secret-value" in runbook
    assert "do not contain API keys, client secrets, tokens, or database" in runbook
    assert "Lambda outbound internet egress is confirmed" in runbook
    assert "check_provider_egress" in runbook
    assert "reviewed in a separate PR" in runbook
