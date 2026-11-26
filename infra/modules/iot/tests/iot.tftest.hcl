# Offline: mock provider (apply is simulated locally, no AWS calls). Run: terraform -chdir=infra/modules/iot test

mock_provider "aws" {
  source = "../../tests/mocks"
}

variables {
  devices = {
    "feeder-01"  = { type = "feeder_bay", site = "s1" }
    "edge-gw-01" = { type = "pump_station", site = "s2" }
  }
  csr_dir = "../../envs/dev/device-csrs" # real, public CSRs (no private keys involved)
}

run "one_identity_per_device" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = length(aws_iot_thing.device) == 2 && length(aws_iot_certificate.device) == 2
    error_message = "every device needs its own Thing and its own certificate"
  }
  assert {
    condition     = alltrue([for c in aws_iot_certificate.device : c.csr != null && c.active])
    error_message = "certificates must be signed from device CSRs (keys never leave the device) and active"
  }
  assert {
    condition     = aws_iot_thing.device["feeder-01"].attributes.device_type == "feeder_bay"
    error_message = "Thing attributes carry device_type: the detection registry (R1) reads it"
  }
}

run "policy_pins_device_to_its_own_client_id_and_topic" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition = toset(flatten([for s in jsondecode(aws_iot_policy.device.policy).Statement : s.Action])) == toset([
      "iot:Connect", "iot:Publish",
    ])
    error_message = "devices may only connect and publish (no subscribe/receive, no wildcards)"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iot_policy.device.policy).Statement :
    strcontains(s.Resource, "$${iot:Connection.Thing.ThingName}")])
    error_message = "every resource must be scoped to the connecting Thing's own name"
  }
  assert {
    condition = one([for s in jsondecode(aws_iot_policy.device.policy).Statement : s.Resource
    if s.Action == "iot:Publish"]) == "arn:aws:iot:ap-south-1:123456789012:topic/iiot/$${iot:Connection.Thing.ThingName}/telemetry"
    error_message = "publish only to iiot/<own Thing name>/telemetry"
  }
  assert {
    condition = one([for s in jsondecode(aws_iot_policy.device.policy).Statement : s.Condition
    if s.Action == "iot:Connect"]).Bool["iot:Connection.Thing.IsAttached"] == "true"
    error_message = "connect requires the certificate to be attached to a registered Thing"
  }
}

run "logging_has_finite_retention" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = aws_cloudwatch_log_group.iot.name == "AWSIotLogsV2" && aws_cloudwatch_log_group.iot.retention_in_days == 7
    error_message = "IoT logs (R1 auth-failure source) must exist with 7-day retention"
  }
  assert {
    condition     = contains(["WARN", "ERROR", "INFO", "DEBUG"], aws_iot_logging_options.this.default_log_level)
    error_message = "logging level must capture ERROR events (auth failures)"
  }
}
