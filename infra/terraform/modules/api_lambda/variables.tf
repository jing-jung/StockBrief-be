variable "name_prefix" {
  type = string
}

variable "package_path" {
  type = string
}

variable "runtime" {
  type = string
}

variable "timeout_seconds" {
  type = number
}

variable "memory_mb" {
  type = number
}

variable "lambda_subnet_ids" {
  type = list(string)
}

variable "lambda_security_group_ids" {
  type = list(string)
}

variable "database_secret_arn" {
  type      = string
  sensitive = true
}

variable "external_api_secret_arn" {
  type      = string
  sensitive = true
}

variable "log_group_name" {
  type = string
}

variable "api_gateway_log_group_arn" {
  type = string
}

variable "database_host" {
  type    = string
  default = ""
}

variable "database_port" {
  type    = number
  default = 5432
}

variable "database_name" {
  type    = string
  default = "stockbrief"
}

variable "ingestion_raw_bucket_name" {
  type    = string
  default = ""
}

variable "ingestion_raw_bucket_arn" {
  type    = string
  default = ""
}

variable "ingestion_raw_kms_key_arn" {
  type    = string
  default = ""
}

variable "agentcore_runtime_arn" {
  type    = string
  default = ""
}

variable "environment_variables" {
  type = map(string)
}

variable "jwt_authorizer_issuer" {
  type    = string
  default = ""
}

variable "jwt_authorizer_enabled" {
  description = "Whether to create the API Gateway JWT authorizer for protected routes."
  type        = bool
  default     = false
}

variable "jwt_authorizer_audience" {
  type    = list(string)
  default = []
}

variable "protected_route_keys" {
  type = list(string)
  default = [
    "GET /v1/me",
    "PATCH /v1/me",
    "GET /v1/me/preferences",
    "PUT /v1/me/preferences",
    "GET /v1/me/watchlist",
    "POST /v1/me/watchlist",
    "PATCH /v1/me/watchlist/{ticker}",
    "DELETE /v1/me/watchlist/{ticker}",
    "POST /v1/me/watchlist/import",
    "GET /v1/me/chat-sessions",
    "POST /v1/me/chat-sessions",
  ]
}
