# M5 SPEC (written before the code, 2026-11-26). Frozen: changes need the owner's approval.
# Contract for infra/modules/ci_oidc: GitHub OIDC provider + two roles.
#   plan  role: assumable only from pull requests of this repo; read-only (+ state lock).
#   apply role: assumable only from the protected "dev" environment of this repo.

mock_provider "aws" {
  source = "../../../infra/tests/mocks"
}

variables {
  github_repo      = "example-owner/iiot-secure-monitor"
  state_bucket_arn = "arn:aws:s3:::iiot-secure-monitor-tfstate-123456789012"
}

run "oidc_provider_is_githubs" {
  command = apply
  module { source = "../../../infra/modules/ci_oidc" }

  assert {
    condition     = aws_iam_openid_connect_provider.github.url == "https://token.actions.githubusercontent.com"
    error_message = "trust GitHub's OIDC issuer"
  }
  assert {
    condition     = toset(aws_iam_openid_connect_provider.github.client_id_list) == toset(["sts.amazonaws.com"])
    error_message = "audience must be sts.amazonaws.com"
  }
}

run "plan_role_only_for_this_repos_pull_requests" {
  command = apply
  module { source = "../../../infra/modules/ci_oidc" }

  assert {
    condition     = jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "plan role must check the token audience"
  }
  assert {
    condition = toset(flatten([jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]])) == toset([
      "repo:example-owner/iiot-secure-monitor:pull_request",
    ])
    error_message = "plan role: exactly this repo's pull_request subject (no wildcards, no other repos)"
  }
  assert {
    condition     = contains([for a in aws_iam_role_policy_attachment.plan_readonly[*] : a.policy_arn], "arn:aws:iam::aws:policy/ReadOnlyAccess")
    error_message = "plan role is read-only"
  }
}

run "apply_role_only_from_protected_environment" {
  command = apply
  module { source = "../../../infra/modules/ci_oidc" }

  assert {
    condition = toset(flatten([jsondecode(aws_iam_role.apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]])) == toset([
      "repo:example-owner/iiot-secure-monitor:environment:dev",
    ])
    error_message = "apply role: only jobs in this repo's protected 'dev' environment (manual approval)"
  }
  assert {
    condition     = !strcontains(aws_iam_role.apply.assume_role_policy, "pull_request")
    error_message = "a pull request must never be able to assume the apply role"
  }
  assert {
    condition     = aws_iam_role.apply.max_session_duration <= 3600
    error_message = "apply credentials live at most 1 hour"
  }
}
