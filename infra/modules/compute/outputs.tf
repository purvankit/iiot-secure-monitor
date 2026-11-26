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
