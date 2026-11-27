# Offline: mock provider. Run: terraform -chdir=infra/modules/monitoring test

mock_provider "aws" {
  source = "../../tests/mocks"
}

run "workspace_exists_with_project_alias" {
  command = apply
  assert {
    condition     = aws_prometheus_workspace.metrics.alias == "iiot-metrics"
    error_message = "one AMP workspace, named for the project"
  }
  assert {
    condition     = startswith(output.workspace_arn, "arn:aws:aps:ap-south-1:")
    error_message = "workspace ARN feeds the task role's aps:RemoteWrite scope"
  }
}
