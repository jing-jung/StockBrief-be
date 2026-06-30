variable "name_prefix" {
  type = string
}

variable "enabled" {
  type    = bool
  default = false
}

variable "container_uri" {
  type    = string
  default = ""
}

variable "network_mode" {
  type    = string
  default = "PUBLIC"
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "request_header_allowlist" {
  type    = list(string)
  default = ["x-correlation-id", "x-user-id"]
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "bedrock_chat_foundation_model_arns" {
  type    = list(string)
  default = []
}

variable "bedrock_chat_inference_profile_arn" {
  type    = string
  default = ""
}
