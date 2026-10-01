# GCP infrastructure for the churn service. One stack per environment,
# ideally one GCP project per environment:
#
#   terraform init -backend-config=envs/prod.backend.hcl
#   terraform apply -var-file=envs/prod.tfvars
terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.4"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  default_labels = {
    project     = "churn"
    environment = var.environment
    managed-by  = "terraform"
  }
}

data "google_project" "this" {}

resource "google_project_service" "apis" {
  for_each = toset([
    "container.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudtrace.googleapis.com",
    "cloudkms.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}
