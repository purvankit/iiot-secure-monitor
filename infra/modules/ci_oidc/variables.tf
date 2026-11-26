variable "github_repo" {
  description = <<-EOT
    The only repository whose workflows may assume these roles, exactly as it appears in the
    OIDC `sub` claim: "owner/name", or with GitHub's immutable subject format (repo setting
    use_immutable_subject) "owner@ownerId/name@repoId". Check with:
      gh api repos/OWNER/NAME/actions/oidc/customization/sub
  EOT
  type        = string
  validation {
    condition     = can(regex("^[\\w.-]+(@\\d+)?/[\\w.-]+(@\\d+)?$", var.github_repo))
    error_message = "exactly one repo as owner/name or owner@id/name@id, no wildcards"
  }
}

variable "state_bucket_arn" {
  description = "Terraform state bucket (the plan role may only write the dev lock file in it)"
  type        = string
}

variable "environment" {
  description = "GitHub environment (with required reviewers) that gates the apply role"
  type        = string
  default     = "dev"
}

variable "state_key" {
  description = "State object of the environment CI deploys (matches envs/dev backend key)"
  type        = string
  default     = "envs/dev/terraform.tfstate"
}

variable "resource_prefix" {
  description = "Project resources are named <prefix>* (roles, queues, tables, log groups...)"
  type        = string
  default     = "iiot"
}
