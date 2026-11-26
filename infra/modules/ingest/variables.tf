variable "raw_retention_days" {
  type    = number
  default = 30
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "permissions_boundary_arn" {
  description = "Boundary (ceiling) for this module's IAM roles; created in infra/ci. null = none (tests)"
  type        = string
  default     = null
}
