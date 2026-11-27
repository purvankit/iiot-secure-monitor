terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  # Partial config: bucket name comes from backend.hcl (keeps the account ID out of git).
  #   terraform init -backend-config=backend.hcl
  backend "s3" {
    key          = "envs/dev/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true # S3 native state locking
  }
}

provider "aws" {
  region = "ap-south-1" # pinned: the CLI profile defaults to us-east-1, which the org SCP blocks
  default_tags {
    tags = { project = "iiot-secure-monitor", env = "dev", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Ceiling for every project role. Created by infra/ci (apply that root first); the CI
  # apply role may only create/modify roles that carry exactly this boundary.
  boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/iiot-workload-boundary"
}

locals {
  # Single source of truth: the simulator's device list, plus the edge gateway.
  sim_devices = jsondecode(file("${path.root}/../../../simulator/config/devices.json")).devices
  devices = merge(
    { for d in local.sim_devices : d.device_id => { type = d.type, site = d.site } },
    { "edge-gw-01" = { type = "pump_station", site = "pune-factory-01" } },
  )
}

module "iot" {
  source                   = "../../modules/iot"
  permissions_boundary_arn = local.boundary_arn
  devices                  = local.devices
  csr_dir                  = "${path.root}/device-csrs"
}

module "ingest" {
  source                   = "../../modules/ingest"
  permissions_boundary_arn = local.boundary_arn
}

module "data" {
  source = "../../modules/data"
}

variable "alert_email" {
  type      = string
  sensitive = true
}

module "alerts" {
  source      = "../../modules/alerts"
  alert_email = var.alert_email
}

# ---------------- compute (M4): the detection service on ECS Fargate ----------------
# Demo:   terraform apply -var image_tag=<git sha> -var desired_count=1
# After:  terraform apply -var image_tag=<same sha> -var desired_count=0   (Fargate bills per hour)

variable "desired_count" {
  description = "Worker tasks to run: 0 (default, no Fargate cost) or 1 while demoing"
  type        = number
  default     = 0
  validation {
    condition     = var.desired_count == 0 || var.image_tag != "none"
    error_message = "desired_count > 0 needs -var image_tag=<sha of an image pushed to ECR>"
  }
}

variable "image_tag" {
  description = "Image tag in ECR = the git commit SHA it was built from (CI passes it in M5)"
  type        = string
  default     = "none"
  validation {
    condition     = var.image_tag != "latest"
    error_message = "use an immutable commit SHA, never 'latest'"
  }
}

# Metrics store for Grafana (ADR 0001): the worker's sidecar remote-writes here.
module "monitoring" {
  source = "../../modules/monitoring"
}

output "prometheus_query_url" {
  description = "AMP endpoint for the local Grafana datasource"
  value       = module.monitoring.query_url
}

module "compute" {
  source                   = "../../modules/compute"
  permissions_boundary_arn = local.boundary_arn
  # The ECR repo doesn't depend on image_uri, so feeding the module its own output is no cycle.
  image_uri         = "${module.compute.repository_url}:${var.image_tag}"
  desired_count     = var.desired_count
  queue_arn         = module.ingest.queue_arn
  queue_url         = module.ingest.queue_url
  dlq_name          = module.ingest.dlq_name
  alert_topic_arn   = module.alerts.topic_arn
  iot_log_group_arn = module.iot.log_group_arn
  table_names = {
    readings = module.data.readings_table
    latest   = module.data.latest_table
    alerts   = module.data.alerts_table
  }
  table_arns               = module.data.table_arns
  prometheus_workspace_arn = module.monitoring.workspace_arn
}

output "ecr_repository_url" {
  value = module.compute.repository_url
}

output "ecs_cluster" {
  value = module.compute.cluster_name
}

output "ecs_service" {
  value = module.compute.service_name
}

output "worker_log_group" {
  value = module.compute.log_group_name
}

output "worker_task_role" {
  value = module.compute.task_role_name
}

# Environment for the detection service (`eval "$(terraform output -raw service_env)"`).
output "service_env" {
  value = join("\n", [
    "export AWS_REGION=ap-south-1",
    "export QUEUE_URL=${module.ingest.queue_url}",
    "export TABLE_READINGS=${module.data.readings_table}",
    "export TABLE_LATEST=${module.data.latest_table}",
    "export TABLE_ALERTS=${module.data.alerts_table}",
    "export ALERT_TOPIC_ARN=${module.alerts.topic_arn}",
  ])
}

output "iot_endpoint" {
  value = module.iot.endpoint
}

output "device_certificate_pems" {
  value     = module.iot.certificate_pems
  sensitive = true
}

output "telemetry_queue_url" {
  value = module.ingest.queue_url
}

output "raw_bucket" {
  value = module.ingest.raw_bucket
}
