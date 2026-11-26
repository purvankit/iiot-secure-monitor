output "readings_table" {
  value = aws_dynamodb_table.readings.name
}

output "latest_table" {
  value = aws_dynamodb_table.latest.name
}

output "alerts_table" {
  value = aws_dynamodb_table.alerts.name
}

output "table_arns" {
  value = [
    aws_dynamodb_table.readings.arn,
    aws_dynamodb_table.latest.arn,
    aws_dynamodb_table.alerts.arn,
  ]
}
