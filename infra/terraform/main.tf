module "secrets" {
  source = "./modules/secrets"

  name_prefix = local.name_prefix
  project     = var.project
  environment = var.environment
}

module "cloudwatch" {
  source = "./modules/cloudwatch"

  name_prefix    = local.name_prefix
  project        = var.project
  environment    = var.environment
  enable_rds     = length(var.db_subnet_ids) > 0
  enable_amplify = var.enable_amplify
}

locals {
  managed_networking_enabled = var.vpc_id != "" && length(var.db_subnet_ids) > 0 && length(var.lambda_subnet_ids) > 0

  effective_lambda_security_group_ids = length(var.lambda_security_group_ids) > 0 ? var.lambda_security_group_ids : (
    local.managed_networking_enabled ? [aws_security_group.lambda[0].id] : []
  )

  effective_rds_security_group_ids = length(var.db_security_group_ids) > 0 ? var.db_security_group_ids : (
    local.managed_networking_enabled ? [aws_security_group.rds[0].id] : []
  )

  effective_rds_proxy_security_group_ids = length(var.rds_proxy_security_group_ids) > 0 ? var.rds_proxy_security_group_ids : (
    local.managed_networking_enabled ? [aws_security_group.rds_proxy[0].id] : local.effective_rds_security_group_ids
  )
}

resource "aws_security_group" "lambda" {
  count = local.managed_networking_enabled ? 1 : 0

  name        = "${local.name_prefix}-lambda-sg"
  description = "Lambda egress for StockBrief API"
  vpc_id      = var.vpc_id

  egress {
    description = "Allow outbound HTTPS and database connections"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds_proxy" {
  count = local.managed_networking_enabled ? 1 : 0

  name        = "${local.name_prefix}-rds-proxy-sg"
  description = "RDS Proxy access from StockBrief API Lambda"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from Lambda"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda[0].id]
  }

  egress {
    description = "Allow outbound connections to RDS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  count = local.managed_networking_enabled ? 1 : 0

  name        = "${local.name_prefix}-rds-sg"
  description = "RDS PostgreSQL access from StockBrief RDS Proxy"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from RDS Proxy"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.rds_proxy[0].id]
  }
}

resource "aws_security_group" "secretsmanager_endpoint" {
  count = local.managed_networking_enabled ? 1 : 0

  name        = "${local.name_prefix}-secretsmanager-vpce-sg"
  description = "Secrets Manager interface endpoint access from StockBrief API Lambda"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTPS from Lambda"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda[0].id]
  }
}

resource "aws_vpc_endpoint" "secretsmanager" {
  count = local.managed_networking_enabled ? 1 : 0

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.lambda_subnet_ids
  security_group_ids  = [aws_security_group.secretsmanager_endpoint[0].id]
  private_dns_enabled = true
}

module "cognito" {
  source = "./modules/cognito"

  name_prefix             = local.name_prefix
  callback_urls           = var.cognito_callback_urls
  logout_urls             = var.cognito_logout_urls
  hosted_ui_domain_prefix = var.cognito_hosted_ui_domain_prefix
}

module "rds" {
  source = "./modules/rds"

  name_prefix             = local.name_prefix
  db_name                 = var.db_name
  db_instance_class       = var.db_instance_class
  allocated_storage_gb    = var.db_allocated_storage_gb
  subnet_ids              = var.db_subnet_ids
  security_group_ids      = local.effective_rds_security_group_ids
  secret_arn              = module.secrets.database_secret_arn
  log_group_name          = module.cloudwatch.rds_log_group_name
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = var.db_skip_final_snapshot
  backup_retention_period = var.db_backup_retention_period
}

module "rds_proxy" {
  source = "./modules/rds_proxy"

  name_prefix            = local.name_prefix
  db_instance_identifier = module.rds.db_instance_identifier
  subnet_ids             = var.db_subnet_ids
  security_group_ids     = local.effective_rds_proxy_security_group_ids
  secret_arn             = module.rds.db_secret_arn
}

module "agentcore_runtime" {
  source = "./modules/agentcore_runtime"

  name_prefix        = local.name_prefix
  enabled            = var.agentcore_runtime_enabled
  container_uri      = var.agentcore_runtime_container_uri
  network_mode       = var.agentcore_network_mode
  subnet_ids         = var.lambda_subnet_ids
  security_group_ids = var.lambda_security_group_ids
  environment_variables = {
    APP_ENV      = var.environment
    SERVICE_NAME = "stockbrief-agent"
  }
}

module "api_lambda" {
  source = "./modules/api_lambda"

  name_prefix               = local.name_prefix
  package_path              = var.api_lambda_package_path
  runtime                   = var.api_lambda_runtime
  timeout_seconds           = var.api_lambda_timeout_seconds
  memory_mb                 = var.api_lambda_memory_mb
  lambda_subnet_ids         = var.lambda_subnet_ids
  lambda_security_group_ids = local.effective_lambda_security_group_ids
  database_secret_arn       = module.secrets.database_secret_arn
  external_api_secret_arn   = module.secrets.external_api_secret_arn
  log_group_name            = module.cloudwatch.api_lambda_log_group_name
  api_gateway_log_group_arn = module.cloudwatch.api_gateway_log_group_arn
  database_host             = module.rds_proxy.proxy_endpoint
  database_port             = 5432
  database_name             = var.db_name
  agentcore_runtime_arn     = module.agentcore_runtime.runtime_arn
  jwt_authorizer_issuer     = module.cognito.issuer
  jwt_authorizer_audience   = [module.cognito.app_client_id]
  environment_variables = {
    APP_ENV               = var.environment
    LOG_LEVEL             = "info"
    SERVICE_NAME          = "stockbrief-api"
    SERVICE_VERSION       = "0.1.0"
    API_BASE_PATH         = "/v1"
    CORS_ALLOWED_ORIGINS  = var.cors_allowed_origins
    COGNITO_USER_POOL_ID  = module.cognito.user_pool_id
    COGNITO_APP_CLIENT_ID = module.cognito.app_client_id
    COGNITO_ISSUER        = module.cognito.issuer
    COGNITO_JWKS_URL      = "${module.cognito.issuer}/.well-known/jwks.json"
  }
}

module "amplify" {
  count  = var.enable_amplify ? 1 : 0
  source = "./modules/amplify"

  name_prefix              = local.name_prefix
  repository_url           = var.amplify_repository_url
  access_token             = var.amplify_access_token
  branch_name              = var.amplify_branch_name
  next_public_api_base     = module.api_lambda.api_base_url
  cognito_region           = var.aws_region
  cognito_user_pool_id     = module.cognito.user_pool_id
  cognito_app_client_id    = module.cognito.app_client_id
  cognito_hosted_ui_domain = module.cognito.hosted_ui_domain == "" ? "" : "${module.cognito.hosted_ui_domain}.auth.${var.aws_region}.amazoncognito.com"
  cognito_redirect_uri     = var.cognito_callback_urls[0]
}
