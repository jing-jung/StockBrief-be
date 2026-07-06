variable "project" {
  description = "Project name used for resource naming."
  type        = string
  default     = "stockbrief"
}

variable "environment" {
  description = "Deployment environment. Use dev, dev-<member>, staging, or prod."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment) || can(regex("^dev-[a-z0-9][a-z0-9-]*$", var.environment))
    error_message = "environment must be dev, dev-<member>, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region for StockBrief MVP resources."
  type        = string
  default     = "ap-northeast-2"
}

variable "api_lambda_package_path" {
  description = "Path to the packaged FastAPI/Mangum Lambda zip."
  type        = string
  default     = "../../dist/stockbrief-api-lambda.zip"
}

variable "api_lambda_runtime" {
  description = "Python runtime for Lambda."
  type        = string
  default     = "python3.13"
}

variable "api_lambda_timeout_seconds" {
  description = "Lambda timeout for API handler."
  type        = number
  default     = 30
}

variable "api_lambda_memory_mb" {
  description = "Lambda memory size for API handler."
  type        = number
  default     = 512
}

variable "cors_allowed_origins" {
  description = "Comma-separated CORS origins passed to the backend."
  type        = string
  default     = "http://localhost:3000,http://127.0.0.1:3000"
}

variable "chat_provider" {
  description = "Chat explanation provider. Keep mock unless Bedrock or AgentCore invocation is explicitly approved."
  type        = string
  default     = "mock"

  validation {
    condition     = contains(["mock", "bedrock", "agentcore"], var.chat_provider)
    error_message = "chat_provider must be mock, bedrock, or agentcore."
  }
}

variable "bedrock_chat_model_id" {
  description = "Foundation model ID or inference profile ID used by the direct Bedrock chat provider when chat_provider is bedrock."
  type        = string
  default     = "apac.amazon.nova-micro-v1:0"
}

variable "bedrock_chat_region" {
  description = "Optional Bedrock Runtime region override. Leave empty to use aws_region."
  type        = string
  default     = ""
}

variable "bedrock_chat_max_tokens" {
  description = "Maximum model output tokens for chat providers."
  type        = number
  default     = 700
}

variable "bedrock_chat_temperature" {
  description = "Model temperature for chat providers."
  type        = number
  default     = 0.2
}

variable "bedrock_chat_timeout_seconds" {
  description = "Bedrock Runtime timeout for chat providers."
  type        = number
  default     = 8
}

variable "bedrock_chat_inference_profile_foundation_model_regions" {
  description = "Foundation model Regions associated with the configured Bedrock inference profile. Keep this in sync with the AWS Bedrock profile routing list."
  type        = list(string)
  default = [
    "ap-southeast-2",
    "ap-northeast-1",
    "ap-south-1",
    "ap-northeast-2",
    "ap-southeast-1",
    "ap-northeast-3",
  ]
}

variable "bedrock_chat_inference_profile_extra_foundation_model_arns" {
  description = "Additional foundation model ARNs required by the configured Bedrock inference profile, such as global profile ARN patterns that cannot be represented by a Region list."
  type        = list(string)
  default     = []
}

variable "amplify_repository_url" {
  description = "Repository URL for Amplify app connection. Placeholder for MVP."
  type        = string
  default     = "https://github.com/example/stockbrief"
}

variable "enable_amplify" {
  description = "Whether to create the Amplify Hosting app. Keep false until the target GitHub organization approves the Amplify GitHub App."
  type        = bool
  default     = false
}

variable "amplify_access_token" {
  description = "GitHub personal access token used by Amplify to connect the repository. Prefer TF_VAR_amplify_access_token and never commit a real value."
  type        = string
  default     = ""
  sensitive   = true
}

variable "amplify_branch_name" {
  description = "Amplify branch to deploy."
  type        = string
  default     = "main"
}

variable "amplify_cognito_redirect_uri" {
  description = "Optional Cognito redirect URI exposed to the Amplify frontend. Leave empty to use the first cognito_callback_urls entry."
  type        = string
  default     = ""
}

variable "cognito_callback_urls" {
  description = "Allowed Cognito Hosted UI callback URLs. The first value is passed to Amplify as NEXT_PUBLIC_COGNITO_REDIRECT_URI."
  type        = list(string)
  default     = ["http://localhost:3000/auth/callback"]
}

variable "cognito_logout_urls" {
  description = "Allowed Cognito Hosted UI logout URLs."
  type        = list(string)
  default     = ["http://localhost:3000/account"]
}

variable "cognito_hosted_ui_domain_prefix" {
  description = "Optional globally unique Cognito Hosted UI domain prefix. Leave empty to skip domain creation in early skeleton planning."
  type        = string
  default     = ""
}

variable "db_name" {
  description = "RDS database name."
  type        = string
  default     = "stockbrief"
}

variable "db_instance_class" {
  description = "RDS instance class for MVP dev."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "RDS allocated storage in GB."
  type        = number
  default     = 20
}

variable "db_deletion_protection" {
  description = "Protect the RDS instance from accidental deletion. Set false for dev."
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on deletion. Set true for dev to avoid leftover snapshots."
  type        = bool
  default     = false
}

variable "db_backup_retention_period" {
  description = "Days to retain automated RDS backups. Use 0 to disable automated backups in dev/test; valid range is 0 to 35."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_period >= 0 && var.db_backup_retention_period <= 35
    error_message = "db_backup_retention_period must be between 0 and 35 days. Use 0 only when disabling automated backups is approved for dev/test."
  }
}

variable "vpc_id" {
  description = "VPC ID used for managed dev security groups and interface endpoints. Leave empty to provide security groups manually."
  type        = string
  default     = ""
}

variable "db_subnet_ids" {
  description = "Private subnet IDs for RDS. Placeholder until VPC module is added."
  type        = list(string)
  default     = []
}

variable "db_security_group_ids" {
  description = "Security group IDs attached to RDS. If empty and vpc_id is set, Terraform creates a dev RDS security group."
  type        = list(string)
  default     = []
}

variable "rds_proxy_security_group_ids" {
  description = "Security group IDs attached to RDS Proxy. If empty and vpc_id is set, Terraform creates a dev proxy security group."
  type        = list(string)
  default     = []
}

variable "enable_rds_proxy" {
  description = "Whether to create RDS Proxy. Disable for the first low-cost dev bootstrap; enable when Lambda concurrency requires connection pooling."
  type        = bool
  default     = false
}

variable "lambda_subnet_ids" {
  description = "Private subnet IDs for Lambda. MUST have a route to a NAT Gateway to allow external API and Cognito calls."
  type        = list(string)
  default     = []
}

variable "lambda_security_group_ids" {
  description = "Security group IDs for Lambda. If empty and vpc_id is set, Terraform creates a dev Lambda security group."
  type        = list(string)
  default     = []
}

variable "vpc_endpoint_route_table_ids" {
  description = "Route table IDs that should receive Gateway VPC endpoints for private AWS service access from Lambda subnets."
  type        = list(string)
  default     = []
}

variable "enable_lambda_nat_egress" {
  description = "Whether to create a NAT Gateway and private route table associations for Lambda outbound internet egress. Keep false until live provider ingestion is approved because NAT Gateway has hourly and data processing costs."
  type        = bool
  default     = false
}

variable "lambda_nat_create_public_subnet" {
  description = "Whether Terraform should create the public subnet and public route table used by the Lambda NAT Gateway."
  type        = bool
  default     = false
}

variable "lambda_nat_public_subnet_id" {
  description = "Existing public subnet ID where the NAT Gateway for Lambda provider egress is created. Leave empty when lambda_nat_create_public_subnet is true."
  type        = string
  default     = ""
}

variable "lambda_nat_public_subnet_cidr_block" {
  description = "CIDR block for the Terraform-managed public NAT subnet. Required when lambda_nat_create_public_subnet is true."
  type        = string
  default     = ""

  validation {
    condition     = var.lambda_nat_public_subnet_cidr_block == "" || can(cidrhost(var.lambda_nat_public_subnet_cidr_block, 0))
    error_message = "lambda_nat_public_subnet_cidr_block must be empty or a valid IPv4 CIDR block."
  }
}

variable "lambda_nat_public_subnet_availability_zone" {
  description = "Optional Availability Zone for the Terraform-managed public NAT subnet."
  type        = string
  default     = ""
}

variable "lambda_nat_internet_gateway_id" {
  description = "Existing Internet Gateway ID for the Terraform-managed public NAT subnet route. Leave empty to discover the VPC Internet Gateway or when lambda_nat_create_internet_gateway is true."
  type        = string
  default     = ""
}

variable "lambda_nat_create_internet_gateway" {
  description = "Whether Terraform should create and attach an Internet Gateway for the managed public NAT subnet. Keep false when the VPC already has an Internet Gateway."
  type        = bool
  default     = false
}

variable "lambda_nat_route_subnet_ids" {
  description = "Subnet IDs that should use the Terraform-managed NAT route table for outbound internet egress. Usually the Lambda subnet IDs."
  type        = list(string)
  default     = []
}

variable "enable_operational_alarms" {
  description = "Whether to create baseline CloudWatch operational alarms for Lambda, API Gateway, and RDS."
  type        = bool
  default     = true
}

variable "operational_alarm_email_addresses" {
  description = "Email addresses subscribed to operational alarm SNS notifications. Leave empty to create alarms without notification actions."
  type        = list(string)
  default     = []
}

variable "agentcore_runtime_enabled" {
  description = "Whether to create the AgentCore Runtime CloudFormation stack. Requires agentcore_runtime_container_uri."
  type        = bool
  default     = false
}

variable "agentcore_runtime_container_uri" {
  description = "ECR image URI for the StockBrief AgentCore Runtime container."
  type        = string
  default     = ""
}

variable "agentcore_runtime_external_arn" {
  description = "Existing AgentCore Runtime ARN managed by direct deploy outside Terraform."
  type        = string
  default     = ""
}

variable "agentcore_runtime_external_id" {
  description = "Existing AgentCore Runtime ID managed by direct deploy outside Terraform."
  type        = string
  default     = ""
}

variable "agentcore_runtime_endpoint_name" {
  description = "AgentCore Runtime endpoint name managed by direct deploy outside Terraform."
  type        = string
  default     = ""
}

variable "agentcore_runtime_manage_with_cloudformation" {
  description = "Legacy fallback to create AgentCore Runtime resources through CloudFormation. Keep false for the reviewed direct deploy path."
  type        = bool
  default     = false
}

variable "agentcore_network_mode" {
  description = "AgentCore Runtime network mode. Use PUBLIC for the first PoC, VPC when private data access is required."
  type        = string
  default     = "PUBLIC"

  validation {
    condition     = contains(["PUBLIC", "VPC"], var.agentcore_network_mode)
    error_message = "agentcore_network_mode must be PUBLIC or VPC."
  }
}

variable "agentcore_runtime_timeout_seconds" {
  description = "Backend timeout for AgentCore Runtime invocation."
  type        = number
  default     = 8
}

variable "agentcore_runtime_max_turns" {
  description = "Maximum Strands Agent turns per Runtime invocation."
  type        = number
  default     = 4
}

variable "agentcore_runtime_qualifier" {
  description = "AgentCore Runtime qualifier used by the backend invoke path."
  type        = string
  default     = "DEFAULT"
}

variable "agentcore_runtime_log_retention_days" {
  description = "CloudWatch log retention for the dev AgentCore Runtime PoC."
  type        = number
  default     = 14
}

variable "enable_ingestion_raw_archive" {
  description = "Whether to create the S3 raw payload archive used by provider ingestion jobs."
  type        = bool
  default     = true
}

variable "ingestion_raw_retention_days" {
  description = "Number of days to retain raw provider payloads in the dev archive bucket."
  type        = number
  default     = 30
}

variable "enable_ingestion_scheduler" {
  description = "Whether to create EventBridge Scheduler rules for provider ingestion plus score refresh. Keep false until provider credentials and target tickers are approved."
  type        = bool
  default     = false
}

variable "ingestion_schedule_expression" {
  description = "EventBridge Scheduler expression for provider ingestion plus score refresh."
  type        = string
  default     = "cron(0 18 ? * MON-FRI *)"
}

variable "ingestion_schedule_provider" {
  description = "Provider passed to the scheduled refresh_score_snapshots operation."
  type        = string
  default     = "OpenDART"

  validation {
    condition     = contains(["OpenDART", "NAVER_NEWS", "KRX"], var.ingestion_schedule_provider)
    error_message = "ingestion_schedule_provider must be OpenDART, NAVER_NEWS, or KRX."
  }
}

variable "ingestion_schedule_tickers" {
  description = "Tickers passed to the scheduled refresh_score_snapshots operation."
  type        = list(string)
  default     = []
}

variable "ingestion_schedule_jobs" {
  description = "Reviewed provider ingestion and score refresh jobs to schedule. When empty, the legacy ingestion_schedule_provider/tickers variables are used."
  type = list(object({
    provider            = string
    tickers             = list(string)
    schedule_expression = optional(string)
  }))
  default = []

  validation {
    condition = alltrue([
      for job in var.ingestion_schedule_jobs : contains(["OpenDART", "NAVER_NEWS", "KRX"], job.provider)
    ])
    error_message = "Each ingestion_schedule_jobs provider must be OpenDART, NAVER_NEWS, or KRX."
  }

  validation {
    condition = alltrue([
      for job in var.ingestion_schedule_jobs : length(job.tickers) > 0
    ])
    error_message = "Each ingestion_schedule_jobs entry must include at least one ticker."
  }
}

variable "ingestion_dlq_message_retention_seconds" {
  description = "Retention period for failed ingestion scheduler invocation messages."
  type        = number
  default     = 1209600
}
