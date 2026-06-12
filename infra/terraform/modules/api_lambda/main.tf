data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_lambda" {
  name               = "${var.name_prefix}-api-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.api_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "vpc_execution" {
  role       = aws_iam_role.api_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "secrets_access" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      var.database_secret_arn,
      var.external_api_secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "secrets_access" {
  name   = "${var.name_prefix}-api-secrets-access"
  role   = aws_iam_role.api_lambda.id
  policy = data.aws_iam_policy_document.secrets_access.json
}

data "aws_iam_policy_document" "agentcore_invoke" {
  count = var.agentcore_runtime_arn == "" ? 0 : 1

  statement {
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [var.agentcore_runtime_arn]
  }
}

resource "aws_iam_role_policy" "agentcore_invoke" {
  count = var.agentcore_runtime_arn == "" ? 0 : 1

  name   = "${var.name_prefix}-api-agentcore-invoke"
  role   = aws_iam_role.api_lambda.id
  policy = data.aws_iam_policy_document.agentcore_invoke[0].json
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.name_prefix}-api"
  role             = aws_iam_role.api_lambda.arn
  handler          = "app.lambda_handler.handler"
  runtime          = var.runtime
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  timeout          = var.timeout_seconds
  memory_size      = var.memory_mb

  environment {
    variables = merge(
      var.environment_variables,
      {
        DATABASE_SECRET_ARN     = var.database_secret_arn
        DATABASE_HOST           = var.database_host
        DATABASE_PORT           = tostring(var.database_port)
        DATABASE_NAME           = var.database_name
        EXTERNAL_API_SECRET_ARN = var.external_api_secret_arn
      }
    )
  }

  dynamic "vpc_config" {
    for_each = length(var.lambda_subnet_ids) > 0 ? [1] : []
    content {
      subnet_ids         = var.lambda_subnet_ids
      security_group_ids = var.lambda_security_group_ids
    }
  }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-http-api"
  protocol_type = "HTTP"
}

locals {
  jwt_authorizer_enabled = var.jwt_authorizer_issuer != "" && length(var.jwt_authorizer_audience) > 0
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  count            = local.jwt_authorizer_enabled ? 1 : 0
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.name_prefix}-cognito-jwt"

  jwt_configuration {
    audience = var.jwt_authorizer_audience
    issuer   = var.jwt_authorizer_issuer
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "root" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "ANY /"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "protected" {
  for_each = local.jwt_authorizer_enabled ? toset(var.protected_route_keys) : []

  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.value
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito[0].id
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = var.api_gateway_log_group_arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      protocol         = "$context.protocol"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
