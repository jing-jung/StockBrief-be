resource "aws_secretsmanager_secret" "database" {
  name        = "${var.name_prefix}/database"
  description = "StockBrief database connection settings. Fill secret values outside git."
}

resource "aws_secretsmanager_secret_version" "database_placeholder" {
  secret_id = aws_secretsmanager_secret.database.id
  secret_string = jsonencode({
    DATABASE_URL = "postgresql+psycopg://PLACEHOLDER_USER:PLACEHOLDER_PASSWORD@PLACEHOLDER_HOST:5432/stockbrief"
  })

  lifecycle {
    ignore_changes = all
  }
}

resource "aws_secretsmanager_secret" "external_api" {
  name        = "${var.name_prefix}/external-api"
  description = "StockBrief OpenDART, NAVER, and fallback data provider credentials. Fill secret values outside git."
}

resource "aws_secretsmanager_secret_version" "external_api_placeholder" {
  secret_id = aws_secretsmanager_secret.external_api.id
  secret_string = jsonencode({
    OPENDART_API_KEY    = ""
    NAVER_CLIENT_ID     = ""
    NAVER_CLIENT_SECRET = ""
    KRX_DATA_PATH       = ""
  })

  lifecycle {
    ignore_changes = all
  }
}
