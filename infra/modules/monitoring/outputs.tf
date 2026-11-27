output "workspace_arn" {
  description = "The only workspace the task role may remote-write to"
  value       = aws_prometheus_workspace.metrics.arn
}

output "query_url" {
  description = "Prometheus-compatible endpoint for Grafana (it appends /api/v1/query)"
  value       = aws_prometheus_workspace.metrics.prometheus_endpoint
}
