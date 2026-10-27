# --- One alarm topic for every project -> Telegram (and optionally email) ---------------------
# Projects' CloudWatch alarms publish here (the lambda-app module looks it up by name). The
# notifier reads its bot token / chat id from SSM /platform/notifier/*, put there by hand:
#   aws ssm put-parameter --type SecureString --name /platform/notifier/telegram-bot-token --value ...
#   aws ssm put-parameter --type SecureString --name /platform/notifier/telegram-chat-id   --value ...

resource "aws_sns_topic" "alarms" {
  name = "platform-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email_subscription && var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

data "archive_file" "notifier" {
  type        = "zip"
  source_file = "${path.module}/notifier/handler.py"
  output_path = "${path.module}/.build/notifier.zip"
}

data "aws_iam_policy_document" "notifier_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "notifier" {
  count              = var.telegram_notifier ? 1 : 0
  name               = "platform-notifier"
  assume_role_policy = data.aws_iam_policy_document.notifier_assume.json
}

resource "aws_cloudwatch_log_group" "notifier" {
  count             = var.telegram_notifier ? 1 : 0
  name              = "/aws/lambda/platform-notifier"
  retention_in_days = 90
}

data "aws_iam_policy_document" "notifier" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/platform-notifier:*"]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account}:parameter/platform/notifier/*"]
  }
  # Read-only lookups that put the cause of a failure into the alert (notifier/handler.py).
  statement {
    actions   = ["logs:FilterLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/*"]
  }
  statement {
    actions   = ["states:ListExecutions"]
    resources = ["arn:aws:states:${var.region}:${local.account}:stateMachine:*"]
  }
  statement {
    actions   = ["states:GetExecutionHistory"]
    resources = ["arn:aws:states:${var.region}:${local.account}:execution:*:*"]
  }
}

resource "aws_iam_role_policy" "notifier" {
  count  = var.telegram_notifier ? 1 : 0
  name   = "platform-notifier"
  role   = aws_iam_role.notifier[0].id
  policy = data.aws_iam_policy_document.notifier.json
}

resource "aws_lambda_function" "notifier" {
  count            = var.telegram_notifier ? 1 : 0
  function_name    = "platform-notifier"
  role             = aws_iam_role.notifier[0].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.notifier.output_path
  source_code_hash = data.archive_file.notifier.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = { SSM_PREFIX = "/platform/notifier" }
  }

  depends_on = [aws_cloudwatch_log_group.notifier, aws_iam_role_policy.notifier]
}

resource "aws_sns_topic_subscription" "notifier" {
  count     = var.telegram_notifier ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notifier[0].arn
}

resource "aws_lambda_permission" "notifier" {
  count         = var.telegram_notifier ? 1 : 0
  statement_id  = "AllowAlarmTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier[0].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarms.arn
}
