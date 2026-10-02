variable "project_id" {
  type = string
}

variable "environment" {
  description = "staging | prod"
  type        = string
  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod"
  }
}

variable "region" {
  type    = string
  default = "asia-south1" # Mumbai
}

variable "name_prefix" {
  description = "Globally-unique prefix for bucket names"
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to deploy via Workload Identity Federation"
  type        = string
}

variable "subnet_cidr" {
  type    = string
  default = "10.50.0.0/20"
}

variable "master_authorized_cidrs" {
  description = "CIDRs allowed to reach the GKE control plane"
  type        = list(object({ cidr_block = string, display_name = string }))
  default     = [{ cidr_block = "0.0.0.0/0", display_name = "anywhere (tighten me)" }]
}
