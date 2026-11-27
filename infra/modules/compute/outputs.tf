output "repository_url" {
  value = aws_ecr_repository.service.repository_url
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_name" {
  value = aws_ecs_service.worker.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.worker.name
}

output "task_role_name" {
  value = aws_iam_role.task.name
}

output "local_worker_role_arn" {
  description = "Role the kind pod's credentials come from (null when not requested)"
  value       = one(aws_iam_role.local_worker[*].arn)
}
