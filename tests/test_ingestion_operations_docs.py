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
    assert "aws lambda invoke" in runbook
    assert '"stockbrief_operation":"ingest_provider_batch"' in runbook
    assert '"provider":"OpenDART"' in runbook
    assert '"provider":"NAVER_NEWS"' in runbook
    assert '"source_date":"YYYY-MM-DD"' in runbook
    assert "Replace `YYYY-MM-DD`" in runbook
    assert "with the business date you want to" in runbook
    assert "missing_api_key" in runbook
    assert "replayed or duplicate-safe result" in runbook


def test_ingestion_operations_runbook_covers_storage_dlq_and_logs() -> None:
    runbook = _runbook()

    assert "select run_id, provider, status, source_date" in runbook
    assert "from ingestion_runs" in runbook
    assert "from source_documents" in runbook
    assert "from disclosures" in runbook
    assert "from news_items" in runbook
    assert "aws s3api list-objects-v2" in runbook
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
    assert "do not contain API keys, client secrets, tokens, or database" in runbook
    assert "Lambda outbound internet egress is confirmed" in runbook
    assert "reviewed in a separate PR" in runbook
