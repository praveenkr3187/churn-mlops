# Object storage + container registry.

resource "aws_kms_key" "data" {
  description             = "churn ${var.environment} data + models"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  # Explicit key policy: account admins manage the key; usage is granted
  # through IAM policies (iam.tf), so the key policy delegates to IAM.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AccountAdmin"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })
}

locals {
  buckets = {
    models = "${var.name_prefix}-churn-${var.environment}-models" # registry: versions/, aliases/
    data   = "${var.name_prefix}-churn-${var.environment}-data"   # training data, batch scores
  }
}

resource "aws_s3_bucket" "this" {
  # checkov:skip=CKV_AWS_18:Object-level access is audited with CloudTrail S3 data events (org trail)
  # checkov:skip=CKV_AWS_144:Cross-region replication not required; models are reproducible from data + code
  # checkov:skip=CKV2_AWS_62:No event-driven consumers of these buckets
  for_each = local.buckets
  bucket   = each.value
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" } # recover from accidental overwrite/delete
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

# Deny any non-TLS access.
resource "aws_s3_bucket_policy" "tls_only" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [each.value.arn, "${each.value.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------- ECR ------
resource "aws_ecr_repository" "this" {
  for_each             = toset(["churn-api", "churn-train"])
  name                 = each.value
  image_tag_mutability = "IMMUTABLE" # a tag always means the same image
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 100 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 100 }
      action       = { type = "expire" }
    }]
  })
}

# ------------------------------------------------------- Secrets Manager ---
# Terraform creates the secret CONTAINER only. The value is set out-of-band
# (docs/DEPLOY.md step 4) so it never lands in Terraform state.
resource "aws_secretsmanager_secret" "api" {
  # checkov:skip=CKV2_AWS_57:API keys are rotated by the documented runbook (docs/OPERATIONS.md#rotate-api-keys)
  name       = "churn-api"
  kms_key_id = aws_kms_key.data.arn
}
