# IoT Rules Engine fan-out: every telemetry message goes to
#   1. SQS (buffer for the ingest/detection service, with a dead-letter queue)
#   2. S3  (raw archive for forensics/replay analysis, auto-expiring)
# The rule also stamps broker-side facts the device can't forge: the MQTT client ID,
# the topic's device segment and the broker receive time. Detection compares these
# with the payload's device_id and ts.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_caller_identity" "current" {}

# ---------------- SQS ----------------

resource "aws_sqs_queue" "dlq" {
  name                      = "iiot-telemetry-dlq"
  message_retention_seconds = 1209600 # 14 days (max): time to inspect poison messages
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "telemetry" {
  name                       = "iiot-telemetry"
  message_retention_seconds  = 345600 # 4 days: survives a weekend with the service scaled to 0
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20 # long polling: fewer empty (billed) receives
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5 # after 5 failed processing attempts, park it in the DLQ
  })
}

# ---------------- S3 raw archive ----------------

resource "aws_s3_bucket" "raw" {
  bucket        = "iiot-secure-monitor-raw-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # dev only: lets `terraform destroy` remove a non-empty bucket
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    id     = "expire-raw"
    status = "Enabled"
    filter { prefix = "raw/" }
    expiration { days = var.raw_retention_days }
  }
}

# ---------------- Rule + least-privilege role ----------------

resource "aws_cloudwatch_log_group" "rule_errors" {
  name              = "/iiot/iot-rule-errors"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "rule" {
  name = "iiot-telemetry-rule"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "iot.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "rule" {
  role = aws_iam_role.rule.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.telemetry.arn },
      { Effect = "Allow", Action = "s3:PutObject", Resource = "${aws_s3_bucket.raw.arn}/raw/*" },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.rule_errors.arn}:*"
      },
    ]
  })
}

resource "aws_iot_topic_rule" "telemetry" {
  name        = "iiot_telemetry_fanout" # rule names: letters, digits, underscore only
  enabled     = true
  sql_version = "2016-03-23"
  sql         = <<-SQL
    SELECT *, clientid() AS mqtt_client_id, topic(2) AS topic_device_id,
           timestamp() AS ingest_ts
    FROM 'iiot/+/telemetry'
  SQL

  sqs {
    queue_url  = aws_sqs_queue.telemetry.url
    role_arn   = aws_iam_role.rule.arn
    use_base64 = false
  }

  s3 {
    bucket_name = aws_s3_bucket.raw.bucket
    role_arn    = aws_iam_role.rule.arn
    # Partitioned by device and day; newuuid() avoids key collisions.
    key = "raw/$${topic(2)}/$${parse_time('yyyy/MM/dd', timestamp())}/$${newuuid()}.json"
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.rule_errors.name
      role_arn       = aws_iam_role.rule.arn
    }
  }

  depends_on = [aws_iam_role_policy.rule]
}
