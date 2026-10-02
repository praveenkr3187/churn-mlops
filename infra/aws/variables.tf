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
  default = "ap-south-1" # Mumbai
}

variable "name_prefix" {
  description = "Globally-unique prefix for bucket names (e.g. your company)"
  type        = string
}

variable "cost_center" {
  type    = string
  default = "ml-platform"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m7i.large", "m6i.large"] # two types = better Spot/capacity odds
}

variable "node_min" {
  type    = number
  default = 3
}

variable "node_max" {
  type    = number
  default = 10
}

variable "github_repository" {
  description = "owner/repo allowed to deploy via GitHub OIDC"
  type        = string
}

variable "cluster_admin_arns" {
  description = "IAM principals (humans / SSO roles) with cluster-admin"
  type        = list(string)
  default     = []
}
