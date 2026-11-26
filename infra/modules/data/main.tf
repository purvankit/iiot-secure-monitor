# DynamoDB tables. On-demand (PAY_PER_REQUEST): no provisioned capacity billing while idle,
# which suits a demo that runs a few hours a week.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

resource "aws_dynamodb_table" "readings" {
  name         = "iiot-readings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"
  range_key    = "sk" # "<ts>#<seq>": time-ordered per device

  attribute {
    name = "device_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  # Readings expire automatically (free deletes); S3 keeps the raw archive.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "latest" {
  name         = "iiot-latest"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"

  attribute {
    name = "device_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "alerts" {
  name         = "iiot-alerts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"
  range_key    = "sk" # "<detected_at>#<alert_id>"

  attribute {
    name = "device_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  # Alerts are the security record: recover from accidental deletes/overwrites.
  point_in_time_recovery {
    enabled = var.alerts_pitr
  }
}
