variable "alert_email" {
  description = "Where alert emails go. Kept out of git (terraform.tfvars is gitignored)."
  type        = string
  sensitive   = true # hide from plan output and, later, CI logs
}
