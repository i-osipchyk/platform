variable "project" {
  description = "Project slug (prefix of every name; matches the bootstrap `projects` key)."
  type        = string
}

variable "env" {
  description = "Environment, e.g. demo | live."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.env))
    error_message = "env is 2-10 lowercase letters/digits."
  }
}

variable "image_uri" {
  description = "Container image by digest: <account>.dkr.ecr.<region>.amazonaws.com/<project>@sha256:..."
  type        = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "Pin the image by digest (repo@sha256:...), not by tag."
  }
}

variable "functions" {
  description = <<-EOT
    One Lambda per entry, all from the same image. Key = short function name; the function is
    <project>-<env>-<key>. retries defaults to 0 because a retried trading job can double-act:
    only raise it for idempotent handlers.
  EOT
  type = map(object({
    handler              = string # python dotted path, e.g. "gtb.runs.app.morning_handler"
    timeout              = optional(number, 60)
    memory               = optional(number, 512)
    reserved_concurrency = optional(number, -1) # 1 = never overlap; -1 = unreserved
    retries              = optional(number, 0)  # async retries (0-2)
    max_event_age        = optional(number, 3600)
    alarm                = optional(bool, true) # Errors >= 1 -> platform-alarms
    environment          = optional(map(string), {})
  }))
}

variable "schedules" {
  description = <<-EOT
    EventBridge Scheduler entries. Key = schedule name. expression: cron(...), rate(...) or
    at(yyyy-mm-ddThh:mm:ss) for one-offs. Several schedules may target one function.
  EOT
  type = map(object({
    function   = string
    expression = string
    timezone   = optional(string, "UTC")
    input      = optional(string) # JSON payload
    enabled    = optional(bool, true)
  }))
  default = {}
}

variable "schedules_enabled" {
  description = "Master switch: false disables every schedule (deploy without anything firing)."
  type        = bool
  default     = true
}

variable "environment" {
  description = "Env vars for every function (merged under each function's own)."
  type        = map(string)
  default     = {}
}

variable "read_bucket_arns" {
  description = "Buckets the functions may list and read."
  type        = list(string)
  default     = []
}

variable "write_bucket_arns" {
  description = "Buckets the functions may list, read and put to (never delete)."
  type        = list(string)
  default     = []
}

variable "ssm_write" {
  description = "Allow PutParameter under /<project>/<env>/ (e.g. rotating OAuth tokens)."
  type        = bool
  default     = false
}

variable "extra_policy_json" {
  description = "Additional IAM policy for the function role (Step Functions, SQS, ...)."
  type        = string
  default     = null
}

variable "alarm_topic_arn" {
  description = "Where alarms go. null = the shared platform-alarms topic from bootstrap."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "architecture" {
  type    = string
  default = "arm64"
}
