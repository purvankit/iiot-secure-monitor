variable "devices" {
  description = "Map of device_id (= Thing name) to its type and site"
  type        = map(object({ type = string, site = string }))
}

variable "csr_dir" {
  description = "Directory holding <device_id>.csr files (public; keys stay on devices)"
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 7
}
