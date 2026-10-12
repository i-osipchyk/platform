variable "region" {
  description = "Home region for state, ECR and the alarm topic. Fed from AWS_REGION via TF_VAR_region (see docs/deployment.md)."
  type        = string
  default     = "eu-west-1"
}

variable "github_owner" {
  description = "GitHub user/org that owns the project repos."
  type        = string
  default     = "i-osipchyk"
}

variable "projects" {
  description = <<-EOT
    One entry per deployable project, keyed by its slug (the prefix of every AWS name, the
    `project` tag, the SSM path and the llmkit project). Each gets: a GitHub OIDC deploy role
    gh-deploy-<slug> trusted from the listed GitHub Environments of `repo`, an ECR repository
    named <slug>, and (when budget_usd > 0) a monthly AWS budget filtered on project=<slug>.
  EOT
  type = map(object({
    repo       = string
    envs       = optional(list(string), ["demo", "live"])
    budget_usd = optional(number, 10)
  }))

  validation {
    condition     = alltrue([for k in keys(var.projects) : can(regex("^[a-z][a-z0-9]{1,15}$", k))])
    error_message = "Project slugs are 2-16 chars, lowercase letters and digits, starting with a letter (no dashes: they separate <slug>-<env>-<name>)."
  }
}

variable "alert_email" {
  description = "Receives budget alerts, and alarms too if alarm_email_subscription is set (empty = none)."
  type        = string
  default     = ""
}

variable "alarm_email_subscription" {
  description = "Also subscribe alert_email to the shared alarm topic (needs a click to confirm)."
  type        = bool
  default     = false
}

variable "telegram_notifier" {
  description = "Deploy the Telegram notifier on the alarm topic. Needs /platform/notifier/telegram-bot-token and /telegram-chat-id in SSM first."
  type        = bool
  default     = true
}

variable "activate_cost_allocation_tags" {
  description = "Activate project/env as cost allocation tags. Fails until AWS has seen the tag on some resource; set false for the first apply on a fresh account."
  type        = bool
  default     = true
}
