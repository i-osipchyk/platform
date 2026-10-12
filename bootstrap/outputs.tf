output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "deploy_role_arns" {
  description = "Set as AWS_DEPLOY_ROLE_ARN on each repo (Settings > Secrets and variables > Actions > Variables)."
  value       = { for k, r in aws_iam_role.deploy : k => r.arn }
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.project : k => r.repository_url }
}
