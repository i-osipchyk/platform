data "aws_caller_identity" "current" {}

locals {
  account      = data.aws_caller_identity.current.account_id
  state_bucket = "tfstate-${local.account}-${var.region}"
}

# --- Terraform state: one bucket per account, key <slug>/<env>.tfstate ----------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 20
    }
  }
}

# --- ECR: one repository per project, shared by its envs (demo and live run the same digest) --

resource "aws_ecr_repository" "project" {
  for_each             = var.projects
  name                 = each.key
  image_tag_mutability = "IMMUTABLE" # tags are git SHAs; a tag always means one image
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { project = each.key }
}

resource "aws_ecr_lifecycle_policy" "project" {
  for_each   = var.projects
  repository = aws_ecr_repository.project[each.key].name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

# Lets the project's functions pull, so deploy roles never need ecr:SetRepositoryPolicy.
resource "aws_ecr_repository_policy" "project" {
  for_each   = var.projects
  repository = aws_ecr_repository.project[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "LambdaPull"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
      Condition = {
        StringLike = {
          "aws:sourceArn" = "arn:aws:lambda:${var.region}:${local.account}:function:${each.key}-*"
        }
      }
    }]
  })
}

# --- Cost attribution ------------------------------------------------------------------------

resource "aws_ce_cost_allocation_tag" "tag" {
  for_each = var.activate_cost_allocation_tags ? toset(["project", "env"]) : toset([])
  tag_key  = each.key
  status   = "Active"
}

resource "aws_budgets_budget" "project" {
  for_each     = { for k, v in var.projects : k => v if v.budget_usd > 0 }
  name         = "project-${each.key}"
  budget_type  = "COST"
  limit_amount = tostring(each.value.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$${each.key}"]
  }

  dynamic "notification" {
    for_each = var.alert_email == "" ? [] : [80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 100 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
