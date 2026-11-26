# Offline: mock provider (apply is simulated locally, no AWS calls). Run: terraform -chdir=infra/modules/ingest test

mock_provider "aws" {
  source = "../../tests/mocks"
}

run "queue_buffers_and_dead_letters" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition     = jsondecode(aws_sqs_queue.telemetry.redrive_policy).maxReceiveCount == 5
    error_message = "telemetry queue must send to the DLQ after 5 failed receives"
  }
  assert {
    condition     = aws_sqs_queue.telemetry.sqs_managed_sse_enabled && aws_sqs_queue.dlq.sqs_managed_sse_enabled
    error_message = "both queues must be encrypted at rest"
  }
  assert {
    condition     = aws_sqs_queue.telemetry.receive_wait_time_seconds == 20
    error_message = "long polling (20 s) keeps empty, billed receives down"
  }
  assert {
    condition     = aws_sqs_queue.telemetry.message_retention_seconds >= 345600
    error_message = "queue must hold >= 4 days so a weekend with the service at 0 loses nothing"
  }
}

run "raw_archive_is_private_and_expires" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.raw.block_public_acls,
      aws_s3_bucket_public_access_block.raw.block_public_policy,
      aws_s3_bucket_public_access_block.raw.ignore_public_acls,
      aws_s3_bucket_public_access_block.raw.restrict_public_buckets,
    ])
    error_message = "raw archive must block all public access"
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.raw.rule[0].expiration[0].days == 30
    error_message = "raw objects must expire (cost + data minimisation)"
  }
  assert {
    condition     = aws_cloudwatch_log_group.rule_errors.retention_in_days == 7
    error_message = "log retention must not be 'never expire'"
  }
}

run "rule_adds_broker_fields_detection_relies_on" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition = alltrue([for f in ["clientid() AS mqtt_client_id", "topic(2) AS topic_device_id", "timestamp() AS ingest_ts"] :
    strcontains(aws_iot_topic_rule.telemetry.sql, f)])
    error_message = "R1/R3/R4 depend on mqtt_client_id, topic_device_id and ingest_ts from the rule"
  }
  assert {
    condition     = strcontains(aws_iot_topic_rule.telemetry.sql, "FROM 'iiot/+/telemetry'")
    error_message = "rule must subscribe to every device's telemetry topic"
  }
}

run "rule_role_is_least_privilege" {
  command = apply # mock provider: simulated, never calls AWS

  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_role_policy.rule.policy).Statement : s.Action])) == toset([
      "sqs:SendMessage", "s3:PutObject", "logs:CreateLogStream", "logs:PutLogEvents",
    ])
    error_message = "IoT rule role may only send to SQS, write S3 objects and write its error log"
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.rule.policy).Statement : s.Resource != "*"])
    error_message = "no wildcard resources in the rule role"
  }
  assert {
    condition     = jsondecode(aws_iam_role.rule.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "trust policy needs aws:SourceAccount (confused-deputy protection)"
  }
}
