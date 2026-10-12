output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}

output "function_arns" {
  value = { for k, f in aws_lambda_function.fn : k => f.arn }
}

output "role_name" {
  description = "Attach extra policies here for project-specific needs."
  value       = aws_iam_role.fn.name
}

output "role_arn" {
  value = aws_iam_role.fn.arn
}

output "alarm_topic_arn" {
  value = local.alarm_topic
}
