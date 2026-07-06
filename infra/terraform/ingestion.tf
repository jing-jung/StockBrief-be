data "aws_caller_identity" "current" {}

locals {
  ingestion_raw_bucket_name = "${local.name_prefix}-raw-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  legacy_ingestion_schedule_jobs = length(var.ingestion_schedule_tickers) > 0 ? [
    {
      schedule_key        = lower(replace(var.ingestion_schedule_provider, "_", "-"))
      provider            = var.ingestion_schedule_provider
      tickers             = var.ingestion_schedule_tickers
      schedule_expression = var.ingestion_schedule_expression
    }
  ] : []
  configured_ingestion_schedule_jobs = length(var.ingestion_schedule_jobs) > 0 ? [
    for job in var.ingestion_schedule_jobs : {
      schedule_key        = lower(replace(job.provider, "_", "-"))
      provider            = job.provider
      tickers             = job.tickers
      schedule_expression = coalesce(job.schedule_expression, var.ingestion_schedule_expression)
    }
  ] : local.legacy_ingestion_schedule_jobs
  ingestion_schedule_jobs_by_key = var.enable_ingestion_scheduler ? {
    for job in local.configured_ingestion_schedule_jobs : job.schedule_key => job
  } : {}
  ingestion_scheduler_enabled = length(local.ingestion_schedule_jobs_by_key) > 0
}

resource "aws_s3_bucket" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  bucket = local.ingestion_raw_bucket_name
}

resource "aws_kms_key" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  description             = "KMS key for StockBrief provider ingestion raw archives"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  name          = "alias/${local.name_prefix}-ingestion-raw"
  target_key_id = aws_kms_key.ingestion_raw[0].key_id
}

resource "aws_s3_bucket_public_access_block" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  bucket                  = aws_s3_bucket.ingestion_raw[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  bucket = aws_s3_bucket.ingestion_raw[0].id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.ingestion_raw[0].arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "ingestion_raw" {
  count = var.enable_ingestion_raw_archive ? 1 : 0

  bucket = aws_s3_bucket.ingestion_raw[0].id

  rule {
    id     = "expire-raw-provider-payloads"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = var.ingestion_raw_retention_days
    }
  }
}

resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${local.name_prefix}-ingestion-dlq"
  message_retention_seconds = var.ingestion_dlq_message_retention_seconds
  sqs_managed_sse_enabled   = true
}

data "aws_iam_policy_document" "ingestion_scheduler_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingestion_scheduler" {
  count = local.ingestion_scheduler_enabled ? 1 : 0

  name               = "${local.name_prefix}-ingestion-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.ingestion_scheduler_assume_role.json
}

data "aws_iam_policy_document" "ingestion_scheduler_invoke" {
  count = local.ingestion_scheduler_enabled ? 1 : 0

  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.api_lambda.lambda_function_arn]
  }

  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingestion_dlq.arn]
  }
}

resource "aws_iam_role_policy" "ingestion_scheduler_invoke" {
  count = local.ingestion_scheduler_enabled ? 1 : 0

  name   = "${local.name_prefix}-ingestion-scheduler-invoke"
  role   = aws_iam_role.ingestion_scheduler[0].id
  policy = data.aws_iam_policy_document.ingestion_scheduler_invoke[0].json
}

resource "aws_scheduler_schedule" "provider_ingestion" {
  for_each = local.ingestion_schedule_jobs_by_key

  name                         = "${local.name_prefix}-provider-ingestion-${each.key}"
  schedule_expression          = each.value.schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.api_lambda.lambda_function_arn
    role_arn = aws_iam_role.ingestion_scheduler[0].arn

    dead_letter_config {
      arn = aws_sqs_queue.ingestion_dlq.arn
    }

    input = jsonencode({
      stockbrief_operation = "refresh_score_snapshots"
      provider             = each.value.provider
      tickers              = each.value.tickers
      raise_on_failure     = true
    })
  }
}

resource "aws_lambda_permission" "ingestion_scheduler" {
  for_each = local.ingestion_schedule_jobs_by_key

  statement_id  = "AllowExecutionFromIngestionScheduler-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = module.api_lambda.lambda_function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.provider_ingestion[each.key].arn
}
