# Workload Identity Federation for GKE: grant IAM roles DIRECTLY to a
# Kubernetes service account (no Google service account, no keys).
locals {
  ns        = "churn-${var.environment}"
  wi_prefix = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject"
  ksa       = { for k, v in {
    api              = "ns/${local.ns}/sa/churn-api"
    trainer          = "ns/${local.ns}/sa/churn-trainer"
    batch            = "ns/${local.ns}/sa/churn-batch"
    external_secrets = "ns/external-secrets/sa/external-secrets"
    otel             = "ns/observability/sa/otel-collector"
  } : k => "${local.wi_prefix}/${v}" }

  bucket_grants = [
    # API: read models only
    { bucket = "models", role = "roles/storage.objectViewer", who = "api" },
    # Trainer: read data, write models. (For alias-level restriction as on AWS,
    # add an IAM Condition on resource.name prefix.)
    { bucket = "data", role = "roles/storage.objectViewer", who = "trainer" },
    { bucket = "models", role = "roles/storage.objectUser", who = "trainer" },
    # Batch: read data + models, write scores
    { bucket = "models", role = "roles/storage.objectViewer", who = "batch" },
    { bucket = "data", role = "roles/storage.objectUser", who = "batch" },
  ]
}

resource "google_storage_bucket_iam_member" "workloads" {
  for_each = { for g in local.bucket_grants : "${g.who}-${g.bucket}-${g.role}" => g }
  bucket   = google_storage_bucket.this[each.value.bucket].name
  role     = each.value.role
  member   = local.ksa[each.value.who]
}

resource "google_secret_manager_secret_iam_member" "eso" {
  secret_id = google_secret_manager_secret.api.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.ksa["external_secrets"]
}

resource "google_project_iam_member" "otel_trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = local.ksa["otel"]
}

# Nodes pull images from Artifact Registry.
resource "google_artifact_registry_repository_iam_member" "nodes_pull" {
  repository = google_artifact_registry_repository.churn.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# ------------------------------------------- GitHub Actions (keyless) ------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  # checkov:skip=CKV_GCP_125:attribute_condition below restricts to one repository AND one environment
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  attribute_mapping = {
    "google.subject"         = "assertion.sub"
    "attribute.repository"   = "assertion.repository"
    "attribute.environment"  = "assertion.environment"
  }
  # Only this repo, only jobs bound to the matching GitHub environment.
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.environment in ['${var.environment}', '${var.environment}-review']"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_deployer" {
  account_id   = "churn-deployer-${var.environment}"
  display_name = "GitHub Actions deployer (${var.environment})"
}

resource "google_service_account_iam_member" "github_impersonation" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  repository = google_artifact_registry_repository.churn.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.github_deployer.member
}

# Deploy into the cluster (RBAC can narrow this further to one namespace).
resource "google_project_iam_member" "deployer_gke" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = google_service_account.github_deployer.member
}

# Read the registry and move the production alias after approval.
resource "google_storage_bucket_iam_member" "deployer_models" {
  bucket = google_storage_bucket.this["models"].name
  role   = "roles/storage.objectUser"
  member = google_service_account.github_deployer.member
}
