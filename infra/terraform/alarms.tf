locals {
  operational_alarm_actions = var.enable_operational_alarms && length(var.operational_alarm_email_addresses) > 0 ? [
    aws_sns_topic.operational_alerts[0].arn
  ] : []
}

resource "aws_sns_topic" "operational_alerts" {
  count = var.enable_operational_alarms && length(var.operational_alarm_email_addresses) > 0 ? 1 : 0

  name = "${local.name_prefix}-operational-alerts"
}

resource "aws_sns_topic_subscription" "operational_alert_emails" {
  for_each = var.enable_operational_alarms ? toset(var.operational_alarm_email_addresses) : toset([])

  topic_arn = aws_sns_topic.operational_alerts[0].arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_metric_alarm" "api_lambda_error_rate" {
  count = var.enable_operational_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-api-lambda-error-rate"
  alarm_description   = "API Lambda error rate is greater than 5% for 2 out of 3 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  metric_query {
    id          = "errors"
    return_data = false

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      period      = 60
      stat        = "Sum"

      dimensions = {
        FunctionName = module.api_lambda.lambda_function_name
      }
    }
  }

  metric_query {
    id          = "invocations"
    return_data = false

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      period      = 60
      stat        = "Sum"

      dimensions = {
        FunctionName = module.api_lambda.lambda_function_name
      }
    }
  }

  metric_query {
    id          = "error_rate"
    expression  = "IF(invocations > 0, errors * 100 / invocations, 0)"
    label       = "Error rate %"
    return_data = true
  }
}

resource "aws_cloudwatch_metric_alarm" "api_lambda_throttles" {
  count = var.enable_operational_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-api-lambda-throttles"
  alarm_description   = "API Lambda has throttled invocations."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    FunctionName = module.api_lambda.lambda_function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_lambda_duration_p99" {
  count = var.enable_operational_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-api-lambda-duration-p99"
  alarm_description   = "API Lambda p99 duration is above 80% of the configured timeout."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  period              = 60
  extended_statistic  = "p99"
  threshold           = var.api_lambda_timeout_seconds * 800
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    FunctionName = module.api_lambda.lambda_function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_5xx" {
  count = var.enable_operational_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-http-api-5xx"
  alarm_description   = "HTTP API returned 5xx responses."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 1
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    ApiId = module.api_lambda.api_id
    Stage = module.api_lambda.api_stage_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_latency_p99" {
  count = var.enable_operational_alarms ? 1 : 0

  alarm_name          = "${local.name_prefix}-http-api-latency-p99"
  alarm_description   = "HTTP API p99 latency is greater than 5 seconds."
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  period              = 60
  extended_statistic  = "p99"
  threshold           = 5000
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    ApiId = module.api_lambda.api_id
    Stage = module.api_lambda.api_stage_name
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  count = var.enable_operational_alarms && length(var.db_subnet_ids) > 0 ? 1 : 0

  alarm_name          = "${local.name_prefix}-rds-cpu-high"
  alarm_description   = "RDS PostgreSQL CPU utilization is greater than 80%."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  period              = 60
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    DBInstanceIdentifier = module.rds.db_instance_identifier
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_free_storage_low" {
  count = var.enable_operational_alarms && length(var.db_subnet_ids) > 0 ? 1 : 0

  alarm_name          = "${local.name_prefix}-rds-free-storage-low"
  alarm_description   = "RDS PostgreSQL free storage is below 2 GiB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  period              = 60
  statistic           = "Average"
  threshold           = 2147483648
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.operational_alarm_actions
  ok_actions          = local.operational_alarm_actions

  dimensions = {
    DBInstanceIdentifier = module.rds.db_instance_identifier
  }
}
