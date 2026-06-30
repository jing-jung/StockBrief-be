locals {
  enabled       = var.enabled && var.container_uri != ""
  runtime_name  = replace("${var.name_prefix}_agent", "-", "_")
  endpoint_name = replace("${var.name_prefix}_default", "-", "_")

  network_configuration = var.network_mode == "VPC" ? {
    NetworkMode = "VPC"
    NetworkModeConfig = {
      SecurityGroups = var.security_group_ids
      Subnets        = var.subnet_ids
    }
    } : {
    NetworkMode       = "PUBLIC"
    NetworkModeConfig = null
  }
}

data "aws_iam_policy_document" "assume_agentcore" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  count = local.enabled ? 1 : 0

  name               = "${var.name_prefix}-agentcore-runtime-role"
  assume_role_policy = data.aws_iam_policy_document.assume_agentcore.json
}

resource "aws_cloudwatch_log_group" "runtime" {
  count = local.enabled ? 1 : 0

  name              = "/aws/bedrock-agentcore/${local.runtime_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "runtime" {
  count = local.enabled ? 1 : 0

  dynamic "statement" {
    for_each = var.bedrock_chat_inference_profile_arn == "" ? [] : [var.bedrock_chat_inference_profile_arn]

    content {
      sid = "InvokeConfiguredInferenceProfile"
      actions = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      resources = [statement.value]
    }
  }

  dynamic "statement" {
    for_each = length(var.bedrock_chat_foundation_model_arns) == 0 ? [] : [var.bedrock_chat_foundation_model_arns]

    content {
      sid = "InvokeConfiguredFoundationModels"
      actions = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      resources = statement.value

      dynamic "condition" {
        for_each = var.bedrock_chat_inference_profile_arn == "" ? [] : [var.bedrock_chat_inference_profile_arn]

        content {
          test     = "StringEquals"
          variable = "bedrock:InferenceProfileArn"
          values   = [condition.value]
        }
      }
    }
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.runtime[0].arn,
      "${aws_cloudwatch_log_group.runtime[0].arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "runtime" {
  count = local.enabled ? 1 : 0

  name   = "${var.name_prefix}-agentcore-runtime"
  role   = aws_iam_role.runtime[0].id
  policy = data.aws_iam_policy_document.runtime[0].json
}

resource "aws_cloudformation_stack" "runtime" {
  count = local.enabled ? 1 : 0

  name         = "${var.name_prefix}-agentcore-runtime"
  capabilities = ["CAPABILITY_NAMED_IAM"]

  template_body = yamlencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "StockBrief AgentCore Runtime and default endpoint."
    Resources = {
      AgentRuntime = {
        Type = "AWS::BedrockAgentCore::Runtime"
        Properties = {
          AgentRuntimeArtifact = {
            ContainerConfiguration = {
              ContainerUri = var.container_uri
            }
          }
          AgentRuntimeName     = local.runtime_name
          NetworkConfiguration = local.network_configuration
          RoleArn              = aws_iam_role.runtime[0].arn
          EnvironmentVariables = var.environment_variables
          RequestHeaderConfiguration = {
            RequestHeaderAllowlist = var.request_header_allowlist
          }
        }
      }
      DefaultEndpoint = {
        Type = "AWS::BedrockAgentCore::RuntimeEndpoint"
        Properties = {
          AgentRuntimeId = {
            "Fn::GetAtt" = ["AgentRuntime", "AgentRuntimeId"]
          }
          AgentRuntimeVersion = {
            "Fn::GetAtt" = ["AgentRuntime", "AgentRuntimeVersion"]
          }
          Name        = local.endpoint_name
          Description = "Default StockBrief agent runtime endpoint."
        }
      }
    }
    Outputs = {
      RuntimeArn = {
        Value = {
          "Fn::GetAtt" = ["AgentRuntime", "AgentRuntimeArn"]
        }
      }
      RuntimeId = {
        Value = {
          "Fn::GetAtt" = ["AgentRuntime", "AgentRuntimeId"]
        }
      }
      RuntimeEndpointName = {
        Value = local.endpoint_name
      }
    }
  })

  depends_on = [aws_iam_role_policy.runtime]
}
