output "plan_role_arn" {
  description = "GitHub secret AWS_PLAN_ROLE_ARN (repository secret)"
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "GitHub secret AWS_APPLY_ROLE_ARN (secret of the 'dev' environment only)"
  value       = aws_iam_role.apply.arn
}

output "workload_boundary_arn" {
  description = "Permissions boundary every project role must carry"
  value       = aws_iam_policy.workload_boundary.arn
}
