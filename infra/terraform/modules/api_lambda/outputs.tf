output "lambda_function_name" {
  value = aws_lambda_function.api.function_name
}

output "api_base_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "api_id" {
  value = aws_apigatewayv2_api.http.id
}

output "api_stage_name" {
  value = aws_apigatewayv2_stage.default.name
}
