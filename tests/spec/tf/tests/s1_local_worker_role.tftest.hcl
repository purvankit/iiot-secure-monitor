# S1 SPEC (written before the code, 2026-11-27). Frozen: changes need the owner's approval.
# Contract for the IAM role a local Kubernetes pod uses to reach the same AWS pipeline:
# created only on request, assumable only by the named principal, 1-hour sessions, the
# workload boundary, and never more than the Fargate task role (no metrics push: in-cluster
# Prometheus pulls).

mock_provider "aws" {
  source = "../../../infra/tests/mocks"
}

variables {
  image_uri                = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/iiot-monitor:abc123"
  queue_arn                = "arn:aws:sqs:ap-south-1:123456789012:iiot-telemetry"
  queue_url                = "https://sqs.ap-south-1.amazonaws.com/123456789012/iiot-telemetry"
  dlq_name                 = "iiot-telemetry-dlq"
  alert_topic_arn          = "arn:aws:sns:ap-south-1:123456789012:iiot-alerts"
  iot_log_group_arn        = "arn:aws:logs:ap-south-1:123456789012:log-group:AWSIotLogsV2"
  table_names              = { readings = "iiot-readings", latest = "iiot-latest", alerts = "iiot-alerts" }
  table_arns               = ["arn:aws:dynamodb:ap-south-1:123456789012:table/iiot-readings"]
  prometheus_workspace_arn = "arn:aws:aps:ap-south-1:123456789012:workspace/ws-mock"
  permissions_boundary_arn = "arn:aws:iam::123456789012:policy/iiot-workload-boundary"
}

run "no_local_role_unless_requested" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = length(aws_iam_role.local_worker) == 0
    error_message = "the local-worker role exists only when a trusted principal is given"
  }
}

run "local_role_is_scoped_to_one_principal" {
  command = apply
  module { source = "../../../infra/modules/compute" }
  variables {
    local_worker_principal_arn = "arn:aws:iam::123456789012:user/example-dev"
  }

  assert {
    condition     = jsondecode(aws_iam_role.local_worker[0].assume_role_policy).Statement[0].Principal.AWS == "arn:aws:iam::123456789012:user/example-dev"
    error_message = "only the named principal may assume it (no account root, no wildcard)"
  }
  assert {
    condition     = aws_iam_role.local_worker[0].max_session_duration <= 3600
    error_message = "sessions last at most 1 hour"
  }
  assert {
    condition     = aws_iam_role.local_worker[0].permissions_boundary == "arn:aws:iam::123456789012:policy/iiot-workload-boundary"
    error_message = "carries the workload boundary like every project role"
  }
  assert {
    condition = length(setsubtract(
      toset(flatten([for s in jsondecode(aws_iam_role_policy.local_worker[0].policy).Statement : s.Action])),
      toset(flatten([for s in jsondecode(aws_iam_role_policy.task.policy).Statement : s.Action]))
    )) == 0
    error_message = "never more than the Fargate task role"
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.local_worker[0].policy, "aps:")
    error_message = "no metrics push: in-cluster Prometheus scrapes the pod"
  }
}

run "rejects_wildcard_principal" {
  command = plan
  module { source = "../../../infra/modules/compute" }
  variables {
    local_worker_principal_arn = "*"
  }
  expect_failures = [var.local_worker_principal_arn]
}
