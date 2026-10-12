# Versioned, encrypted, private bucket that Terraform refuses to destroy. To really delete one,
# empty it and remove the resource by hand: data loss should take deliberate effort.

data "aws_caller_identity" "current" {}

locals {
  name = coalesce(var.name, "${var.project}-${var.env}-${var.purpose}-${data.aws_caller_identity.current.account_id}")
}

resource "aws_s3_bucket" "this" {
  bucket = local.name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  count  = var.noncurrent_version_days == null ? 0 : 1
  bucket = aws_s3_bucket.this.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
  }
}
