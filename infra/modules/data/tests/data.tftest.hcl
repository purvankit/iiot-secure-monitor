# Offline: mock provider (apply is simulated locally, no AWS calls). Run: terraform -chdir=infra/modules/data test

mock_provider "aws" {
  source = "../../tests/mocks"
}

run "tables_cost_nothing_when_idle" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition = alltrue([for t in [aws_dynamodb_table.readings, aws_dynamodb_table.latest, aws_dynamodb_table.alerts] :
    t.billing_mode == "PAY_PER_REQUEST"])
    error_message = "on-demand billing only: provisioned capacity bills while idle"
  }
}

run "keys_match_what_the_service_writes" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = aws_dynamodb_table.readings.hash_key == "device_id" && aws_dynamodb_table.readings.range_key == "sk"
    error_message = "service/store.py writes readings with device_id + sk"
  }
  assert {
    condition     = aws_dynamodb_table.latest.hash_key == "device_id" && aws_dynamodb_table.latest.range_key == null
    error_message = "latest holds exactly one item per device"
  }
  assert {
    condition     = aws_dynamodb_table.alerts.hash_key == "device_id" && aws_dynamodb_table.alerts.range_key == "sk"
    error_message = "service/store.py writes alerts with device_id + sk"
  }
}

run "readings_expire_alerts_are_recoverable" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = aws_dynamodb_table.readings.ttl[0].enabled && aws_dynamodb_table.readings.ttl[0].attribute_name == "expires_at"
    error_message = "readings TTL must use expires_at (set by store.put_reading)"
  }
  assert {
    condition     = aws_dynamodb_table.alerts.point_in_time_recovery[0].enabled
    error_message = "alerts are the security record: PITR on by default"
  }
}
