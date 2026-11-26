output "queue_url" {
  value = aws_sqs_queue.telemetry.url
}

output "queue_arn" {
  value = aws_sqs_queue.telemetry.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}
