# SNS topic for security alerts, delivered by email.
# The email subscription stays "PendingConfirmation" until the link in AWS's email is clicked.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

resource "aws_sns_topic" "alerts" {
  name              = "iiot-alerts"
  kms_master_key_id = "alias/aws/sns" # AWS-managed key: encrypted at rest, no KMS key fee
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
