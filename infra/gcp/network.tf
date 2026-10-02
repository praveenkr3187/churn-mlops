locals {
  name = "churn-${var.environment}"
}

resource "google_compute_network" "vpc" {
  # checkov:skip=CKV2_GCP_18:Custom-mode VPC has no default firewall rules; GKE manages its own
  name                    = local.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "nodes" {
  name                     = "${local.name}-nodes"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true # reach GCS / Artifact Registry without public IPs

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.60.0.0/14"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.64.0.0/20"
  }

  log_config {
    aggregation_interval = "INTERVAL_5_MIN"
    flow_sampling        = 0.5
  }
}

# Private nodes need NAT to pull public images (Envoy, Prometheus, ...).
resource "google_compute_router" "nat" {
  name    = "${local.name}-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "${local.name}-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Static IP for the public Gateway (k8s/platform/gcp/envoyproxy.yaml).
resource "google_compute_address" "gateway" {
  name   = "${local.name}-gateway"
  region = var.region
}
