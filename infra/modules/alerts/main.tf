# SNS topic for security alerts, delivered by email.
# The email subscription stays "PendingConfirmation" until the link in AWS's email is clicked.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Customer-managed key (~$1/month). The free AWS-managed key alias/aws/sns can't be used:
# its key policy can't be edited, so CloudWatch alarms (M4) could never publish to the topic.
# The alarm would sit in ALARM state while no email ever arrived.
resource "aws_kms_key" "alerts" {
  description             = "Encrypts the iiot-alerts SNS topic at rest"
  deletion_window_in_days = 7
  # Rotation adds ~$1/month per rotated version; off for this dev budget (turn on in prod).
  enable_key_rotation = false
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Standard first statement: lets IAM policies in this account manage the key.
        # Without it the key can become unmanageable.
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        # guardrail: allow-wildcard (key policy admin statement; "*" here means this key only)
        Action = "kms:*"
        # guardrail: allow-wildcard (in a key policy, Resource "*" means this key itself)
        Resource = "*"
      },
      {
        # CloudWatch alarms publish to the topic, so SNS needs this key on their behalf.
        Sid       = "CloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        # guardrail: allow-wildcard (in a key policy, Resource "*" means this key itself)
        Resource  = "*"
        Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
      },
      {
        # Principals in this account (the worker's task role) may use the key only through
        # SNS. So the task role needs no KMS permission of its own, and nobody can use
        # the key directly to decrypt other data.
        Sid       = "AccountPrincipalsViaSns"
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        # guardrail: allow-wildcard (in a key policy, Resource "*" means this key itself)
        Resource = "*"
        Condition = { StringEquals = {
          "kms:CallerAccount" = data.aws_caller_identity.current.account_id
          "kms:ViaService"    = "sns.${data.aws_region.current.region}.amazonaws.com"
        } }
      },
    ]
  })
}

resource "aws_kms_alias" "alerts" {
  name          = "alias/iiot-alerts"
  target_key_id = aws_kms_key.alerts.key_id
}

resource "aws_sns_topic" "alerts" {
  name              = "iiot-alerts"
  kms_master_key_id = aws_kms_key.alerts.arn
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
