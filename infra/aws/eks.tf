module "eks" {
  # checkov:skip=CKV_TF_1:Registry modules pinned by version constraint + .terraform.lock.hcl
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.26"

  cluster_name    = local.name
  cluster_version = var.kubernetes_version

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  # Public API endpoint so GitHub-hosted runners can deploy; restrict by CIDR
  # or switch to a private endpoint + self-hosted runners for stricter setups.
  cluster_endpoint_public_access = true

  # Envelope-encrypt Kubernetes Secrets with KMS.
  create_kms_key = true
  cluster_encryption_config = {
    resources = ["secrets"]
  }

  # Control-plane audit logs -> CloudWatch (who did what to the cluster).
  cluster_enabled_log_types = ["api", "audit", "authenticator"]

  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    eks-pod-identity-agent = {}  # pod -> IAM role without static keys
    metrics-server         = {}  # needed by the HPA
    vpc-cni = {
      before_compute = true
      # Enforce Kubernetes NetworkPolicy (k8s/base/networkpolicy.yaml).
      configuration_values = jsonencode({ enableNetworkPolicy = "true" })
    }
  }

  eks_managed_node_groups = {
    general = {
      instance_types = var.node_instance_types
      capacity_type  = var.environment == "prod" ? "ON_DEMAND" : "SPOT"
      min_size       = var.node_min
      max_size       = var.node_max
      desired_size   = var.node_min
      ami_type       = "AL2023_x86_64_STANDARD"
      # IMDSv2 only, hop limit 1: pods can't steal the node's IAM role.
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }
    }
  }

  access_entries = merge(
    {
      # CI/CD may only manage its own namespace.
      github_deployer = {
        principal_arn = aws_iam_role.github_deployer.arn
        policy_associations = {
          ns_admin = {
            policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy"
            access_scope = { type = "namespace", namespaces = ["churn-${var.environment}"] }
          }
        }
      }
    },
    { for i, arn in var.cluster_admin_arns : "admin_${i}" => {
      principal_arn = arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    } }
  )
}
