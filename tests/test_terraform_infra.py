from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = REPOSITORY_ROOT / "infra/terraform"


def _read(path: str) -> str:
    return (TERRAFORM_ROOT / path).read_text(encoding="utf-8")


def test_amplify_module_targets_frontend_repository_root() -> None:
    main_tf = _read("modules/amplify/main.tf")

    assert "appRoot: ." in main_tf
    assert "apps/web" not in main_tf


def test_rds_proxy_module_exists_and_is_wired_to_api_lambda() -> None:
    rds_proxy_tf = _read("modules/rds_proxy/main.tf")
    rds_proxy_variables_tf = _read("modules/rds_proxy/variables.tf")
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    api_lambda_tf = _read("modules/api_lambda/main.tf")

    assert "aws_db_proxy" in rds_proxy_tf
    assert "aws_db_proxy_default_target_group" in rds_proxy_tf
    assert "aws_db_proxy_target" in rds_proxy_tf
    assert "var.enabled" in rds_proxy_tf
    assert 'variable "enabled"' in rds_proxy_variables_tf
    assert 'variable "enable_rds_proxy"' in variables_tf
    assert 'module "rds_proxy"' in root_main_tf
    assert "enabled                = var.enable_rds_proxy" in root_main_tf
    assert "DATABASE_HOST" in api_lambda_tf
    assert "DATABASE_PORT" in api_lambda_tf
    assert "DATABASE_NAME" in api_lambda_tf


def test_dev_rds_cost_controls_are_environment_variables() -> None:
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")
    prod_tfvars = _read("envs/prod/terraform.tfvars.example")

    assert 'variable "db_deletion_protection"' in variables_tf
    assert 'variable "db_skip_final_snapshot"' in variables_tf
    assert 'variable "db_backup_retention_period"' in variables_tf
    assert "deletion_protection     = var.db_deletion_protection" in root_main_tf
    assert "skip_final_snapshot     = var.db_skip_final_snapshot" in root_main_tf
    assert "backup_retention_period = var.db_backup_retention_period" in root_main_tf

    assert "db_deletion_protection     = false" in dev_tfvars
    assert "db_skip_final_snapshot     = true" in dev_tfvars
    assert "db_backup_retention_period = 1" in dev_tfvars
    assert "enable_rds_proxy" in dev_tfvars

    assert "db_deletion_protection     = true" in prod_tfvars
    assert "db_skip_final_snapshot     = false" in prod_tfvars
    assert "db_backup_retention_period = 7" in prod_tfvars


def test_new_aws_bootstrap_does_not_pin_old_dev_account() -> None:
    backend_tf = _read("backend.tf")
    deploy_tfvars = _read("envs/dev/deploy.auto.tfvars.json")

    assert "420615923610" not in backend_tf
    assert "420615923610" not in deploy_tfvars
    assert "REPLACE_WITH_ACCOUNT_ID" not in backend_tf
    assert "stockbrief-terraform-state-" in backend_tf
    assert "vpc-0fdabc1f990027c99" not in deploy_tfvars
    assert "subnet-08f5ab10f709efd3e" not in deploy_tfvars
    assert "subnet-0940fc5ef61437e6d" not in deploy_tfvars
    assert '"vpc_id": "vpc-07b9f3920d93b65e1"' in deploy_tfvars
    assert "subnet-08d89333a3c3e2924" in deploy_tfvars
    assert "subnet-0e10680a556fa9ca8" in deploy_tfvars


def test_agentcore_runtime_module_uses_cloudformation_resources() -> None:
    agentcore_tf = _read("modules/agentcore_runtime/main.tf")
    root_main_tf = _read("main.tf")

    assert "AWS::BedrockAgentCore::Runtime" in agentcore_tf
    assert "AWS::BedrockAgentCore::RuntimeEndpoint" in agentcore_tf
    assert "agentcore_runtime_container_uri" in root_main_tf


def test_api_gateway_stage_has_access_logs() -> None:
    api_lambda_tf = _read("modules/api_lambda/main.tf")
    api_lambda_variables_tf = _read("modules/api_lambda/variables.tf")
    root_main_tf = _read("main.tf")

    assert "access_log_settings" in api_lambda_tf
    assert "format = jsonencode" in api_lambda_tf
    assert 'variable "jwt_authorizer_enabled"' in api_lambda_variables_tf
    assert "jwt_authorizer_enabled    = true" in root_main_tf


def test_amplify_hosted_callback_can_be_overridden_after_domain_creation() -> None:
    root_main_tf = _read("main.tf")
    outputs_tf = _read("outputs.tf")
    variables_tf = _read("variables.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")

    assert 'variable "amplify_cognito_redirect_uri"' in variables_tf
    assert "amplify_cognito_redirect_uri" in dev_tfvars
    assert "amplify_cognito_redirect_uri == \"\" ? var.cognito_callback_urls[0]" in root_main_tf
    assert 'output "amplify_default_domain"' in outputs_tf


def test_api_lambda_role_has_vpc_and_agentcore_invoke_permissions() -> None:
    api_lambda_tf = _read("modules/api_lambda/main.tf")

    assert "AWSLambdaVPCAccessExecutionRole" in api_lambda_tf
    assert "bedrock-agentcore:InvokeAgentRuntime" in api_lambda_tf


def test_ingestion_pipeline_resources_are_wired_with_scheduler_disabled_by_default() -> None:
    ingestion_tf = _read("ingestion.tf")
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    outputs_tf = _read("outputs.tf")
    api_lambda_tf = _read("modules/api_lambda/main.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")

    assert 'variable "enable_ingestion_scheduler"' in variables_tf
    assert "default     = false" in variables_tf
    assert "aws_s3_bucket\" \"ingestion_raw" in ingestion_tf
    assert "aws_kms_key\" \"ingestion_raw" in ingestion_tf
    assert "enable_key_rotation     = true" in ingestion_tf
    assert 'sse_algorithm     = "aws:kms"' in ingestion_tf
    assert "aws_sqs_queue\" \"ingestion_dlq" in ingestion_tf
    assert "sqs_managed_sse_enabled   = true" in ingestion_tf
    assert "aws_scheduler_schedule\" \"provider_ingestion" in ingestion_tf
    assert "local.ingestion_scheduler_enabled" in ingestion_tf
    assert "length(var.ingestion_schedule_tickers) > 0" in ingestion_tf
    assert "stockbrief_operation = \"ingest_provider_batch\"" in ingestion_tf
    assert "raise_on_failure     = true" in ingestion_tf
    assert "INGESTION_RAW_BUCKET" in root_main_tf
    assert "INGESTION_RAW_BUCKET" in api_lambda_tf
    assert "s3:PutObject" in api_lambda_tf
    assert "kms:GenerateDataKey" in api_lambda_tf
    assert 'output "ingestion_raw_bucket_name"' in outputs_tf
    assert 'output "ingestion_raw_kms_key_arn"' in outputs_tf
    assert 'output "ingestion_dlq_url"' in outputs_tf
    assert "enable_ingestion_scheduler          = false" in dev_tfvars


def test_secret_versions_do_not_reclaim_manually_rotated_current_values() -> None:
    secrets_tf = _read("modules/secrets/main.tf")

    assert secrets_tf.count("ignore_changes = all") == 2
