# AWS infrastructure for the churn service. One stack per environment:
#
#   terraform init -backend-config=envs/prod.backend.hcl
#   terraform apply -var-file=envs/prod.tfvars
#
# Best practice: staging and prod live in SEPARATE AWS accounts, so a
# mistake in one can never touch the other.
terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Remote state with native S3 locking (no DynamoDB table needed since TF 1.10).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project     = "churn"
      environment = var.environment
      managed-by  = "terraform"
      cost-center = var.cost_center
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  # checkov:skip=CKV_AWS_394:We slice the first 3 AZs explicitly in network.tf
  state = "available"
}
