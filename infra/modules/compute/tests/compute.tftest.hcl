# Offline: mock provider. Run: terraform -chdir=infra/modules/compute test
# Behaviour beyond the frozen spec (tests/spec/tf/tests/m4_compute.tftest.hcl).

mock_provider "aws" {
  source = "../../tests/mocks"
}

variables {
  image_uri         = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/iiot-monitor:abc123"
  queue_arn         = "arn:aws:sqs:ap-south-1:123456789012:iiot-telemetry"
  queue_url         = "https://sqs.ap-south-1.amazonaws.com/123456789012/iiot-telemetry"
  dlq_name          = "iiot-telemetry-dlq"
  alert_topic_arn   = "arn:aws:sns:ap-south-1:123456789012:iiot-alerts"
  iot_log_group_arn = "arn:aws:logs:ap-south-1:123456789012:log-group:AWSIotLogsV2"
  table_names       = { readings = "iiot-readings", latest = "iiot-latest", alerts = "iiot-alerts" }
  table_arns        = ["arn:aws:dynamodb:ap-south-1:123456789012:table/iiot-readings"]
}

run "scaled_to_zero_has_no_not_running_alarm" {
  command = apply
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.task_not_running) == 0
    error_message = "with 0 tasks a 'not running' alarm would be in ALARM forever"
  }
}

run "running_task_is_watched" {
  command = apply
  variables { desired_count = 1 }
  assert {
    condition     = aws_cloudwatch_metric_alarm.task_not_running[0].treat_missing_data == "breaching"
    error_message = "no CPU datapoints (task gone) must count as breaching"
  }
}

run "never_two_workers" {
  command = plan
  variables { desired_count = 2 }
  expect_failures = [var.desired_count]
}

run "one_worker_at_a_time_during_deploys" {
  command = apply
  assert {
    condition     = aws_ecs_service.worker.deployment_maximum_percent == 100 && aws_ecs_service.worker.deployment_minimum_healthy_percent == 0
    error_message = "detection state is per-process: stop the old task before starting the new one"
  }
}

run "execution_role_scoped_to_our_repo_and_log_group" {
  command = apply
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.execution.policy).Statement :
    s.Resource != "*" || s.Action == ["ecr:GetAuthorizationToken"]])
    error_message = "only GetAuthorizationToken (account-wide) may use Resource = *"
  }
  assert {
    condition     = jsondecode(aws_iam_role.task.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "trust policy needs the confused-deputy guard"
  }
}

run "both_roles_carry_the_boundary" {
  command = apply
  variables { permissions_boundary_arn = "arn:aws:iam::123456789012:policy/iiot-workload-boundary" }
  assert {
    condition = (aws_iam_role.task.permissions_boundary == "arn:aws:iam::123456789012:policy/iiot-workload-boundary"
    && aws_iam_role.execution.permissions_boundary == "arn:aws:iam::123456789012:policy/iiot-workload-boundary")
    error_message = "CI may only manage roles that carry the workload boundary"
  }
}
