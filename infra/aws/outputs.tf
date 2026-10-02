output "cluster_name" {
  value = module.eks.cluster_name
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "models_bucket" {
  value = "s3://${aws_s3_bucket.this["models"].bucket}/registry"
}

output "data_bucket" {
  value = "s3://${aws_s3_bucket.this["data"].bucket}"
}

output "github_deployer_role_arn" {
  description = "Set as AWS_DEPLOY_ROLE_ARN in the GitHub environment"
  value       = aws_iam_role.github_deployer.arn
}
