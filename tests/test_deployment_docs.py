from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

SECRET_ENV_KEYS = {
    "DATABASE_URL",
    "OPENDART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "KRX_DATA_PATH",
}


def test_env_example_secret_keys_match_terraform_secret_docs() -> None:
    env_example = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )

    for key in SECRET_ENV_KEYS:
        assert f"{key}=" in env_example, f".env.example missing secret-like key: {key}"
        assert key in terraform_readme, f"Terraform README secret list missing key: {key}"


def test_lambda_handler_target_is_documented_and_importable() -> None:
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )

    assert "app.lambda_handler.handler" in terraform_readme
    from app.lambda_handler import handler

    assert handler is not None


def test_lambda_packaging_script_targets_backend_repository_root() -> None:
    script = (REPOSITORY_ROOT / "scripts/package_api_lambda.sh").read_text(
        encoding="utf-8"
    )

    assert 'API_DIR="${ROOT_DIR}"' in script
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3.13}"' in script
    assert 'LAMBDA_PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"' in script
    assert '"boto3", "botocore", "uvicorn"' in script
    assert '"${PYTHON_BIN}" -m pip install' in script
    assert '--platform "${LAMBDA_PLATFORM}"' in script
    assert "--only-binary=:all:" in script
    assert 'cp -R "${API_DIR}/app" "${BUILD_DIR}/app"' in script
    assert "services/api" not in script


def test_lambda_terraform_resource_tracks_package_hash() -> None:
    module_main = (
        REPOSITORY_ROOT / "infra/terraform/modules/api_lambda/main.tf"
    ).read_text(encoding="utf-8")

    assert "source_code_hash" in module_main
    assert "filebase64sha256(var.package_path)" in module_main


def test_terraform_readme_documents_multi_repository_layout() -> None:
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )

    assert "StockBrief-fe" in terraform_readme
    assert "apps/web" not in terraform_readme
    assert "services/api" not in terraform_readme


def test_terraform_readme_documents_external_api_secret_update_runbook() -> None:
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )

    assert "### External API Credential Update Runbook" in terraform_readme
    assert "terraform output -raw external_api_secret_arn" in terraform_readme
    assert "/tmp/stockbrief-external-api-secret.json" in terraform_readme
    assert "aws secretsmanager update-secret" in terraform_readme
    assert "--secret-string file:///tmp/stockbrief-external-api-secret.json" in terraform_readme
    assert "aws secretsmanager describe-secret" in terraform_readme
    assert "Do not use `get-secret-value`" in terraform_readme
    assert "aws lambda invoke" in terraform_readme
    assert '"provider":"OpenDART"' in terraform_readme
    assert '"provider":"NAVER_NEWS"' in terraform_readme
    assert '"source_date":"YYYY-MM-DD"' in terraform_readme
    assert "Replace `YYYY-MM-DD` with the business date you want to verify" in terraform_readme
    assert "missing_api_key" in terraform_readme
    assert "outbound internet egress" in terraform_readme


def test_deployment_bootstrap_documents_dev_cost_pause_and_resume() -> None:
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "## Dev Cost Pause And Resume Runbook" in deployment_doc
    assert "aws rds stop-db-instance" in deployment_doc
    assert "aws rds start-db-instance" in deployment_doc
    assert "aws rds wait db-instance-available" in deployment_doc
    assert "RDS stop is a short-term pause control" in deployment_doc
    assert "stop it again if AWS has returned it to `available`" in deployment_doc
    assert "aws lambda put-function-concurrency" in deployment_doc
    assert "--reserved-concurrent-executions 0" in deployment_doc
    assert "aws lambda delete-function-concurrency" in deployment_doc
    assert "enable_ingestion_scheduler = false" in deployment_doc
    assert "terraform plan -var-file=envs/dev/deploy.auto.tfvars.json" in deployment_doc
    assert "Do not delete Terraform-managed resources from the AWS console" in deployment_doc
    assert "Do not use `terraform apply` as a blind repair step" in deployment_doc
