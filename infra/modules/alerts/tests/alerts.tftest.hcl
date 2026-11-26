# Offline: mock provider (apply is simulated locally, no AWS calls). Run: terraform -chdir=infra/modules/alerts test

mock_provider "aws" {
  source = "../../tests/mocks"
}

variables {
  alert_email = "test@example.com"
}

run "alerts_topic_encrypted_and_emails" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = aws_sns_topic.alerts.kms_master_key_id == aws_kms_key.alerts.arn
    error_message = "alert topic must be encrypted at rest with OUR key (alias/aws/sns blocks CloudWatch alarms)"
  }
  assert {
    condition = anytrue([for s in jsondecode(aws_kms_key.alerts.policy).Statement :
    try(s.Principal.Service, "") == "cloudwatch.amazonaws.com"])
    error_message = "key policy must let CloudWatch alarms publish to the encrypted topic"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_kms_key.alerts.policy).Statement :
      try(s.Principal.AWS, "") != "*" || (
        contains(keys(s.Condition.StringEquals), "kms:ViaService")
    && contains(keys(s.Condition.StringEquals), "kms:CallerAccount"))])
    error_message = "a key statement open to all principals must be limited to SNS + this account"
  }
  assert {
    condition     = aws_sns_topic_subscription.email.protocol == "email"
    error_message = "alerts are delivered by email"
  }
}

run "email_variable_is_sensitive" {
  command = plan

  variables {
    alert_email = "someone@example.com"
  }
  # If the variable were not sensitive, this output would leak it into plan/CI logs.
  assert {
    condition     = issensitive(var.alert_email)
    error_message = "alert_email must be sensitive so it never appears in plan output or CI logs"
  }
}
