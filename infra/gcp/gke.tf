# GKE Autopilot: Google manages nodes, scaling, security patches and
# enforces a hardened baseline. Workload Identity, Dataplane V2
# (NetworkPolicy) and Shielded Nodes are always on.
resource "google_container_cluster" "this" {
  name     = local.name
  location = var.region # regional = control plane + nodes across 3 zones

  # Autopilot enforces these itself, so the checks are false positives:
  # checkov:skip=CKV_GCP_12:Autopilot uses Dataplane V2, which always enforces NetworkPolicy
  # checkov:skip=CKV_GCP_13:Autopilot disables client certificate auth
  # checkov:skip=CKV_GCP_61:Intranode visibility is not configurable on Autopilot; subnet flow logs are on
  # checkov:skip=CKV_GCP_69:Autopilot always runs the GKE metadata server (Workload Identity)
  # checkov:skip=CKV_GCP_65:Small team; IAM principals are managed directly. Use Google Groups for RBAC at scale.
  enable_autopilot    = true
  resource_labels = {
    project     = "churn"
    environment = var.environment
  }
  deletion_protection = var.environment == "prod"

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.nodes.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  release_channel {
    channel = var.environment == "prod" ? "STABLE" : "REGULAR"
  }

  # Upgrades only in a weekend night window (IST).
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-03T20:30:00Z"
      end_time   = "2026-01-04T00:30:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA"
    }
  }

  # Envelope-encrypt Kubernetes Secrets with a customer-managed key.
  database_encryption {
    state    = "ENCRYPTED"
    key_name = google_kms_crypto_key.gke.id
  }

  # Turns on Binary Authorization. The project policy starts as "allow all";
  # tighten it to require the cosign signatures CI attaches (docs/SECURITY.md).
  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  depends_on = [google_project_service.apis, google_kms_crypto_key_iam_member.gke]
}

resource "google_kms_key_ring" "this" {
  name       = local.name
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "gke" {
  name            = "gke-secrets"
  key_ring        = google_kms_key_ring.this.id
  rotation_period = "7776000s" # 90 days
  lifecycle {
    prevent_destroy = true # destroying the key makes every encrypted Secret unreadable
  }
}

resource "google_kms_crypto_key_iam_member" "gke" {
  crypto_key_id = google_kms_crypto_key.gke.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@container-engine-robot.iam.gserviceaccount.com"
}
