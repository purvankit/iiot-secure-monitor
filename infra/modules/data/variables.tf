variable "alerts_pitr" {
  description = "Point-in-time recovery for the alerts table (billed per GB; tiny here)"
  type        = bool
  default     = true
}
