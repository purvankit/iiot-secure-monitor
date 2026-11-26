variable "image_uri" {
  description = "Full ECR image URI incl. tag (the git commit SHA). The task runs exactly this."
  type        = string
}

variable "desired_count" {
  description = "Running worker tasks. Fargate bills per task-hour: 0 unless demoing. Max 1 (see main.tf)."
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.desired_count)
    error_message = "0 or 1: detection state (flood windows, replay seqs) lives in one process."
  }
}

variable "queue_arn" { type = string }
variable "queue_url" { type = string }

variable "dlq_name" {
  description = "Dead-letter queue name (CloudWatch SQS metrics are keyed by name, not ARN)"
  type        = string
}

variable "alert_topic_arn" { type = string }

variable "iot_log_group_arn" {
  description = "AWSIotLogsV2 log group, polled for auth failures (rule R1)"
  type        = string
}

variable "table_names" {
  type = object({ readings = string, latest = string, alerts = string })
}

variable "table_arns" { type = list(string) }

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "permissions_boundary_arn" {
  description = "Boundary (ceiling) for this module's IAM roles; created in infra/ci. null = none (tests)"
  type        = string
  default     = null
}
