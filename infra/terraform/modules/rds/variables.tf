variable "name_prefix" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_instance_class" {
  type = string
}

variable "allocated_storage_gb" {
  type = number
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "secret_arn" {
  type      = string
  sensitive = true
}

variable "log_group_name" {
  type = string
}

variable "deletion_protection" {
  description = "Protect the RDS instance from accidental deletion. Set false for dev to allow teardown."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on deletion. Set true for dev to avoid leftover snapshots."
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "Number of days to retain automated backups. 0 disables backups."
  type        = number
  default     = 7
}
