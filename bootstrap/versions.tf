terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # First apply runs on local state (the bucket doesn't exist yet). Then copy
  # backend.tf.example to backend.tf and `terraform init -migrate-state`. See README.md.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "platform"
      managed_by = "terraform"
    }
  }
}
