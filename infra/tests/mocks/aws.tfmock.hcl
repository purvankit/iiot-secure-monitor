# Shared mock values for `terraform test` (offline). The AWS provider validates ARN formats,
# so mocked resources need realistic ARNs instead of the random strings mocks default to.

mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}
mock_data "aws_region" {
  defaults = { region = "ap-south-1", name = "ap-south-1" }
}
mock_data "aws_iot_endpoint" {
  defaults = { endpoint_address = "mock-ats.iot.ap-south-1.amazonaws.com" }
}
mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
}
mock_resource "aws_sqs_queue" {
  defaults = {
    arn = "arn:aws:sqs:ap-south-1:123456789012:mock"
    url = "https://sqs.ap-south-1.amazonaws.com/123456789012/mock"
  }
}
mock_resource "aws_s3_bucket" {
  defaults = { arn = "arn:aws:s3:::mock-bucket" }
}
mock_resource "aws_cloudwatch_log_group" {
  defaults = { arn = "arn:aws:logs:ap-south-1:123456789012:log-group:mock" }
}
mock_resource "aws_iot_certificate" {
  defaults = { arn = "arn:aws:iot:ap-south-1:123456789012:cert/mock" }
}
mock_resource "aws_sns_topic" {
  defaults = { arn = "arn:aws:sns:ap-south-1:123456789012:mock" }
}
mock_resource "aws_dynamodb_table" {
  defaults = { arn = "arn:aws:dynamodb:ap-south-1:123456789012:table/mock" }
}
mock_resource "aws_ecr_repository" {
  defaults = {
    arn            = "arn:aws:ecr:ap-south-1:123456789012:repository/mock"
    repository_url = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/mock"
  }
}
mock_resource "aws_ecs_cluster" {
  defaults = { arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/mock" }
}
mock_resource "aws_ecs_task_definition" {
  defaults = { arn = "arn:aws:ecs:ap-south-1:123456789012:task-definition/mock:1" }
}
mock_resource "aws_iam_openid_connect_provider" {
  defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
}
mock_resource "aws_kms_key" {
  defaults = {
    arn    = "arn:aws:kms:ap-south-1:123456789012:key/mock-kms-key-id"
    key_id = "mock-kms-key-id"
  }
}
mock_resource "aws_ecs_service" {
  defaults = { id = "arn:aws:ecs:ap-south-1:123456789012:service/mock/mock" }
}
mock_resource "aws_iam_policy" {
  defaults = { arn = "arn:aws:iam::123456789012:policy/mock" }
}
