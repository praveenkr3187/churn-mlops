locals {
  buckets = {
    models = "${var.name_prefix}-churn-${var.environment}-models"
    data   = "${var.name_prefix}-churn-${var.environment}-data"
  }
}

resource "google_storage_bucket" "this" {
  # checkov:skip=CKV_GCP_62:Access is audited with Cloud Audit Logs (Data Access), enabled at org level
  for_each = local.buckets
  name     = each.value
  location = var.region

  uniform_bucket_level_access = true       # IAM only, no per-object ACLs
  public_access_prevention    = "enforced" # can never be made public
  force_destroy               = false

  versioning { enabled = true }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
      with_state         = "ARCHIVED"
    }
    action { type = "Delete" }
  }
}

resource "google_artifact_registry_repository" "churn" {
  # checkov:skip=CKV_GCP_84:Google-managed encryption is sufficient for container images
  repository_id = "churn"
  location      = var.region
  format        = "DOCKER"

  docker_config { immutable_tags = true }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions { keep_count = 100 }
  }

  depends_on = [google_project_service.apis]
}

# Container only; the value is added out-of-band (docs/DEPLOY.md step 4).
resource "google_secret_manager_secret" "api" {
  secret_id = "churn-api"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
