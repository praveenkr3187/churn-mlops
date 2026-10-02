# Workload identity with EKS Pod Identity: each Kubernetes service account is
# mapped to its own IAM role. No access keys anywhere.
locals {
  ns             = "churn-${var.environment}"
  models_arn     = aws_s3_bucket.this["models"].arn
  data_arn       = aws_s3_bucket.this["data"].arn
  pod_trust_json = data.aws_iam_policy_document.pod_trust.json
}

data "aws_iam_policy_document" "pod_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

# Role name => (namespace, service account, policy statements)
locals {
  workloads = {
    api = {
      namespace = local.ns, sa = "churn-api"
      statements = [
        { actions = ["s3:GetObject"], resources = ["${local.models_arn}/*"] },
        { actions = ["s3:ListBucket"], resources = [local.models_arn] },
      ]
    }
    trainer = {
      namespace = local.ns, sa = "churn-trainer"
      statements = [
        { actions = ["s3:GetObject"], resources = ["${local.data_arn}/*", "${local.models_arn}/*"] },
        { actions = ["s3:ListBucket"], resources = [local.data_arn, local.models_arn] },
        # May add versions and move the STAGING alias only — never production.
        { actions = ["s3:PutObject"], resources = [
          "${local.models_arn}/registry/versions/*",
          "${local.models_arn}/registry/aliases/staging.json",
          "${local.models_arn}/registry/history.jsonl",
        ] },
      ]
    }
    batch = {
      namespace = local.ns, sa = "churn-batch"
      statements = [
        { actions = ["s3:GetObject"], resources = ["${local.data_arn}/*", "${local.models_arn}/*"] },
        { actions = ["s3:ListBucket"], resources = [local.data_arn, local.models_arn] },
        { actions = ["s3:PutObject"], resources = ["${local.data_arn}/scores/*"] },
      ]
    }
    external-secrets = {
      namespace = "external-secrets", sa = "external-secrets"
      statements = [
        { actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
        resources = [aws_secretsmanager_secret.api.arn] },
      ]
    }
    otel-collector = {
      namespace = "observability", sa = "otel-collector"
      statements = [
        { actions = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], resources = ["*"] },
      ]
    }
  }
}

data "aws_iam_policy_document" "workload" {
  for_each = local.workloads
  dynamic "statement" {
    for_each = each.value.statements
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
  # All workloads touching the buckets need the KMS key.
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role" "workload" {
  for_each           = local.workloads
  name               = "${local.name}-${each.key}"
  assume_role_policy = local.pod_trust_json
}

resource "aws_iam_role_policy" "workload" {
  for_each = local.workloads
  role     = aws_iam_role.workload[each.key].id
  policy   = data.aws_iam_policy_document.workload[each.key].json
}

resource "aws_eks_pod_identity_association" "workload" {
  for_each        = local.workloads
  cluster_name    = module.eks.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.sa
  role_arn        = aws_iam_role.workload[each.key].arn
}

# The AWS Load Balancer Controller needs a large AWS-maintained policy;
# the community module packages it.
module "lbc_pod_identity" {
  # checkov:skip=CKV_TF_1:Registry modules pinned by version constraint + .terraform.lock.hcl
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "~> 1.12"

  name                            = "${local.name}-aws-lbc"
  attach_aws_lb_controller_policy = true
  associations = {
    this = {
      cluster_name    = module.eks.cluster_name
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
    }
  }
}

# ------------------------------------------------ GitHub Actions (OIDC) ----
# CI gets short-lived credentials per run; no AWS keys stored in GitHub.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only jobs running in this repo's matching GitHub *environment* (which can
    # require reviewers) may assume the role. `<env>-review` is the no-approval
    # environment the model-release workflow uses to show the model card.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:environment:${var.environment}",
        "repo:${var.github_repository}:environment:${var.environment}-review",
      ]
    }
  }
}

resource "aws_iam_role" "github_deployer" {
  name               = "${local.name}-github-deployer"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "github_deployer" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart", "ecr:DescribeImages"]
    resources = [for r in aws_ecr_repository.this : r.arn]
  }
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [module.eks.cluster_arn]
  }
  # Read the registry; move the production alias after human approval.
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [local.models_arn, "${local.models_arn}/*"]
  }
  statement {
    actions = ["s3:PutObject"]
    resources = ["${local.models_arn}/registry/aliases/*",
    "${local.models_arn}/registry/history.jsonl"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "github_deployer" {
  role   = aws_iam_role.github_deployer.id
  policy = data.aws_iam_policy_document.github_deployer.json
}
