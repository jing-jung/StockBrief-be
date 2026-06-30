import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = REPOSITORY_ROOT / "infra/terraform"


def _read(path: str) -> str:
    return (TERRAFORM_ROOT / path).read_text(encoding="utf-8")


def test_multi_account_dev_profile_templates_are_available() -> None:
    variables_tf = _read("variables.tf")
    dev_backend = _read("backends/dev.hcl")
    template_backend = _read("backends/dev-template.hcl.example")
    template_tfvars = json.loads(_read("envs/dev-template/deploy.auto.tfvars.json.example"))

    assert "dev-<member>" in variables_tf
    assert 'regex("^dev-[a-z0-9][a-z0-9-]*$"' in variables_tf
    assert 'bucket         = "stockbrief-terraform-state-560271561793-ap-northeast-2"' in dev_backend
    assert 'key            = "stockbrief/dev/terraform.tfstate"' in dev_backend
    assert "REPLACE_WITH_ACCOUNT_ID" in template_backend
    assert "REPLACE_WITH_TARGET_ENV" in template_backend
    assert not (TERRAFORM_ROOT / "backends/dev-junwoo.hcl").exists()
    assert not (TERRAFORM_ROOT / "envs/dev-junwoo/deploy.auto.tfvars.json").exists()
    assert template_tfvars["environment"] == "REPLACE_WITH_TARGET_ENV"
    assert template_tfvars["enable_amplify"] is False
    assert template_tfvars["enable_lambda_nat_egress"] is False
    assert template_tfvars["enable_ingestion_scheduler"] is False
    assert template_tfvars["enable_rds_proxy"] is False
    assert template_tfvars["db_deletion_protection"] is False
    assert template_tfvars["db_skip_final_snapshot"] is True
    assert template_tfvars["vpc_id"] == "REPLACE_WITH_VPC_ID"


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


def test_rds_backup_retention_period_has_cost_policy_validation() -> None:
    variables_tf = _read("variables.tf")
    rds_module_variables_tf = _read("modules/rds/variables.tf")

    expected_description = (
        "Use 0 to disable automated backups in dev/test; valid range is 0 to 35."
    )
    expected_error = (
        "Use 0 only when disabling automated backups is approved for dev/test."
    )

    assert expected_description in variables_tf
    assert "var.db_backup_retention_period >= 0" in variables_tf
    assert "var.db_backup_retention_period <= 35" in variables_tf
    assert "db_backup_retention_period must be between 0 and 35 days" in variables_tf
    assert expected_error in variables_tf

    assert expected_description in rds_module_variables_tf
    assert "var.backup_retention_period >= 0" in rds_module_variables_tf
    assert "var.backup_retention_period <= 35" in rds_module_variables_tf
    assert "backup_retention_period must be between 0 and 35 days" in rds_module_variables_tf
    assert expected_error in rds_module_variables_tf


def test_dev_backend_and_tfvars_track_current_dev_account() -> None:
    backend_tf = _read("backend.tf")
    deploy_tfvars = _read("envs/dev/deploy.auto.tfvars.json")

    assert "420615923610" not in backend_tf
    assert "420615923610" not in deploy_tfvars
    assert "217139788460" not in backend_tf
    assert "217139788460" not in deploy_tfvars
    assert "REPLACE_WITH_ACCOUNT_ID" not in backend_tf
    assert "stockbrief-terraform-state-560271561793-ap-northeast-2" in backend_tf
    assert "vpc-0fdabc1f990027c99" not in deploy_tfvars
    assert "subnet-08f5ab10f709efd3e" not in deploy_tfvars
    assert "subnet-0940fc5ef61437e6d" not in deploy_tfvars
    assert '"vpc_id": "vpc-07b9f3920d93b65e1"' in deploy_tfvars
    assert "subnet-08d89333a3c3e2924" in deploy_tfvars
    assert "subnet-0e10680a556fa9ca8" in deploy_tfvars
    assert "rtb-01a4330966a81395a" in deploy_tfvars


def test_dev_deploy_tfvars_tracks_hosted_web_bootstrap() -> None:
    deploy_tfvars = json.loads(_read("envs/dev/deploy.auto.tfvars.json"))
    terraform_readme = _read("README.md")
    hosted_origin = "https://main.d20hgo2k8atldu.amplifyapp.com"

    assert deploy_tfvars["enable_amplify"] is True
    assert deploy_tfvars["amplify_cognito_redirect_uri"] == f"{hosted_origin}/auth/callback"
    assert deploy_tfvars["agentcore_runtime_enabled"] is False
    assert deploy_tfvars["agentcore_runtime_container_uri"] == ""
    assert hosted_origin in deploy_tfvars["cors_allowed_origins"].split(",")
    assert f"{hosted_origin}/auth/callback" in deploy_tfvars["cognito_callback_urls"]
    assert f"{hosted_origin}/account" in deploy_tfvars["cognito_logout_urls"]
    assert "current dev profile has Amplify enabled" in terraform_readme
    assert "hosted FE callback/logout URLs" in terraform_readme
    assert "Keep `agentcore_runtime_container_uri` empty" in terraform_readme


def test_dev_account_transition_requires_backend_deploy_result_on_issue_52() -> None:
    terraform_readme = _read("README.md")

    assert "After a dev backend/account transition PR merges" in terraform_readme
    assert "run `backend-dev-deploy`" in terraform_readme
    assert "record the success or expected guard failure on #52" in terraform_readme


def test_dev_scheduler_preserves_reviewed_reactivation_inputs_while_paused() -> None:
    deploy_tfvars = json.loads(_read("envs/dev/deploy.auto.tfvars.json"))
    terraform_readme = _read("README.md")
    ingestion_runbook = (REPOSITORY_ROOT / "docs/engineering/INGESTION_OPERATIONS_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    assert deploy_tfvars["enable_lambda_nat_egress"] is False
    assert deploy_tfvars["lambda_nat_public_subnet_id"] == "subnet-0c816842b11dfd2e7"
    assert deploy_tfvars["lambda_nat_public_subnet_id"] not in deploy_tfvars["lambda_nat_route_subnet_ids"]
    assert deploy_tfvars["lambda_nat_route_subnet_ids"] == [
        "subnet-08d89333a3c3e2924",
        "subnet-0e10680a556fa9ca8",
    ]
    assert deploy_tfvars["enable_ingestion_scheduler"] is False
    assert deploy_tfvars["ingestion_schedule_jobs"] == [
        {
            "provider": "OpenDART",
            "tickers": ["005930"],
            "schedule_expression": "cron(0 18 ? * MON-FRI *)",
        },
        {
            "provider": "NAVER_NEWS",
            "tickers": ["005930"],
            "schedule_expression": "cron(5 18 ? * MON-FRI *)",
        },
    ]
    assert "reviewed NAT subnet IDs in tfvars" in terraform_readme
    assert "After #214" in terraform_readme
    assert "reviewed reactivation inputs" in terraform_readme
    assert "After #214" in ingestion_runbook
    assert "`enable_ingestion_scheduler` stays `false`" in ingestion_runbook
    assert "check_ingestion_scheduler_enable_gate" in ingestion_runbook
    assert "scheduler reactivation plan evidence for #199/#200" in terraform_readme
    assert "planned 6 scheduler additions" in terraform_readme
    assert "not new changes in this scheduler reactivation plan" in terraform_readme
    assert "Classify those drift items before apply" in terraform_readme


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
    assert "jwt_authorizer_enabled  = true" in root_main_tf


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


def test_direct_bedrock_chat_provider_is_conditionally_wired() -> None:
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    api_lambda_tf = _read("modules/api_lambda/main.tf")
    api_lambda_variables_tf = _read("modules/api_lambda/variables.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")
    deploy_tfvars = json.loads(_read("envs/dev/deploy.auto.tfvars.json"))
    terraform_readme = _read("README.md")

    assert 'variable "chat_provider"' in variables_tf
    assert 'contains(["mock", "bedrock", "agentcore"], var.chat_provider)' in variables_tf
    assert 'variable "bedrock_chat_model_id"' in variables_tf
    assert 'variable "bedrock_chat_region"' in variables_tf
    assert 'chat_provider           = "mock"' in dev_tfvars
    assert 'bedrock_chat_model_id   = "apac.amazon.nova-micro-v1:0"' in dev_tfvars

    assert "CHAT_PROVIDER" in root_main_tf
    assert "BEDROCK_CHAT_MODEL_ID" in root_main_tf
    assert "BEDROCK_CHAT_REGION" in root_main_tf
    assert "bedrock_chat_foundation_model_arns" in root_main_tf
    assert "bedrock_chat_inference_profile_arn" in root_main_tf
    assert "bedrock_chat_uses_inference_profile" in root_main_tf
    assert 'startswith(var.bedrock_chat_model_id, "apac.")' in root_main_tf
    assert 'startswith(var.bedrock_chat_model_id, "global.")' in root_main_tf
    assert "bedrock_chat_base_foundation_model_id" in root_main_tf
    assert 'replace(var.bedrock_chat_model_id, "/^(apac|global)\\\\./", "")' in root_main_tf
    assert "foundation-model/${local.bedrock_chat_base_foundation_model_id}" in root_main_tf
    assert "inference-profile/${var.bedrock_chat_model_id}" in root_main_tf
    assert "bedrock_chat_inference_profile_foundation_model_regions" in root_main_tf
    assert "bedrock_chat_inference_profile_extra_foundation_model_arns" in root_main_tf
    assert "bedrock_chat_all_profile_foundation_model_arns" in root_main_tf
    assert "arn:aws:bedrock:${region}::foundation-model" in root_main_tf
    assert "concat(" in root_main_tf
    assert "data.aws_caller_identity.current.account_id" in root_main_tf
    assert "var.chat_provider == \"bedrock\"" in root_main_tf

    assert 'variable "bedrock_chat_foundation_model_arns"' in api_lambda_variables_tf
    assert 'variable "bedrock_chat_inference_profile_arn"' in api_lambda_variables_tf
    assert "bedrock:InvokeModel" in api_lambda_tf
    assert "bedrock:InferenceProfileArn" in api_lambda_tf
    assert "InvokeConfiguredInferenceProfile" in api_lambda_tf
    assert "InvokeConfiguredFoundationModels" in api_lambda_tf
    assert "length(var.bedrock_chat_foundation_model_arns) == 0" in api_lambda_tf
    assert "api-bedrock-chat-invoke" in api_lambda_tf
    assert "profile ARN" in terraform_readme
    assert "bedrock_chat_inference_profile_foundation_model_regions" in terraform_readme
    assert "bedrock_chat_inference_profile_extra_foundation_model_arns" in terraform_readme
    assert "ap-southeast-2" in terraform_readme
    assert "provider on `mock`" in terraform_readme
    assert "current dev profile has Bedrock direct chat enabled" in terraform_readme
    assert "answer_sha256_prefix=246e9a43b265" in terraform_readme
    assert "After applying a profile that switches the deployed API" in terraform_readme
    assert "existing `/v1/chat` response contract is preserved" in terraform_readme
    assert "citation guard remains active" in terraform_readme
    assert "fail-closed as `CHAT_PROVIDER_UNAVAILABLE`" in terraform_readme
    assert "The #204 post-merge deployed `/v1/chat` evidence returned HTTP 200" in terraform_readme
    assert "repeat both the redacted direct Bedrock smoke" in terraform_readme
    assert deploy_tfvars["chat_provider"] == "bedrock"
    assert deploy_tfvars["bedrock_chat_model_id"] == "apac.amazon.nova-micro-v1:0"
    assert deploy_tfvars["bedrock_chat_region"] == ""
    assert deploy_tfvars["bedrock_chat_inference_profile_foundation_model_regions"] == [
        "ap-southeast-2",
        "ap-northeast-1",
        "ap-south-1",
        "ap-northeast-2",
        "ap-southeast-1",
        "ap-northeast-3",
    ]
    assert deploy_tfvars["bedrock_chat_inference_profile_extra_foundation_model_arns"] == []


def test_ingestion_pipeline_resources_are_wired_with_scheduler_disabled_by_default() -> None:
    ingestion_tf = _read("ingestion.tf")
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    outputs_tf = _read("outputs.tf")
    api_lambda_tf = _read("modules/api_lambda/main.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")
    deploy_tfvars = json.loads(_read("envs/dev/deploy.auto.tfvars.json"))

    assert 'variable "enable_ingestion_scheduler"' in variables_tf
    assert 'variable "ingestion_schedule_jobs"' in variables_tf
    assert "default     = false" in variables_tf
    assert "aws_s3_bucket\" \"ingestion_raw" in ingestion_tf
    assert "aws_kms_key\" \"ingestion_raw" in ingestion_tf
    assert "enable_key_rotation     = true" in ingestion_tf
    assert 'sse_algorithm     = "aws:kms"' in ingestion_tf
    assert "aws_sqs_queue\" \"ingestion_dlq" in ingestion_tf
    assert "sqs_managed_sse_enabled   = true" in ingestion_tf
    assert "aws_scheduler_schedule\" \"provider_ingestion" in ingestion_tf
    assert "local.ingestion_scheduler_enabled" in ingestion_tf
    assert "local.ingestion_schedule_jobs_by_key" in ingestion_tf
    assert "for_each = local.ingestion_schedule_jobs_by_key" in ingestion_tf
    assert "stockbrief_operation = \"ingest_provider_batch\"" in ingestion_tf
    assert "raise_on_failure     = true" in ingestion_tf
    assert "INGESTION_RAW_BUCKET" in root_main_tf
    assert "INGESTION_RAW_BUCKET" in api_lambda_tf
    assert "s3:PutObject" in api_lambda_tf
    assert "kms:GenerateDataKey" in api_lambda_tf
    assert 'variable "vpc_endpoint_route_table_ids"' in variables_tf
    assert 'resource "aws_vpc_endpoint" "s3"' in root_main_tf
    assert 'service_name      = "com.amazonaws.${var.aws_region}.s3"' in root_main_tf
    assert 'vpc_endpoint_type = "Gateway"' in root_main_tf
    assert "route_table_ids   = local.s3_gateway_endpoint_route_table_ids" in root_main_tf
    assert 'output "ingestion_raw_bucket_name"' in outputs_tf
    assert 'output "ingestion_raw_kms_key_arn"' in outputs_tf
    assert 'output "ingestion_dlq_url"' in outputs_tf
    assert 'output "ingestion_scheduler_names"' in outputs_tf
    assert "enable_ingestion_scheduler          = false" in dev_tfvars
    assert deploy_tfvars["enable_ingestion_scheduler"] is False
    assert [job["provider"] for job in deploy_tfvars["ingestion_schedule_jobs"]] == [
        "OpenDART",
        "NAVER_NEWS",
    ]
    assert all(job["tickers"] == ["005930"] for job in deploy_tfvars["ingestion_schedule_jobs"])


def test_lambda_nat_egress_is_toggleable_and_disabled_by_default() -> None:
    egress_tf = _read("egress.tf")
    root_main_tf = _read("main.tf")
    variables_tf = _read("variables.tf")
    dev_tfvars = _read("envs/dev/terraform.tfvars.example")
    terraform_readme = _read("README.md")
    deployment_doc = (REPOSITORY_ROOT / "docs/engineering/DEPLOYMENT_BOOTSTRAP.md").read_text(
        encoding="utf-8"
    )

    assert 'variable "enable_lambda_nat_egress"' in variables_tf
    assert 'variable "lambda_nat_public_subnet_id"' in variables_tf
    assert 'variable "lambda_nat_route_subnet_ids"' in variables_tf
    assert "default     = false" in variables_tf
    assert "aws_nat_gateway\" \"lambda_egress" in egress_tf
    assert "aws_eip\" \"lambda_nat" in egress_tf
    assert "aws_route_table\" \"lambda_nat_egress" in egress_tf
    assert "aws_route_table_association\" \"lambda_nat_egress" in egress_tf
    assert "local.lambda_nat_egress_inputs_valid" in egress_tf
    assert "local.lambda_nat_egress_enabled" in egress_tf
    assert "s3_gateway_endpoint_route_table_ids" in root_main_tf
    assert "aws_route_table.lambda_nat_egress[0].id" in root_main_tf
    assert "local.s3_gateway_endpoint_route_table_ids" in root_main_tf
    assert "!contains(var.lambda_nat_route_subnet_ids, var.lambda_nat_public_subnet_id)" in egress_tf
    assert "not included in lambda_nat_route_subnet_ids" in egress_tf
    assert "precondition" in egress_tf
    assert "enable_lambda_nat_egress     = false" in dev_tfvars
    assert "NAT Gateway hourly and data processing costs" in terraform_readme
    assert "Do not include" in terraform_readme
    assert "`lambda_nat_public_subnet_id` in `lambda_nat_route_subnet_ids`" in terraform_readme
    assert "turn it off after the evidence is collected" in terraform_readme
    assert "managed NAT route table" in terraform_readme
    assert "remove the NAT Gateway and EIP" in deployment_doc


def test_dev_live_provider_nat_egress_uses_non_overlapping_subnets() -> None:
    deploy_tfvars = json.loads(_read("envs/dev/deploy.auto.tfvars.json"))
    terraform_readme = _read("README.md")
    runbook = (REPOSITORY_ROOT / "docs/engineering/INGESTION_OPERATIONS_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    nat_enabled = deploy_tfvars.get("enable_lambda_nat_egress", False)
    nat_public_subnet_id = deploy_tfvars.get("lambda_nat_public_subnet_id", "")
    nat_route_subnet_ids = deploy_tfvars.get("lambda_nat_route_subnet_ids", [])

    if nat_enabled:
        assert nat_public_subnet_id
        assert nat_route_subnet_ids
        assert nat_public_subnet_id not in nat_route_subnet_ids
    else:
        assert nat_public_subnet_id
        assert nat_route_subnet_ids
        assert nat_public_subnet_id not in nat_route_subnet_ids

    assert "enable_lambda_nat_egress" in terraform_readme
    assert "lambda_nat_public_subnet_id" in terraform_readme
    assert "lambda_nat_route_subnet_ids" in terraform_readme
    assert "The NAT public subnet must" in runbook
    assert "raw archive writes continue through" in runbook


def test_secret_versions_do_not_reclaim_manually_rotated_current_values() -> None:
    secrets_tf = _read("modules/secrets/main.tf")

    assert secrets_tf.count("ignore_changes = all") == 2


def test_managed_security_group_egress_is_port_scoped() -> None:
    root_main_tf = _read("main.tf")

    assert 'resource "aws_security_group_rule" "lambda_https_egress"' in root_main_tf
    assert 'resource "aws_security_group_rule" "lambda_database_egress"' in root_main_tf
    assert 'resource "aws_security_group_rule" "rds_proxy_to_rds"' in root_main_tf
    assert 'resource "aws_security_group_rule" "rds_proxy_from_lambda"' in root_main_tf
    assert 'resource "aws_security_group_rule" "rds_from_managed_database_client"' in root_main_tf
    assert 'description       = "HTTPS outbound for Cognito, external APIs, and AWS public endpoints"' in root_main_tf
    assert 'description              = "PostgreSQL to managed database endpoint"' in root_main_tf
    assert 'description              = "PostgreSQL to RDS"' in root_main_tf
    assert 'protocol          = "-1"' not in root_main_tf
    assert 'protocol                 = "-1"' not in root_main_tf
    assert 'cidr_blocks       = ["0.0.0.0/0"]' in root_main_tf
    assert "from_port         = 443" in root_main_tf
    assert "to_port           = 443" in root_main_tf
    assert "from_port                = 5432" in root_main_tf
    assert "to_port                  = 5432" in root_main_tf
    assert "source_security_group_id = aws_security_group.rds[0].id" in root_main_tf


def test_security_group_rule_transition_apply_is_documented() -> None:
    terraform_readme = _read("README.md")

    assert "## Security Group Rule Apply Review" in terraform_readme
    assert "older inline-rule state" in terraform_readme
    assert "rule deletes and creates in the same apply" in terraform_readme
    assert "aws_security_group.lambda[0] will be updated in-place" in terraform_readme
    assert "aws_security_group_rule.lambda_https_egress[0] will be created" in terraform_readme
    assert "aws_security_group_rule.lambda_database_egress" in terraform_readme
    assert "aws_security_group_rule.rds_proxy_from_lambda" in terraform_readme
    assert "aws_security_group_rule.rds_proxy_to_rds" in terraform_readme
    assert "aws_security_group_rule.rds_from_managed_database_client" in terraform_readme
    assert "aws_security_group_rule.secretsmanager_endpoint_from_lambda" in terraform_readme
    assert "terraform plan -var-file=envs/dev/deploy.auto.tfvars.json" in terraform_readme
    assert "resources should remain\nin-place" in terraform_readme
    assert "Do not apply if Terraform plans to replace a Security Group" in terraform_readme
    assert "defined maintenance window" in terraform_readme
    assert "Secrets Manager connectivity loss is acceptable" in terraform_readme
    assert "enabling scheduled ingestion" in terraform_readme


def test_rds_proxy_operational_alarms_are_defined_and_documented() -> None:
    alarms_tf = _read("alarms.tf")
    rds_proxy_outputs_tf = _read("modules/rds_proxy/outputs.tf")
    terraform_readme = _read("README.md")

    assert 'output "proxy_name"' in rds_proxy_outputs_tf
    assert 'resource "aws_cloudwatch_metric_alarm" "rds_proxy_borrow_latency_high"' in alarms_tf
    assert (
        'resource "aws_cloudwatch_metric_alarm" "rds_proxy_database_connection_failures"'
        in alarms_tf
    )
    assert 'resource "aws_cloudwatch_metric_alarm" "rds_proxy_client_auth_failures"' in alarms_tf
    assert alarms_tf.count("ProxyName = module.rds_proxy.proxy_name") == 3
    assert "DatabaseConnectionsBorrowLatency" in alarms_tf
    assert "DatabaseConnectionsSetupFailed" in alarms_tf
    assert "ClientConnectionsSetupFailedAuth" in alarms_tf
    assert "threshold           = 1000000" in alarms_tf
    assert "RDS Proxy | Database connection borrow latency > 1 second" in terraform_readme
    assert "Some RDS Proxy metrics are not visible until after the" in terraform_readme
    assert "Confirm every SNS email subscription is in `Confirmed` status" in terraform_readme
    assert "Prefer a team or operations group alias" in terraform_readme
    assert "Terraform plan and state metadata" in terraform_readme
