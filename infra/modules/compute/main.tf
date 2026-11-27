# Compute: the detection service as one ECS Fargate task.
#
#   ECR (scanned, immutable tags) --image--> ECS service (0 or 1 task, public subnet)
#   task role: only the API calls worker.py makes;  logs -> CloudWatch -> metric filter -> alarm -> SNS
#
# Cost decisions (CLAUDE.md §7): no NAT gateway (public subnet + public IP, no inbound),
# no load balancer (nothing calls the worker), Container Insights off, desired_count 0 by default.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name       = "iiot-worker"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  queue_name = basename(var.queue_url) # SQS CloudWatch metrics are keyed by queue name
  port       = 8080                    # worker.serve_http: /health + Prometheus /metrics

  # Metrics push (ADR 0001): the ADOT sidecar scrapes the worker over localhost (containers in
  # an awsvpc task share one network namespace, so no inbound rule is needed) and remote-writes
  # to AMP with SigV4, signed by the task role.
  workspace_id     = element(split("/", var.prometheus_workspace_arn), 1)
  remote_write_url = "https://aps-workspaces.${local.region}.amazonaws.com/workspaces/${local.workspace_id}/api/v1/remote_write"
  collector_config = yamlencode({
    extensions = { sigv4auth = { region = local.region, service = "aps" } }
    receivers = { prometheus = { config = { scrape_configs = [{
      job_name        = "iiot-worker"
      scrape_interval = "15s"
      static_configs  = [{ targets = ["localhost:${local.port}"] }]
    }] } } }
    exporters = { prometheus_remote_write = {
      endpoint = local.remote_write_url
      auth     = { authenticator = "sigv4auth" }
    } }
    service = {
      extensions = ["sigv4auth"]
      pipelines  = { metrics = { receivers = ["prometheus"], exporters = ["prometheus_remote_write"] } }
    }
  })
}

# ---------------------------------------------------------------- network (all free)

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true # the task resolves AWS API hostnames via the VPC resolver
  enable_dns_hostnames = true
  tags                 = { Name = "iiot" }
}

# Strip the VPC's default security group (it allows all traffic between its members), so
# nothing launched by accident gets open rules.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

# Two AZs so ECS can place the task elsewhere if one AZ has trouble. Subnets cost nothing.
resource "aws_subnet" "public" {
  for_each          = { a = 0, b = 1 }
  vpc_id            = aws_vpc.main.id
  availability_zone = "${local.region}${each.key}"
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, each.value)
  tags              = { Name = "iiot-public-${each.key}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# The public IP is for OUTBOUND calls only (SQS, DynamoDB, SNS, ECR...). No inbound rules
# means nothing on the internet can open a connection to the task, even knowing its IP.
# Production alternative: private subnets + VPC endpoints (no public IP at all).
resource "aws_security_group" "worker" {
  name        = local.name
  description = "Detection worker: no inbound, HTTPS out to AWS APIs only"
  vpc_id      = aws_vpc.main.id
  ingress     = []
  egress {
    description = "AWS APIs and ECR over TLS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------- image registry

resource "aws_ecr_repository" "service" {
  name = "iiot-monitor"
  # A tag (= git SHA) can never be re-pointed at a different image: what was reviewed and
  # scanned is what runs.
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # dev: `terraform destroy` may remove a repo that holds images
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "service" {
  repository = aws_ecr_repository.service.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 newest images (storage is billed per GB-month)"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------- IAM

locals {
  ecs_tasks_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy guard: only ECS acting for THIS account may assume the role.
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

# Execution role = what the ECS *agent* needs before the app starts: pull the image, create
# the log stream. Written by hand instead of AmazonECSTaskExecutionRolePolicy, which allows
# every repo and every log group in the account.
resource "aws_iam_role" "execution" {
  name                 = "${local.name}-execution"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = local.ecs_tasks_trust
}

resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        # guardrail: allow-wildcard (GetAuthorizationToken is account-wide; AWS has no resource for it)
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = aws_ecr_repository.service.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.worker.arn}:*"
      },
    ]
  })
}

# Task role = what the *application* can do. Exactly the calls in iiot_monitor/:
#   worker.poll_once      sqs:ReceiveMessage, sqs:DeleteMessage
#   store.put_* / load_latest  dynamodb:PutItem, dynamodb:Scan
#   alerts.Notifier       sns:Publish (KMS use is granted by the topic key's policy, via SNS only)
#   worker.load_registry  iot:ListThings
#   authlog.AuthLogPoller logs:FilterLogEvents
#   metrics sidecar       aps:RemoteWrite (ADR 0001; the spec change was owner-approved)
resource "aws_iam_role" "task" {
  name                 = "${local.name}-task"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = local.ecs_tasks_trust
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ConsumeTelemetry"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage"]
        Resource = var.queue_arn
      },
      {
        Sid      = "WriteState"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:Scan"]
        Resource = var.table_arns
      },
      {
        Sid      = "SendAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.alert_topic_arn
      },
      {
        Sid    = "ReadDeviceRegistry"
        Effect = "Allow"
        Action = ["iot:ListThings"]
        # guardrail: allow-wildcard (iot:ListThings has no resource type; IAM only accepts "*")
        Resource = "*"
      },
      {
        Sid      = "ReadIotAuthFailures"
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents"]
        Resource = [var.iot_log_group_arn, "${var.iot_log_group_arn}:*"]
      },
      {
        Sid      = "PushMetrics"
        Effect   = "Allow"
        Action   = ["aps:RemoteWrite"]
        Resource = var.prometheus_workspace_arn
      },
    ]
  })
}

# ---------------------------------------------------------------- ECS

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = "iiot"
  setting {
    name  = "containerInsights"
    value = "disabled" # extra CloudWatch metrics cost money; basic CPU/memory metrics are free
  }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc" # required by Fargate: the task gets its own network interface
  cpu                      = "256"    # 0.25 vCPU
  memory                   = "512"    # 0.5 GB
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # matches images built on the WSL/CI x86 machines
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.image_uri
    essential = true
    # Config only. No secrets: AWS credentials come from the task role, not env vars.
    environment = [
      { name = "AWS_REGION", value = local.region },
      { name = "QUEUE_URL", value = var.queue_url },
      { name = "TABLE_READINGS", value = var.table_names.readings },
      { name = "TABLE_LATEST", value = var.table_names.latest },
      { name = "TABLE_ALERTS", value = var.table_names.alerts },
      { name = "ALERT_TOPIC_ARN", value = var.alert_topic_arn },
    ]
    portMappings           = [{ containerPort = local.port, protocol = "tcp" }]
    readonlyRootFilesystem = true # a compromised worker can't drop tools or modify its code
    user                   = "10001"
    linuxParameters        = { initProcessEnabled = true } # forwards SIGTERM, reaps zombies
    stopTimeout            = 30                            # one 20 s long poll + finishing the batch
    # No curl in the image; Python's stdlib does the HTTP check. Non-200 -> exception -> exit 1.
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:${local.port}/health', timeout=3)"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "worker"
      }
    }
    }, {
    # Metrics sidecar. Non-essential: if it dies, the worker keeps detecting attacks (metrics
    # pause, alerts don't). Index 1, so the worker stays container_definitions[0].
    name                   = "metrics-forwarder"
    image                  = var.collector_image
    essential              = false
    environment            = [{ name = "AOT_CONFIG_CONTENT", value = local.collector_config }]
    readonlyRootFilesystem = true
    memory                 = var.collector_memory_mb # hard cap: it can never starve the worker
    dependsOn              = [{ containerName = "worker", condition = "START" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "metrics-forwarder"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = local.name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  # Stop the old task BEFORE starting the new one. Detection state (flood windows, replay
  # sequence numbers) lives in one process; two workers splitting the queue would each see
  # half the messages and miss floods/replays. A brief gap is fine: SQS buffers the backlog.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  deployment_circuit_breaker {
    enable   = true
    rollback = true # a new image whose tasks keep failing -> back to the last working one
  }

  network_configuration {
    subnets          = [for s in aws_subnet.public : s.id]
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true # instead of a NAT gateway (~$33/month)
  }
}

# ---------------------------------------------------------------- alarms -> SNS

resource "aws_cloudwatch_log_metric_filter" "processing_errors" {
  name           = "${local.name}-processing-errors"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "\"processing failed\"" # worker.poll_once: log.exception("processing failed; ...")
  metric_transformation {
    name      = "ProcessingErrors"
    namespace = "IIoT/Worker"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "processing_errors" {
  alarm_name          = "${local.name}-processing-errors"
  alarm_description   = "The worker failed to process a message (it will be retried, then dead-lettered)"
  namespace           = "IIoT/Worker"
  metric_name         = "ProcessingErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching" # no error lines logged = healthy
  alarm_actions       = [var.alert_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-dlq-not-empty"
  alarm_description   = "A message failed 5 times and was parked in the DLQ: inspect it"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alert_topic_arn]
}

# Only exists while the worker is supposed to run: with desired_count = 0 it would be in
# ALARM permanently. Without Container Insights there is no RunningTaskCount metric, so this
# uses the free per-service CPU metric: no datapoints = no running task = breaching.
resource "aws_cloudwatch_metric_alarm" "task_not_running" {
  count               = var.desired_count > 0 ? 1 : 0
  alarm_name          = "${local.name}-not-running"
  alarm_description   = "desired_count > 0 but no worker task is reporting metrics"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "SampleCount"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alert_topic_arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "iiot-secure-monitor"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Telemetry queue (backlog) and DLQ", region = local.region, stat = "Maximum", period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.queue_name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Messages in / processing errors", region = local.region, stat = "Sum", period = 60
          metrics = [
            ["AWS/SQS", "NumberOfMessagesDeleted", "QueueName", local.queue_name, { label = "processed" }],
            ["IIoT/Worker", "ProcessingErrors", { label = "processing errors" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "Worker CPU / memory (%)", region = local.region, stat = "Average", period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.worker.name],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.worker.name],
          ]
        }
      },
      {
        type = "alarm", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "Alarms"
          alarms = concat(
            [aws_cloudwatch_metric_alarm.processing_errors.arn, aws_cloudwatch_metric_alarm.dlq_not_empty.arn],
            aws_cloudwatch_metric_alarm.task_not_running[*].arn,
          )
        }
      },
    ]
  })
}
