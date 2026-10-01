output "cluster_name" {
  value = google_container_cluster.this.name
}

output "kubeconfig_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --region ${var.region} --project ${var.project_id}"
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.churn.repository_id}"
}

output "models_bucket" {
  value = "gs://${google_storage_bucket.this["models"].name}/registry"
}

output "data_bucket" {
  value = "gs://${google_storage_bucket.this["data"].name}"
}

output "gateway_ip" {
  description = "Put in k8s/platform/gcp/envoyproxy.yaml and your DNS A record"
  value       = google_compute_address.gateway.address
}

output "github_workload_identity_provider" {
  description = "Set as GCP_WORKLOAD_IDENTITY_PROVIDER in the GitHub environment"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_deployer_service_account" {
  description = "Set as GCP_DEPLOY_SERVICE_ACCOUNT in the GitHub environment"
  value       = google_service_account.github_deployer.email
}
