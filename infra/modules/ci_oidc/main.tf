# GitHub Actions -> AWS without stored keys (OIDC), as two roles with different powers:
#
#   plan  role  <- any pull request of THIS repo     read-only (+ the state lock file)
#   apply role  <- only jobs in the protected "dev"  PowerUser + IAM limited to bounded iiot-* roles
#                  environment (manual approval)
#
# How OIDC works: GitHub signs a short-lived token (JWT) per job saying "repo X, PR / env Y".
# AWS checks the signature against GitHub's issuer and our trust policy's conditions, then
# hands out 1-hour credentials. Nothing secret is stored in GitHub.
#
# Privilege escalation guard: IAM writes are allowed only on roles named iiot-* AND only if
# they carry the workload boundary below. A boundary is a ceiling: whatever policy a role
# gets (even AdministratorAccess), it can never do more than the boundary allows. So a
# compromised deploy can't mint itself an admin role. The CI roles are named github-iiot-*,
# outside the iiot-* prefix, so the apply role can't edit itself either.
#
# Applied by the owner from a laptop (infra/ci), never by CI: CI must not manage its own access.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  issuer  = "token.actions.githubusercontent.com"
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  p       = var.resource_prefix
  # ARN prefixes, so the policies below stay readable
  arn_role = "arn:aws:iam::${local.account}:role"
  arn_logs = "arn:aws:logs:${local.region}:${local.account}:log-group"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.issuer}"
  client_id_list = ["sts.amazonaws.com"] # the token's audience (`aud`) for AWS
  # No thumbprint_list: AWS verifies GitHub's issuer through its own trusted CA list.
}

# ---------------------------------------------------------------- plan role

resource "aws_iam_role" "plan" {
  name                 = "github-iiot-plan"
  description          = "terraform plan from pull requests of ${var.github_repo} (read-only)"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "${local.issuer}:aud" = "sts.amazonaws.com"
        # Exact match: StringLike with "repo:*" would let ANY GitHub repo in.
        "${local.issuer}:sub" = "repo:${var.github_repo}:pull_request"
      } }
    }]
  })
}

# Why read-only matters: a PR can change the workflow file itself, so whoever opens a PR
# controls what runs with this role. Read-only caps the damage at "can look".
resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# `terraform plan` takes the S3 state lock (a .tflock object next to the state), so it
# needs to create and delete exactly that one object.
resource "aws_iam_role_policy" "plan_state_lock" {
  role = aws_iam_role.plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:DeleteObject"]
      Resource = "${var.state_bucket_arn}/${var.state_key}.tflock"
    }]
  })
}

# ---------------------------------------------------------------- apply role

resource "aws_iam_role" "apply" {
  name                 = "github-iiot-apply"
  description          = "terraform apply + image push from the protected '${var.environment}' environment of ${var.github_repo}"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "${local.issuer}:aud" = "sts.amazonaws.com"
        # Only a job declaring `environment: dev`, which GitHub holds until a reviewer approves.
        "${local.issuer}:sub" = "repo:${var.github_repo}:environment:${var.environment}"
      } }
    }]
  })
}

# PowerUserAccess = everything except IAM / Organizations / Account management.
# guardrail: allow-wildcard (PowerUserAccess: IAM is excluded and re-granted narrowly below; see header)
resource "aws_iam_role_policy_attachment" "apply_poweruser" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

resource "aws_iam_role_policy" "apply_iam" {
  name = "bounded-project-roles"
  role = aws_iam_role.apply.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadProjectRoles"
        Effect = "Allow"
        Action = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:ListRoleTags"]
        Resource = "${local.arn_role}/${local.p}-*"
      },
      {
        # These actions support the iam:PermissionsBoundary condition: they only work on a
        # role that has (or is being created with) exactly our boundary.
        Sid    = "WriteOnlyBoundedProjectRoles"
        Effect = "Allow"
        Action = ["iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePermissionsBoundary"]
        Resource  = "${local.arn_role}/${local.p}-*"
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.workload_boundary.arn } }
      },
      {
        # No boundary condition key exists for these; they can't add permissions.
        # (Removing a boundary, iam:DeleteRolePermissionsBoundary, is NOT granted.)
        Sid    = "MaintainProjectRoles"
        Effect = "Allow"
        Action = ["iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
        "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole"]
        Resource = "${local.arn_role}/${local.p}-*"
      },
      {
        # Hand project roles only to the services that use them (ECS tasks, IoT rules/logging).
        Sid       = "PassProjectRolesToProjectServices"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = "${local.arn_role}/${local.p}-*"
        Condition = { StringEquals = { "iam:PassedToService" = ["ecs-tasks.amazonaws.com", "iot.amazonaws.com"] } }
      },
    ]
  })
}

# ---------------------------------------------------------------- workload boundary

# The ceiling for every project role: the union of what the IoT rule, IoT logging, ECS
# execution and ECS task roles actually use, on project-named resources only.
# Adding an AWS call to the worker = add it to the role policy AND here.
resource "aws_iam_policy" "workload_boundary" {
  name        = "${local.p}-workload-boundary"
  description = "Permissions boundary: maximum any ${local.p}-* role can ever do"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Queues"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage"]
        Resource = "arn:aws:sqs:${local.region}:${local.account}:${local.p}-*"
      },
      {
        Sid      = "RawArchive"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "arn:aws:s3:::${local.p}-secure-monitor-raw-${local.account}/*"
      },
      {
        Sid      = "Tables"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:Scan"]
        Resource = "arn:aws:dynamodb:${local.region}:${local.account}:table/${local.p}-*"
      },
      {
        Sid      = "Alerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = "arn:aws:sns:${local.region}:${local.account}:${local.p}-*"
      },
      {
        # The alerts topic is KMS-encrypted; publishing uses the key, but only through SNS.
        Sid       = "AlertTopicKeyViaSns"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = "arn:aws:kms:${local.region}:${local.account}:key/*"
        Condition = { StringEquals = { "kms:ViaService" = "sns.${local.region}.amazonaws.com" } }
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
        "logs:FilterLogEvents"]
        Resource = [
          "${local.arn_logs}:AWSIotLogsV2*",
          "${local.arn_logs}:/${local.p}/*",
          "${local.arn_logs}:/ecs/${local.p}-*",
        ]
      },
      {
        Sid      = "PullProjectImages"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = "arn:aws:ecr:${local.region}:${local.account}:repository/${local.p}-*"
      },
      {
        # ADR 0001: the metrics sidecar pushes to AMP. Workspace IDs are random (ws-...), so
        # the ceiling allows any workspace in this account+region; the task role itself is
        # pinned to the one workspace ARN.
        Sid      = "PushMetrics"
        Effect   = "Allow"
        Action   = ["aps:RemoteWrite"]
        Resource = "arn:aws:aps:${local.region}:${local.account}:workspace/*"
      },
      {
        Sid    = "UnscopableReads"
        Effect = "Allow"
        Action = ["iot:ListThings", "ecr:GetAuthorizationToken"]
        # guardrail: allow-wildcard (neither action has a resource type; IAM only accepts "*")
        Resource = "*"
      },
    ]
  })
}
