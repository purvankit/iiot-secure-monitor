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

locals {
  # Single source of truth: the simulator's device list, plus the edge gateway.
  sim_devices = jsondecode(file("${path.root}/../../../simulator/config/devices.json")).devices
  devices = merge(
    { for d in local.sim_devices : d.device_id => { type = d.type, site = d.site } },
    { "edge-gw-01" = { type = "pump_station", site = "pune-factory-01" } },
  )
}

module "iot" {
  source  = "../../modules/iot"
  devices = local.devices
  csr_dir = "${path.root}/device-csrs"
}

module "ingest" {
  source = "../../modules/ingest"
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
