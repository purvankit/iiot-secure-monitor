# CI access (M5): GitHub OIDC provider, plan/apply roles, workload permissions boundary.
#
# Applied by the OWNER from a laptop, never by CI: the pipeline must not manage its own
# permissions. Same state bucket as envs/dev, separate key.
#   terraform -chdir=infra/ci init -backend-config=../envs/dev/backend.hcl
#   terraform -chdir=infra/ci apply
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    key          = "ci/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "ap-south-1" # pinned: the CLI profile defaults to us-east-1, which the org SCP blocks
  default_tags {
    tags = { project = "iiot-secure-monitor", env = "ci", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

variable "github_repo" {
  type    = string
  default = "purvankit/iiot-secure-monitor"
}

module "ci_oidc" {
  source           = "../modules/ci_oidc"
  github_repo      = var.github_repo
  state_bucket_arn = "arn:aws:s3:::iiot-secure-monitor-tfstate-${data.aws_caller_identity.current.account_id}"
}

output "plan_role_arn" {
  value = module.ci_oidc.plan_role_arn
}

output "apply_role_arn" {
  value = module.ci_oidc.apply_role_arn
}

output "workload_boundary_arn" {
  value = module.ci_oidc.workload_boundary_arn
}
