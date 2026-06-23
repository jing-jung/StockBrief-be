import json
import re
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
    assert "Deterministic Lambda packages include regular files only" in script
    assert "symlinks are intentionally excluded" in script
    assert "find . -type f | LC_ALL=C sort | zip -X" in script
    assert "services/api" not in script


def test_backend_ci_checks_lambda_packaging_script_on_pr() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/backend-ci.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request:" in workflow
    assert "Check Lambda packaging script syntax" in workflow
    assert "bash -n scripts/package_api_lambda.sh" in workflow
    assert "Verify deterministic Lambda package" in workflow
    assert "./scripts/package_api_lambda.sh" in workflow
    assert "sha256sum dist/stockbrief-api-lambda.zip" in workflow
    assert 'test "$first_hash" = "$second_hash"' in workflow


def test_external_api_secret_update_script_handles_secret_payload_safely() -> None:
    script = (REPOSITORY_ROOT / "scripts/update_external_api_secret.sh").read_text(
        encoding="utf-8"
    )

    assert "set -euo pipefail" in script
    assert "OPENDART_API_KEY" in script
    assert "NAVER_CLIENT_ID" in script
    assert "NAVER_CLIENT_SECRET" in script
    assert "--prompt" in script
    assert "prompt_secret OPENDART_API_KEY" in script
    assert "read -r -s -p" in script
    assert "Missing required environment variables" in script
    assert "mktemp" in script
    assert "trap cleanup EXIT" in script
    assert 'AWS_PROFILE="$profile"' in script
    assert 'AWS_REGION="$region"' in script
    assert 'AWS_DEFAULT_REGION="$region"' in script
    assert 'terraform -chdir="$terraform_dir" output -raw external_api_secret_arn' in script
    assert "aws secretsmanager update-secret" in script
    assert '--secret-string "file://${tmp_payload}"' in script
    assert "aws secretsmanager describe-secret" in script
    assert "get-secret-value" not in script


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


def _markdown_section(markdown: str, heading: str) -> str:
    start_marker = f"### {heading}"
    start = markdown.index(start_marker)
    next_heading = markdown.find("\n## ", start + len(start_marker))
    if next_heading == -1:
        return markdown[start:]
    return markdown[start:next_heading]


def _bootstrap_policy_document(script: str) -> dict[str, object]:
    match = re.search(
        r'cat >"\$\{tmpdir\}/deploy-policy\.json" <<POLICY\n(?P<policy>.*?)\nPOLICY',
        script,
        re.DOTALL,
    )
    assert match is not None
    policy = json.loads(match.group("policy"))
    assert isinstance(policy, dict)
    return policy


def _statement_actions(policy: dict[str, object], sid: str) -> set[str]:
    statements = policy["Statement"]
    assert isinstance(statements, list)
    for statement in statements:
        assert isinstance(statement, dict)
        if statement.get("Sid") == sid:
            actions = statement["Action"]
            assert isinstance(actions, list)
            return {str(action) for action in actions}
    raise AssertionError(f"Policy statement not found: {sid}")


def test_ingestion_scheduler_enable_gate_documents_live_provider_prerequisites() -> None:
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )
    scheduler_gate = _markdown_section(terraform_readme, "Scheduler Enable Gate")

    assert "Scheduler Enable Gate" in scheduler_gate
    assert "Secrets Manager" in scheduler_gate
    assert "ingest_provider_batch" in scheduler_gate
    assert "check_provider_egress" in scheduler_gate
    assert "outbound internet egress" in scheduler_gate
    assert "S3 raw archive" in scheduler_gate
    assert "DLQ" in scheduler_gate
    assert "rate-limit" in scheduler_gate
    assert "data freshness" in scheduler_gate
    assert "keep the scheduler disabled" in scheduler_gate


def test_terraform_readme_documents_external_api_secret_update_runbook() -> None:
    terraform_readme = (REPOSITORY_ROOT / "infra/terraform/README.md").read_text(
        encoding="utf-8"
    )

    assert "### External API Credential Update Runbook" in terraform_readme
    assert "terraform output -raw external_api_secret_arn" in terraform_readme
    assert "scripts/update_external_api_secret.sh --prompt --dry-run" in terraform_readme
    assert "scripts/update_external_api_secret.sh --prompt" in terraform_readme
    assert "through `AWS_PROFILE`, `AWS_REGION`, and" in terraform_readme
    assert "`AWS_DEFAULT_REGION`" in terraform_readme
    assert "passes `--profile`/`--region` to AWS Secrets" in terraform_readme
    assert "`--secret-id`" in terraform_readme
    assert "skip Terraform state lookup" in terraform_readme
    assert "aws secretsmanager update-secret" in terraform_readme
    assert "`file://`" in terraform_readme
    assert "aws secretsmanager describe-secret" in terraform_readme
    assert "Do not use `get-secret-value`" in terraform_readme
    assert "aws lambda invoke" in terraform_readme
    assert '"stockbrief_operation": "check_provider_egress"' in terraform_readme
    assert '"providers": ["OpenDART", "NAVER_NEWS"]' in terraform_readme
    assert "does not send API keys or client secrets" in terraform_readme
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


def test_github_deploy_role_policy_can_refresh_ingestion_and_nat_resources() -> None:
    bootstrap_script = (REPOSITORY_ROOT / "scripts/bootstrap_github_oidc.sh").read_text(
        encoding="utf-8"
    )
    deploy_policy = _bootstrap_policy_document(bootstrap_script)
    deployment_actions = _statement_actions(deploy_policy, "DevBackendDeployment")
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    for action in [
        "kms:DescribeKey",
        "kms:GetKeyPolicy",
        "kms:GetKeyRotationStatus",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetLifecycleConfiguration",
        "s3:DeleteBucketPublicAccessBlock",
        "s3:DeleteBucketEncryption",
        "s3:DeleteLifecycleConfiguration",
        "s3:GetBucketAcl",
        "s3:GetBucketOwnershipControls",
        "sqs:GetQueueAttributes",
        "ec2:CreateNatGateway",
        "ec2:DescribeNatGateways",
        "ec2:AllocateAddress",
        "ec2:DescribeAddressesAttribute",
        "ec2:CreateRouteTable",
        "ec2:AssociateRouteTable",
    ]:
        assert action in deployment_actions
    assert "Terraform refresh" in deployment_doc
    assert "deploy role" in deployment_doc
    assert "EIP address" in deployment_doc
    assert "attributes/route table state" in deployment_doc
