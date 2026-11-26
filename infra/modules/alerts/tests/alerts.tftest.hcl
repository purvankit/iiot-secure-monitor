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
    condition     = aws_sns_topic.alerts.kms_master_key_id == "alias/aws/sns"
    error_message = "alert topic must be encrypted at rest"
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
