import json
import os
import re
import subprocess
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


def test_backend_dev_deploy_checks_assumed_account_matches_backend() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/backend-dev-deploy.yml"
    ).read_text(encoding="utf-8")
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "Verify deploy account matches Terraform backend" in workflow
    assert "target_env:" in workflow
    assert 'TARGET_ENV: ${{ github.event.inputs.target_env || \'dev\' }}' in workflow
    assert "backends/{target_env}.hcl" in workflow
    assert "envs/{target_env}/deploy.auto.tfvars.json" in workflow
    assert "TF_BACKEND_CONFIG: ${{ steps.deploy-profile.outputs.tf_backend_config }}" in workflow
    assert "scripts/verify_deploy_account_matches_backend.sh" in workflow
    assert "Before Terraform init, `backend-dev-deploy` compares the account" in deployment_doc
    assert "cannot accidentally deploy against a backend that" in deployment_doc
    assert "During account transition work, this failure is the expected guardrail" in deployment_doc
    assert "not as a deployment regression" in deployment_doc


def test_backend_dev_deploy_supports_target_environment_profiles() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/backend-dev-deploy.yml"
    ).read_text(encoding="utf-8")
    bootstrap_doc = (
        REPOSITORY_ROOT / "docs/engineering/NEW_AWS_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "Resolve deploy profile" in workflow
    assert 'target_env != "dev" and not target_env.startswith("dev-")' in workflow
    assert "backend-dev-deploy only accepts dev or dev-* target_env values" in workflow
    assert "AWS_{target_env.upper().replace('-', '_')}_DEPLOY_ROLE_ARN" in workflow
    assert 'variables.get("TF_BACKEND_CONFIG_HCL")' in workflow
    assert 'variables.get("TFVARS_JSON")' in workflow
    assert "TFVARS_JSON environment must match target_env" in workflow
    assert 'json.dump(parsed_tfvars, tfvars_file, ensure_ascii=False, indent=2)' in workflow
    assert "Missing deploy profile file(s):" in workflow
    assert 'os.path.join(os.environ["TF_DIR"], tf_var_file)' in workflow
    assert 'os.path.join(os.environ["TF_DIR"], tf_backend_config)' in workflow
    assert 'terraform init' in workflow
    assert '-backend-config="${{ steps.deploy-profile.outputs.tf_backend_config }}"' in workflow
    assert '-var-file="${{ steps.deploy-profile.outputs.tf_var_file }}"' in workflow
    assert "target_env=dev-junwoo" in bootstrap_doc
    assert "backends/dev-junwoo.hcl" in bootstrap_doc
    assert "envs/dev-junwoo/deploy.auto.tfvars.json" in bootstrap_doc
    assert "`target_env=dev` 또는" in bootstrap_doc
    assert "`target_env=dev-*`만 허용" in bootstrap_doc
    assert "TF_BACKEND_CONFIG_HCL" in bootstrap_doc
    assert "TFVARS_JSON" in bootstrap_doc
    assert "`amplify_cognito_redirect_uri`는 `enable_amplify=false`" in bootstrap_doc
    assert "`agentcore_runtime_container_uri`는 `agentcore_runtime_enabled=false`" in bootstrap_doc


def test_new_aws_bootstrap_documents_manual_amplify_account_switching() -> None:
    bootstrap_doc = (
        REPOSITORY_ROOT / "docs/engineering/NEW_AWS_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "FE Amplify 콘솔 수동 생성 방법" in bootstrap_doc
    assert "현재 활성 AWS 계정마다 Amplify app을 하나 만든다" in bootstrap_doc
    assert "NEXT_PUBLIC_API_BASE_URL=<api_base_url>/v1" in bootstrap_doc
    assert "NEXT_PUBLIC_COGNITO_USER_POOL_ID=<cognito_user_pool_id>" in bootstrap_doc
    assert "NEXT_PUBLIC_COGNITO_APP_CLIENT_ID=<cognito_app_client_id>" in bootstrap_doc
    assert "NEXT_PUBLIC_COGNITO_HOSTED_UI_DOMAIN=<cognito_hosted_ui_domain에서 https:// 제거>" in bootstrap_doc
    assert "callback: https://main.<amplify-default-domain>/auth/callback" in bootstrap_doc
    assert "Amplify access token이나" in bootstrap_doc


def test_deploy_account_guard_accepts_matching_accounts(tmp_path: Path) -> None:
    result = _run_deploy_account_guard(
        tmp_path,
        assumed_account="123456789012",
        role_arn="arn:aws:iam::123456789012:role/stockbrief-dev-github-actions-deploy",
        state_bucket="stockbrief-terraform-state-123456789012-ap-northeast-2",
    )

    assert result.returncode == 0
    assert "Verified deploy account 123456789012 matches" in result.stdout


def test_deploy_account_guard_accepts_matching_backend_config(
    tmp_path: Path,
) -> None:
    backend_config = tmp_path / "dev-member.hcl"
    backend_config.write_text(
        '''
bucket = "stockbrief-terraform-state-123456789012-ap-northeast-2"
key    = "stockbrief/dev-member/terraform.tfstate"
region = "ap-northeast-2"
''',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "scripts/verify_deploy_account_matches_backend.sh"],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "ASSUMED_AWS_ACCOUNT_ID": "123456789012",
            "DEPLOY_ROLE_ARN": "arn:aws:iam::123456789012:role/stockbrief-dev-member-github-actions-deploy",
            "TF_DIR": str(tmp_path),
            "TF_BACKEND_CONFIG": "dev-member.hcl",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "dev-member.hcl" in result.stdout


def test_deploy_account_guard_rejects_unparseable_role_arn(tmp_path: Path) -> None:
    result = _run_deploy_account_guard(
        tmp_path,
        assumed_account="123456789012",
        role_arn="not-an-iam-role-arn",
        state_bucket="stockbrief-terraform-state-123456789012-ap-northeast-2",
    )

    assert result.returncode == 1
    assert "Could not parse AWS account id from AWS_DEV_DEPLOY_ROLE_ARN" in result.stdout


def test_deploy_account_guard_rejects_unparseable_backend_bucket(tmp_path: Path) -> None:
    result = _run_deploy_account_guard(
        tmp_path,
        assumed_account="123456789012",
        role_arn="arn:aws:iam::123456789012:role/stockbrief-dev-github-actions-deploy",
        state_bucket="unexpected-state-bucket",
    )

    assert result.returncode == 1
    assert "Could not parse AWS account id from Terraform backend bucket" in result.stdout


def test_deploy_account_guard_rejects_assumed_role_account_mismatch(
    tmp_path: Path,
) -> None:
    result = _run_deploy_account_guard(
        tmp_path,
        assumed_account="999999999999",
        role_arn="arn:aws:iam::123456789012:role/stockbrief-dev-github-actions-deploy",
        state_bucket="stockbrief-terraform-state-999999999999-ap-northeast-2",
    )

    assert result.returncode == 1
    assert (
        "Assumed AWS account 999999999999 does not match deploy role account 123456789012"
        in result.stdout
    )


def test_deploy_account_guard_rejects_assumed_backend_account_mismatch(
    tmp_path: Path,
) -> None:
    result = _run_deploy_account_guard(
        tmp_path,
        assumed_account="123456789012",
        role_arn="arn:aws:iam::123456789012:role/stockbrief-dev-github-actions-deploy",
        state_bucket="stockbrief-terraform-state-999999999999-ap-northeast-2",
    )

    assert result.returncode == 1
    assert (
        "Assumed AWS account 123456789012 does not match Terraform backend account 999999999999"
        in result.stdout
    )


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


def _run_deploy_account_guard(
    tmp_path: Path,
    *,
    assumed_account: str,
    role_arn: str,
    state_bucket: str,
) -> subprocess.CompletedProcess[str]:
    backend_file = tmp_path / "backend.tf"
    backend_file.write_text(
        f'''
terraform {{
  backend "s3" {{
    bucket = "{state_bucket}"
  }}
}}
''',
        encoding="utf-8",
    )

    return subprocess.run(
        ["bash", "scripts/verify_deploy_account_matches_backend.sh"],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "ASSUMED_AWS_ACCOUNT_ID": assumed_account,
            "DEPLOY_ROLE_ARN": role_arn,
            "TF_BACKEND_FILE": str(backend_file),
        },
        capture_output=True,
        text=True,
        check=False,
    )


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


def _statement(policy: dict[str, object], sid: str) -> dict[str, object]:
    statements = policy["Statement"]
    assert isinstance(statements, list)
    for statement in statements:
        assert isinstance(statement, dict)
        if statement.get("Sid") == sid:
            return statement
    raise AssertionError(f"Policy statement not found: {sid}")


def _statement_actions(policy: dict[str, object], sid: str) -> set[str]:
    actions = _statement(policy, sid)["Action"]
    if isinstance(actions, str):
        return {actions}
    assert isinstance(actions, list)
    return {str(action) for action in actions}


def _statement_resources(policy: dict[str, object], sid: str) -> set[str]:
    resources = _statement(policy, sid)["Resource"]
    if isinstance(resources, str):
        return {resources}
    assert isinstance(resources, list)
    return {str(resource) for resource in resources}


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
    assert "ingestion_schedule_jobs" in scheduler_gate
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


def test_new_aws_bootstrap_uses_placeholders_for_operational_identifiers() -> None:
    bootstrap_doc = (
        REPOSITORY_ROOT / "docs/engineering/NEW_AWS_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "실제 계정 ID, 리소스 ID, API ID, Cognito ID, 도메인은" in bootstrap_doc
    assert "공개 문서에 그대로 남기지 말고" in bootstrap_doc
    assert "<api-gateway-base-url>" in bootstrap_doc
    assert "<cognito-issuer-url>" in bootstrap_doc
    assert "<cognito-user-pool-id>" in bootstrap_doc
    assert "<cognito-app-client-id>" in bootstrap_doc
    assert "<cognito-hosted-ui-domain>" in bootstrap_doc
    assert "<terraform-state-bucket>" in bootstrap_doc
    assert "<terraform-lock-table>" in bootstrap_doc
    assert "<github-actions-deploy-role-arn>" in bootstrap_doc

    assert not re.search(
        r"https://[a-z0-9]+\.execute-api\.ap-northeast-2\.amazonaws\.com",
        bootstrap_doc,
    )
    assert not re.search(r"ap-northeast-2_[A-Za-z0-9]+", bootstrap_doc)
    assert not re.search(
        r"https://[a-z0-9-]+\.auth\.ap-northeast-2\.amazoncognito\.com",
        bootstrap_doc,
    )
    assert "560271561793" not in bootstrap_doc
    assert "3pgg4n3hda2pqf9q8ij9m79glk" not in bootstrap_doc
    assert "stockbrief-terraform-state-560271561793" not in bootstrap_doc
    assert "arn:aws:iam::560271561793:role/" not in bootstrap_doc


def test_deployment_bootstrap_documents_nat_egress_plan_review_checklist() -> None:
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")
    checklist = _markdown_section(deployment_doc, "NAT Egress Plan Review Checklist")

    assert "Before enabling `enable_lambda_nat_egress`" in checklist
    assert "NAT Gateway" in checklist
    assert "Elastic IP for the NAT Gateway" in checklist
    assert "NAT route table" in checklist
    assert "Lambda subnet route table associations" in checklist
    assert "Amplify in-place update" in checklist
    assert "Cognito client in-place update" in checklist
    assert "RDS in-place update" in checklist
    assert "Lambda package hash update" in checklist
    assert "terraform state show module.amplify.aws_amplify_app.this" in checklist
    assert "terraform state show module.cognito.aws_cognito_user_pool_client.client" in checklist
    assert "terraform state show module.rds.aws_db_instance.this" in checklist
    assert "terraform state show module.api_lambda.aws_lambda_function.api" in checklist
    assert "adjust `--profile`, `--region`, and `Name` tag" in checklist
    assert "values to match that environment's Terraform resources" in checklist
    assert "aws ec2 describe-nat-gateways" in checklist
    assert "aws ec2 describe-route-tables" in checklist
    assert "If any non-NAT item is unexplained, do not apply" in checklist


def test_github_deploy_role_policy_scopes_prefix_named_resources() -> None:
    bootstrap_script = (REPOSITORY_ROOT / "scripts/bootstrap_github_oidc.sh").read_text(
        encoding="utf-8"
    )
    deploy_policy = _bootstrap_policy_document(bootstrap_script)
    wildcard_actions = _statement_actions(
        deploy_policy, "DevBackendDeploymentWildcardFallback"
    )
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert 'resource_name_prefix="stockbrief-${environment}"' in bootstrap_script

    assert _statement_resources(deploy_policy, "DeployIamRolesByPrefix") == {
        "arn:aws:iam::${account_id}:role/${resource_name_prefix}-*"
    }
    assert _statement_actions(deploy_policy, "DeployPassRolesByPrefix") == {
        "iam:PassRole"
    }
    assert _statement(deploy_policy, "DeployPassRolesByPrefix")["Condition"] == {
        "StringEquals": {
            "iam:PassedToService": [
                "bedrock-agentcore.amazonaws.com",
                "lambda.amazonaws.com",
                "rds.amazonaws.com",
                "scheduler.amazonaws.com",
            ]
        }
    }
    assert _statement_resources(deploy_policy, "DeployLambdaFunctionByPrefix") == {
        "arn:aws:lambda:${region}:${account_id}:function:${resource_name_prefix}-*"
    }
    assert _statement_resources(deploy_policy, "DeployLogGroupsByPrefix") == {
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/amplify/${resource_name_prefix}-web",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/amplify/${resource_name_prefix}-web:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/apigateway/${resource_name_prefix}-http-api",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/apigateway/${resource_name_prefix}-http-api:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/lambda/${resource_name_prefix}-api",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/lambda/${resource_name_prefix}-api:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/rds/${resource_name_prefix}-postgres",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/rds/${resource_name_prefix}-postgres:*",
    }
    assert _statement_resources(deploy_policy, "DeploySecretsByPrefix") == {
        "arn:aws:secretsmanager:${region}:${account_id}:secret:${resource_name_prefix}/*"
    }
    assert _statement_resources(deploy_policy, "DeployRdsManagedMasterUserSecret") == {
        "arn:aws:secretsmanager:${region}:${account_id}:secret:rds!db-*"
    }
    assert _statement_actions(deploy_policy, "DeployRdsManagedMasterUserSecret") == {
        "secretsmanager:CreateSecret",
        "secretsmanager:DescribeSecret",
        "secretsmanager:TagResource",
    }
    assert _statement_resources(deploy_policy, "DeploySqsQueuesByPrefix") == {
        "arn:aws:sqs:${region}:${account_id}:${resource_name_prefix}-*"
    }
    assert _statement_resources(deploy_policy, "DeploySchedulesByPrefix") == {
        "arn:aws:scheduler:${region}:${account_id}:schedule/default/${resource_name_prefix}-*"
    }
    assert _statement_resources(deploy_policy, "DeployIngestionRawBucketByPrefix") == {
        "arn:aws:s3:::${resource_name_prefix}-raw-${account_id}-${region}",
        "arn:aws:s3:::${resource_name_prefix}-raw-${account_id}-${region}/*",
    }
    assert "s3:PutEncryptionConfiguration" in _statement_actions(
        deploy_policy, "TerraformStateBucket"
    )
    assert "s3:PutEncryptionConfiguration" in _statement_actions(
        deploy_policy, "DeployIngestionRawBucketByPrefix"
    )

    for scoped_sid, action in [
        ("DeployLambdaFunctionByPrefix", "lambda:UpdateFunctionCode"),
        ("DeployIamRolesByPrefix", "iam:CreateRole"),
        ("DeploySecretsByPrefix", "secretsmanager:CreateSecret"),
        ("DeployIngestionRawBucketByPrefix", "s3:GetBucketPublicAccessBlock"),
        ("DeploySqsQueuesByPrefix", "sqs:GetQueueAttributes"),
        ("DeploySchedulesByPrefix", "scheduler:CreateSchedule"),
        ("DeploySnsTopicsByPrefix", "sns:CreateTopic"),
        ("DeployCloudWatchAlarmsByPrefix", "cloudwatch:PutMetricAlarm"),
        ("DeployLogGroupsByPrefix", "logs:PutRetentionPolicy"),
    ]:
        assert action in _statement_actions(deploy_policy, scoped_sid)
        assert action not in wildcard_actions

    for action in [
        "kms:DescribeKey",
        "kms:GetKeyPolicy",
        "kms:GetKeyRotationStatus",
        "apigateway:*",
        "ec2:CreateNatGateway",
        "ec2:DescribeNatGateways",
        "ec2:AllocateAddress",
        "ec2:DescribeAddressesAttribute",
        "ec2:CreateRouteTable",
        "ec2:AssociateRouteTable",
        "rds:CreateDBProxy",
        "logs:CreateLogGroup",
        "logs:TagResource",
        "cloudwatch:DescribeAlarms",
    ]:
        assert action in wildcard_actions

    assert "lambda:*" not in bootstrap_script
    assert "iam:*" not in bootstrap_script
    assert "s3:PutBucketEncryption" not in bootstrap_script
    assert "s3:DeleteBucketEncryption" not in bootstrap_script
    assert "s3:DeleteBucketPublicAccessBlock" not in bootstrap_script
    assert "s3:DeleteBucketTagging" not in bootstrap_script
    assert "s3:DeleteLifecycleConfiguration" not in bootstrap_script
    assert "Terraform refresh" in deployment_doc
    assert "deploy role" in deployment_doc
    assert "stockbrief-<environment>-*" in deployment_doc
    assert "wildcard fallback statement" in deployment_doc
    assert "API Gateway stage creation" in deployment_doc
    assert "Access Analyzer reports" in deployment_doc
    assert "`apigateway:TagResource`" in deployment_doc
    assert "`apigateway:UntagResource`" in deployment_doc
    assert "`apigateway:*`" in deployment_doc
    assert "Analyzer-valid narrower action set" in deployment_doc
    assert "Prefer adding a narrow" in deployment_doc
    assert "PR #164 covers only the apply blocker" in deployment_doc
    assert "It does not close #52 by itself" in deployment_doc
    assert "`logs:TagResource` addition" in deployment_doc
    assert "future narrowing candidate in #52" in deployment_doc
    assert "managed master user password secrets" in deployment_doc
    assert "AWS's" in deployment_doc
    assert "`rds!db-*` naming" in deployment_doc
    assert "After PR #164 merges" in deployment_doc
    assert "live" in deployment_doc
    assert "deploy role inline policy" in deployment_doc
    assert "no longer fails on" in deployment_doc
    assert "`rds!db-*` exception remains part of the least-privilege" in deployment_doc
    assert "Keep the least-privilege hardening issue open" in deployment_doc
    assert "`backend-dev-deploy` verification are complete" in deployment_doc


def test_bootstrap_reconciles_dev_environment_branch_policy_to_main_only() -> None:
    bootstrap_script = (REPOSITORY_ROOT / "scripts/bootstrap_github_oidc.sh").read_text(
        encoding="utf-8"
    )
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    policy_reconciliation = bootstrap_script[
        bootstrap_script.index("obsolete_branch_policies=") :
        bootstrap_script.index("echo \"Setting GitHub repository variables")
    ]

    assert "obsolete_branch_policies" in policy_reconciliation
    assert '.name != \\"${branch_escaped}\\"' in policy_reconciliation
    assert "[.name, .id] | @tsv" in policy_reconciliation
    assert "Obsolete GitHub Environment branch policies" in policy_reconciliation
    assert (
        "name=${obsolete_branch_policy_name} id=${obsolete_branch_policy_id}"
        in policy_reconciliation
    )
    assert (
        "deployment-branch-policies/${obsolete_branch_policy_id}"
        in policy_reconciliation
    )
    assert "gh api --method DELETE" in policy_reconciliation
    assert "run_change gh api --method DELETE" in policy_reconciliation
    assert "|| true" not in policy_reconciliation
    assert "allow only the\n`main` branch" in deployment_doc


def test_bootstrap_dry_run_guards_write_actions() -> None:
    bootstrap_script = (REPOSITORY_ROOT / "scripts/bootstrap_github_oidc.sh").read_text(
        encoding="utf-8"
    )
    deployment_doc = (
        REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")

    assert "--dry-run" in bootstrap_script
    assert "dry_run=\"false\"" in bootstrap_script
    assert "Dry-run mode enabled" in bootstrap_script
    assert "run_change()" in bootstrap_script
    assert "DRY RUN: %s" in bootstrap_script

    for write_call in [
        "run_change aws s3api create-bucket",
        "run_change aws s3api put-bucket-versioning",
        "run_change aws dynamodb create-table",
        "run_change aws iam create-open-id-connect-provider",
        "run_change aws iam update-assume-role-policy",
        "run_change aws iam create-role",
        "run_change aws iam put-role-policy",
        "run_change gh api --method PUT",
        "run_change gh api --method POST",
        "run_change gh api --method DELETE",
        "run_change gh variable set",
    ]:
        assert write_call in bootstrap_script

    assert "scripts/bootstrap_github_oidc.sh --dry-run" in deployment_doc
    assert (
        "Run without `--dry-run` only after reviewing the planned changes"
        in deployment_doc
    )
    assert "branch name and policy ID" in deployment_doc
