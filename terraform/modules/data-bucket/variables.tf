variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "purpose" {
  description = "What the bucket holds, e.g. data | records | artifacts."
  type        = string
  default     = "data"
}

variable "name" {
  description = "Override the generated <project>-<env>-<purpose>-<account> name (adopting an existing bucket). Must still start with <project>- for the deploy role to manage it."
  type        = string
  default     = null
}

variable "noncurrent_version_days" {
  description = "Expire overwritten/deleted object versions after N days (null = keep forever)."
  type        = number
  default     = null
}
