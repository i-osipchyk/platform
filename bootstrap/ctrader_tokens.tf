# --- One cTrader token pair per account, rotated in one place --------------------------------
# A cTrader refresh spends the old refresh token, so projects that each kept and rotated their
# own copy broke each other. The pair lives once in /platform/shared/ctrader-*, which every
# lambda-app function already reads (SHARED_SECRETS_PATH -> CTRADER_ACCESS_TOKEN etc.). This
# function is the only writer; projects never refresh. Seeding and cut-over: docs/conventions.md
# § Secrets ("cTrader tokens").

locals {
  ctrader_prefix = "/platform/shared"
  ctrader_name   = "platform-ctrader-token-refresh"
}

data "archive_file" "ctrader_tokens" {
  type        = "zip"
  source_file = "${path.module}/ctrader_tokens/handler.py"
  output_path = "${path.module}/.build/ctrader_tokens.zip"
}

resource "aws_iam_role" "ctrader_tokens" {
  count              = var.ctrader_token_refresh ? 1 : 0
  name               = local.ctrader_name
  assume_role_policy = data.aws_iam_policy_document.notifier_assume.json
}

resource "aws_cloudwatch_log_group" "ctrader_tokens" {
  count             = var.ctrader_token_refresh ? 1 : 0
  name              = "/aws/lambda/${local.ctrader_name}"
  retention_in_days = 90
}

data "aws_iam_policy_document" "ctrader_tokens" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/${local.ctrader_name}:*"]
  }
  statement {
    actions   = ["ssm:GetParameters", "ssm:PutParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account}:parameter${local.ctrader_prefix}/ctrader-*"]
  }
}

resource "aws_iam_role_policy" "ctrader_tokens" {
  count  = var.ctrader_token_refresh ? 1 : 0
  name   = local.ctrader_name
  role   = aws_iam_role.ctrader_tokens[0].id
  policy = data.aws_iam_policy_document.ctrader_tokens.json
}

resource "aws_lambda_function" "ctrader_tokens" {
  count            = var.ctrader_token_refresh ? 1 : 0
  function_name    = local.ctrader_name
  role             = aws_iam_role.ctrader_tokens[0].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.ctrader_tokens.output_path
  source_code_hash = data.archive_file.ctrader_tokens.output_base64sha256
  timeout          = 60
  memory_size      = 128
  # No reserved_concurrency = 1: it fails on accounts still at the 10-execution quota. Once a
  # day plus the odd manual {"force": true} won't overlap; don't add a second trigger.

  environment {
    variables = { SSM_PREFIX = local.ctrader_prefix }
  }

  depends_on = [aws_cloudwatch_log_group.ctrader_tokens, aws_iam_role_policy.ctrader_tokens]
}

# --- daily schedule ---

data "aws_iam_policy_document" "ctrader_tokens_scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ctrader_tokens_scheduler" {
  count              = var.ctrader_token_refresh ? 1 : 0
  name               = "${local.ctrader_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.ctrader_tokens_scheduler_assume.json
}

resource "aws_iam_role_policy" "ctrader_tokens_scheduler" {
  count = var.ctrader_token_refresh ? 1 : 0
  name  = "invoke"
  role  = aws_iam_role.ctrader_tokens_scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ctrader_tokens[0].arn
    }]
  })
}

resource "aws_scheduler_schedule" "ctrader_tokens" {
  count               = var.ctrader_token_refresh ? 1 : 0
  name                = local.ctrader_name
  schedule_expression = "cron(0 3 * * ? *)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.ctrader_tokens[0].arn
    role_arn = aws_iam_role.ctrader_tokens_scheduler[0].arn
    retry_policy {
      maximum_retry_attempts = 0 # a failed refresh needs a look, not a blind retry
    }
  }
}

# --- a failed refresh alarms to platform-alarms ---

resource "aws_cloudwatch_metric_alarm" "ctrader_tokens_errors" {
  count               = var.ctrader_token_refresh ? 1 : 0
  alarm_name          = "${local.ctrader_name}-errors"
  alarm_description   = "cTrader token refresh failed. Every project's broker access is at risk; see the function's logs."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ctrader_tokens[0].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}
