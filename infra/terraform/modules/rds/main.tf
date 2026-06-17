resource "aws_db_subnet_group" "postgres" {
  count      = length(var.subnet_ids) > 0 ? 1 : 0
  name       = "${var.name_prefix}-postgres"
  subnet_ids = var.subnet_ids
}

resource "aws_db_instance" "postgres" {
  count = length(var.subnet_ids) > 0 ? 1 : 0

  identifier                  = "${var.name_prefix}-postgres"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = var.allocated_storage_gb
  db_name                     = var.db_name
  username                    = "stockbrief_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.postgres[0].name
  vpc_security_group_ids      = var.security_group_ids
  backup_retention_period     = var.backup_retention_period
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = var.skip_final_snapshot
  publicly_accessible         = false
  storage_encrypted           = true
}
