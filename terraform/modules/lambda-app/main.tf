data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_sns_topic" "platform" {
  count = var.alarm_topic_arn == null ? 1 : 0
  name  = "platform-alarms"
}

locals {
  name        = "${var.project}-${var.env}"
  account     = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.region
  alarm_topic = var.alarm_topic_arn != null ? var.alarm_topic_arn : data.aws_sns_topic.platform[0].arn
  ssm_arn     = "arn:aws:ssm:${local.region}:${local.account}:parameter"

  base_environment = {
    PROJECT             = var.project
    ENV                 = var.env
    LLMKIT_PROJECT      = var.project
    LLMKIT_ENV          = var.env
    SECRETS_PATH        = "/${var.project}/${var.env}"
    SHARED_SECRETS_PATH = "/platform/shared"
  }
  scheduled_functions = toset([for s in values(var.schedules) : s.function])
}

# --- role (shared by the app's functions) ---------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "fn" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for g in aws_cloudwatch_log_group.fn : "${g.arn}:*"]
  }

  # Secrets: /<project>/<env>/* for the app, /platform/shared/* for keys every project uses
  # (Langfuse). Values are SecureStrings under the aws/ssm key, set by scripts, never Terraform.
  statement {
    sid     = "SecretsRead"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "${local.ssm_arn}/${var.project}/${var.env}",
      "${local.ssm_arn}/${var.project}/${var.env}/*",
      "${local.ssm_arn}/platform/shared",
      "${local.ssm_arn}/platform/shared/*",
    ]
  }

  dynamic "statement" {
    for_each = var.ssm_write ? [1] : []
    content {
      sid       = "SecretsWrite"
      actions   = ["ssm:PutParameter"]
      resources = ["${local.ssm_arn}/${var.project}/${var.env}/*"]
    }
  }

  dynamic "statement" {
    for_each = length(var.read_bucket_arns) + length(var.write_bucket_arns) > 0 ? [1] : []
    content {
      # Without ListBucket a missing key is AccessDenied instead of NoSuchKey.
      sid       = "BucketList"
      actions   = ["s3:ListBucket"]
      resources = distinct(concat(var.read_bucket_arns, var.write_bucket_arns))
    }
  }

  dynamic "statement" {
    for_each = length(var.read_bucket_arns) > 0 ? [1] : []
    content {
      sid       = "BucketRead"
      actions   = ["s3:GetObject"]
      resources = [for b in var.read_bucket_arns : "${b}/*"]
    }
  }

  dynamic "statement" {
    for_each = length(var.write_bucket_arns) > 0 ? [1] : []
    content {
      # Append-only by design: no DeleteObject. Versioned buckets keep every overwrite anyway.
      sid       = "BucketWrite"
      actions   = ["s3:GetObject", "s3:PutObject"]
      resources = [for b in var.write_bucket_arns : "${b}/*"]
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  name   = "${local.name}-lambda"
  role   = aws_iam_role.fn.id
  policy = data.aws_iam_policy_document.fn.json
}

resource "aws_iam_role_policy" "extra" {
  count  = var.extra_policy_json == null ? 0 : 1
  name   = "${local.name}-extra"
  role   = aws_iam_role.fn.id
  policy = var.extra_policy_json
}

# --- functions ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = var.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "fn" {
  for_each      = var.functions
  function_name = "${local.name}-${each.key}"
  role          = aws_iam_role.fn.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = [var.architecture]
  memory_size   = each.value.memory
  timeout       = each.value.timeout

  reserved_concurrent_executions = each.value.reserved_concurrency

  image_config {
    command = [each.value.handler]
  }

  environment {
    variables = merge(local.base_environment, var.environment, each.value.environment, {
      FUNCTION = each.key
    })
  }

  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

resource "aws_lambda_function_event_invoke_config" "fn" {
  for_each                     = var.functions
  function_name                = aws_lambda_function.fn[each.key].function_name
  maximum_retry_attempts       = each.value.retries
  maximum_event_age_in_seconds = each.value.max_event_age
}

# --- schedules (EventBridge Scheduler: timezone-aware, one-offs via at()) -------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = length(var.schedules) > 0 ? 1 : 0
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  count = length(var.schedules) > 0 ? 1 : 0
  name  = "${local.name}-invoke"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [for f in local.scheduled_functions : aws_lambda_function.fn[f].arn]
    }]
  })
}

resource "aws_scheduler_schedule_group" "app" {
  count = length(var.schedules) > 0 ? 1 : 0
  name  = local.name
}

resource "aws_scheduler_schedule" "app" {
  for_each                     = var.schedules
  name                         = each.key
  group_name                   = aws_scheduler_schedule_group.app[0].name
  schedule_expression          = each.value.expression
  schedule_expression_timezone = each.value.timezone
  state                        = var.schedules_enabled && each.value.enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn[each.value.function].arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = each.value.input

    # Retries of the *invoke* only (throttling etc.); the function's own retries are `retries`.
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
  }
}

# --- alarms -> platform-alarms -> Telegram --------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = { for k, f in var.functions : k => f if f.alarm }
  alarm_name          = "${local.name}-${each.key}-errors"
  alarm_description   = "${var.project} (${var.env}): ${each.key} failed (error or timeout)"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alarm_topic]
  ok_actions          = [local.alarm_topic]
}
