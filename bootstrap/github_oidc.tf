# --- GitHub Actions -> AWS without stored keys -----------------------------------------------
# Each project gets gh-deploy-<slug>, assumable only by jobs of its repo that run in one of its
# GitHub Environments (the reusable deploy workflow always sets `environment:`). Put a required
# reviewer on the `live` Environment in GitHub and live deploys need your click.
#
# The policy lets Terraform manage <slug>-* resources and nothing else. If a plan fails with
# AccessDenied for a service a project legitimately needs, add it here (scoped to <slug>-*).

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "deploy_trust" {
  for_each = var.projects

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for e in each.value.envs : "repo:${var.github_owner}/${each.value.repo}:environment:${e}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  for_each             = var.projects
  name                 = "gh-deploy-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust[each.key].json
  max_session_duration = 3600
  tags                 = { project = each.key }
}

data "aws_iam_policy_document" "deploy" {
  for_each = var.projects

  # Terraform state (this project's keys only; DeleteObject is for the S3-native lock file).
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/${each.key}/*"]
  }

  # Image push.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
      "ecr:DescribeImages", "ecr:DescribeRepositories", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:ListImages", "ecr:PutImage", "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.project[each.key].arn]
  }

  # Read-only calls Terraform makes that don't support resource scoping.
  statement {
    sid = "Describe"
    actions = [
      "cloudwatch:DescribeAlarms", "iam:GetPolicy", "iam:GetPolicyVersion",
      "lambda:GetAccountSettings", "lambda:ListFunctions", "logs:DescribeLogGroups",
      "scheduler:ListScheduleGroups", "scheduler:ListSchedules", "sns:ListTopics",
      "ssm:DescribeParameters", "states:ListStateMachines", "events:ListRules",
      "s3:ListAllMyBuckets",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "SharedAlarmTopic"
    actions   = ["sns:GetTopicAttributes", "sns:ListTagsForResource"]
    resources = [aws_sns_topic.alarms.arn]
  }

  # The project's own resources, by name prefix.
  statement {
    sid = "AppResources"
    actions = [
      "lambda:*", "scheduler:*", "events:*", "states:*", "sns:*",
      "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:ListTagsForResource",
      "cloudwatch:TagResource", "cloudwatch:UntagResource",
      "cloudwatch:PutDashboard", "cloudwatch:GetDashboard", "cloudwatch:DeleteDashboards",
      "logs:*",
    ]
    resources = [
      "arn:aws:lambda:${var.region}:${local.account}:function:${each.key}-*",
      "arn:aws:scheduler:${var.region}:${local.account}:schedule-group/${each.key}-*",
      "arn:aws:scheduler:${var.region}:${local.account}:schedule/${each.key}-*/*",
      "arn:aws:events:${var.region}:${local.account}:rule/${each.key}-*",
      "arn:aws:states:${var.region}:${local.account}:stateMachine:${each.key}-*",
      "arn:aws:states:${var.region}:${local.account}:execution:${each.key}-*:*",
      "arn:aws:sns:${var.region}:${local.account}:${each.key}-*",
      "arn:aws:cloudwatch:${var.region}:${local.account}:alarm:${each.key}-*",
      "arn:aws:cloudwatch::${local.account}:dashboard/${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/${each.key}-*:*",
      "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/vendedlogs/states/${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/vendedlogs/states/${each.key}-*:*",
    ]
  }

  # Buckets: configuration only. No object access, so a deploy can never read or delete data.
  statement {
    sid       = "Buckets"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${each.key}-*"]
  }

  # Roles the project's Terraform creates for its functions / scheduler / state machines.
  statement {
    sid = "Roles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole", "iam:TagRole",
      "iam:UntagRole", "iam:UpdateAssumeRolePolicy", "iam:ListRolePolicies",
      "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:ListAttachedRolePolicies", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:ListInstanceProfilesForRole", "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${local.account}:role/${each.key}-*"]
  }

  # Non-secret parameters Terraform manages (e.g. a kill switch). Secret values are set by
  # scripts/put_secrets.py, never by Terraform; note that SSM's aws/ssm key lets any principal in
  # the account decrypt, so this role *can* read them.
  statement {
    sid = "Parameters"
    actions = [
      "ssm:GetParameter", "ssm:GetParameters", "ssm:PutParameter", "ssm:DeleteParameter",
      "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource", "ssm:ListTagsForResource",
    ]
    resources = ["arn:aws:ssm:${var.region}:${local.account}:parameter/${each.key}/*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  for_each = var.projects
  name     = "deploy-${each.key}"
  role     = aws_iam_role.deploy[each.key].id
  policy   = data.aws_iam_policy_document.deploy[each.key].json
}
