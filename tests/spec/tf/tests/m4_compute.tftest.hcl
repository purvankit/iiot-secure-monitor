# M4 SPEC (written before the code, 2026-11-26). Frozen: changing an assertion needs the
# owner's approval. Defines the contract for infra/modules/compute.
# Offline: mock provider; `apply` here is simulated locally and never calls AWS.

mock_provider "aws" {
  source = "../../../infra/tests/mocks"
}

variables {
  image_uri         = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/iiot-monitor:abc123"
  queue_arn         = "arn:aws:sqs:ap-south-1:123456789012:iiot-telemetry"
  queue_url         = "https://sqs.ap-south-1.amazonaws.com/123456789012/iiot-telemetry"
  dlq_name          = "iiot-telemetry-dlq"
  alert_topic_arn   = "arn:aws:sns:ap-south-1:123456789012:iiot-alerts"
  iot_log_group_arn = "arn:aws:logs:ap-south-1:123456789012:log-group:AWSIotLogsV2"
  table_names       = { readings = "iiot-readings", latest = "iiot-latest", alerts = "iiot-alerts" }
  table_arns = [
    "arn:aws:dynamodb:ap-south-1:123456789012:table/iiot-readings",
    "arn:aws:dynamodb:ap-south-1:123456789012:table/iiot-latest",
    "arn:aws:dynamodb:ap-south-1:123456789012:table/iiot-alerts",
  ]
}

run "cost_safe_defaults" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = aws_ecs_service.worker.desired_count == 0
    error_message = "Fargate bills per task-hour: desired_count must default to 0"
  }
  assert {
    condition     = aws_ecs_task_definition.worker.cpu == "256" && aws_ecs_task_definition.worker.memory == "512"
    error_message = "smallest Fargate size (0.25 vCPU / 0.5 GB), as costed in CLAUDE.md"
  }
  assert {
    condition     = aws_ecs_service.worker.launch_type == "FARGATE" && contains(aws_ecs_task_definition.worker.requires_compatibilities, "FARGATE")
    error_message = "Fargate, not EC2"
  }
  assert {
    condition     = aws_cloudwatch_log_group.worker.retention_in_days == 7
    error_message = "worker logs keep 7 days"
  }
  assert {
    condition     = jsondecode(aws_ecr_lifecycle_policy.service.policy).rules[0].selection.countNumber <= 10
    error_message = "ECR keeps at most 10 images (storage cost)"
  }
}

run "network_has_no_inbound_and_no_nat" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = one(aws_ecs_service.worker.network_configuration).assign_public_ip
    error_message = "public subnet + public IP instead of a NAT gateway (cost decision in CLAUDE.md)"
  }
  assert {
    condition     = length(aws_security_group.worker.ingress) == 0
    error_message = "worker accepts NO inbound connections"
  }
  assert {
    condition     = alltrue([for e in aws_security_group.worker.egress : e.from_port == 443 && e.to_port == 443 && e.protocol == "tcp"])
    error_message = "egress only HTTPS (AWS APIs, ECR)"
  }
  assert {
    condition     = contains(one(aws_ecs_service.worker.network_configuration).security_groups, aws_security_group.worker.id)
    error_message = "service must use the locked-down security group"
  }
}

run "image_supply_chain" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = one(aws_ecr_repository.service.image_scanning_configuration).scan_on_push
    error_message = "ECR must scan images on push"
  }
  assert {
    condition     = aws_ecr_repository.service.image_tag_mutability == "IMMUTABLE"
    error_message = "immutable tags: a deployed tag can never be silently replaced"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].image == var.image_uri
    error_message = "task runs exactly the image it was given"
  }
}

run "container_contract_matches_service_code" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition = toset([for e in jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].environment : e.name]) == toset([
      "AWS_REGION", "QUEUE_URL", "TABLE_READINGS", "TABLE_LATEST", "TABLE_ALERTS", "ALERT_TOPIC_ARN",
    ])
    error_message = "env vars must be exactly what iiot_monitor/worker.py reads (no secrets in env)"
  }
  assert {
    condition     = one([for e in jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].environment : e.value if e.name == "AWS_REGION"]) == "ap-south-1"
    error_message = "worker must run in ap-south-1"
  }
  assert {
    condition     = strcontains(join(" ", jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].healthCheck.command), "/health")
    error_message = "container health check must hit /health (served by worker.serve_http)"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].logConfiguration.logDriver == "awslogs"
    error_message = "container logs go to CloudWatch"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].readonlyRootFilesystem
    error_message = "read-only root filesystem (the worker writes nothing to disk)"
  }
}

run "task_role_is_least_privilege" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = aws_iam_role.task.name != aws_iam_role.execution.name
    error_message = "separate roles: execution (pull image, write logs) vs task (app permissions)"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_role_policy.task.policy).Statement : s.Action])) == toset([
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "dynamodb:PutItem", "dynamodb:Scan",
      "sns:Publish", "iot:ListThings", "logs:FilterLogEvents",
    ])
    error_message = "task role = exactly the API calls the worker makes, nothing more"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
    s.Resource != "*" || toset(flatten([s.Action])) == toset(["iot:ListThings"])])
    error_message = "only iot:ListThings (not resource-scopable) may use Resource = *"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
    !contains(flatten([s.Action]), "sqs:ReceiveMessage") || s.Resource == var.queue_arn])
    error_message = "SQS access only on the telemetry queue"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.task.policy).Statement :
    !contains(flatten([s.Action]), "sns:Publish") || s.Resource == var.alert_topic_arn])
    error_message = "SNS publish only to the alerts topic"
  }
}

run "alarms_notify_the_alert_topic" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = strcontains(aws_cloudwatch_log_metric_filter.processing_errors.pattern, "processing failed")
    error_message = "metric filter must match worker.py's 'processing failed' log line"
  }
  assert {
    condition     = aws_cloudwatch_log_metric_filter.processing_errors.log_group_name == aws_cloudwatch_log_group.worker.name
    error_message = "metric filter must watch the worker's log group"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.processing_errors.alarm_actions == toset([var.alert_topic_arn]) && aws_cloudwatch_metric_alarm.processing_errors.threshold >= 1
    error_message = "processing errors alarm -> SNS (M4 'done when': fires on a forced error)"
  }
  assert {
    condition = (aws_cloudwatch_metric_alarm.dlq_not_empty.namespace == "AWS/SQS"
      && aws_cloudwatch_metric_alarm.dlq_not_empty.metric_name == "ApproximateNumberOfMessagesVisible"
      && aws_cloudwatch_metric_alarm.dlq_not_empty.dimensions["QueueName"] == var.dlq_name
      && aws_cloudwatch_metric_alarm.dlq_not_empty.threshold == 0
    && aws_cloudwatch_metric_alarm.dlq_not_empty.comparison_operator == "GreaterThanThreshold")
    error_message = "any message in the DLQ must raise an alarm"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_actions == toset([var.alert_topic_arn])
    error_message = "DLQ alarm -> SNS"
  }
  assert {
    condition     = length(jsondecode(aws_cloudwatch_dashboard.main.dashboard_body).widgets) >= 1
    error_message = "a CloudWatch dashboard exists"
  }
}

run "rollback_on_failed_deploy" {
  command = apply
  module { source = "../../../infra/modules/compute" }

  assert {
    condition     = one(aws_ecs_service.worker.deployment_circuit_breaker).enable && one(aws_ecs_service.worker.deployment_circuit_breaker).rollback
    error_message = "a deploy whose tasks keep failing must roll back automatically"
  }
}
