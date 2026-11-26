# Device identity and authorisation in AWS IoT Core.
#
# - One Thing + one X.509 certificate per device. The certificate is signed from a
#   CSR generated on the device, so the private key never reaches AWS or Terraform state.
# - ONE shared policy using policy variables: a device may connect only with
#   client ID == its Thing name, and publish only to iiot/<its Thing name>/telemetry.
# - IoT logging (connect/auth failures etc.) goes to CloudWatch Logs with short retention.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  arn_prefix = "arn:aws:iot:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
}

resource "aws_iot_thing" "device" {
  for_each = var.devices
  name     = each.key
  attributes = {
    device_type = each.value.type
    site        = each.value.site
  }
}

resource "aws_iot_certificate" "device" {
  for_each = var.devices
  csr      = file("${var.csr_dir}/${each.key}.csr")
  active   = true
}

resource "aws_iot_thing_principal_attachment" "device" {
  for_each  = var.devices
  thing     = aws_iot_thing.device[each.key].name
  principal = aws_iot_certificate.device[each.key].arn
}

resource "aws_iot_policy" "device" {
  name = "iiot-device-policy"
  # "$${...}" escapes Terraform interpolation; IoT Core substitutes these at connect time.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ConnectAsOwnThingOnly"
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "${local.arn_prefix}:client/$${iot:Connection.Thing.ThingName}"
        # Certificate must be attached to a registered Thing (rejects unregistered certs).
        Condition = { Bool = { "iot:Connection.Thing.IsAttached" = "true" } }
      },
      {
        Sid      = "PublishOwnTelemetryOnly"
        Effect   = "Allow"
        Action   = "iot:Publish"
        Resource = "${local.arn_prefix}:topic/iiot/$${iot:Connection.Thing.ThingName}/telemetry"
      },
    ]
  })
}

resource "aws_iot_policy_attachment" "device" {
  for_each = var.devices
  policy   = aws_iot_policy.device.name
  target   = aws_iot_certificate.device[each.key].arn
}

data "aws_iot_endpoint" "ats" {
  endpoint_type = "iot:Data-ATS"
}

# --- IoT Core logging -> CloudWatch Logs (detection rule 1 uses auth failures) ---

# Pre-create the log group so retention is 7 days, not "never expire".
resource "aws_cloudwatch_log_group" "iot" {
  name              = "AWSIotLogsV2"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "iot_logging" {
  name                 = "iiot-iot-logging"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "iot.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy protection: only IoT Core acting for THIS account.
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "iot_logging" {
  role = aws_iam_role.iot_logging.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.iot.arn}:*"
    }]
  })
}

# Account+region-wide setting. WARN includes ERROR, which is where connect/auth failures land.
resource "aws_iot_logging_options" "this" {
  default_log_level = "WARN"
  role_arn          = aws_iam_role.iot_logging.arn
  depends_on        = [aws_iam_role_policy.iot_logging, aws_cloudwatch_log_group.iot]
}
