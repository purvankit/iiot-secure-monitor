# Offline: mock provider. Run: terraform -chdir=infra/modules/ci_oidc test
# Privilege-escalation guards beyond the frozen spec (tests/spec/tf/tests/m5_ci_oidc.tftest.hcl).

mock_provider "aws" {
  source = "../../tests/mocks"
}

variables {
  github_repo      = "example-owner/iiot-secure-monitor"
  state_bucket_arn = "arn:aws:s3:::iiot-secure-monitor-tfstate-123456789012"
}

run "iam_writes_require_the_boundary" {
  command = apply
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.apply_iam.policy).Statement :
      length(setintersection(toset(s.Action), toset(["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy"]))) == 0
    || s.Condition.StringEquals["iam:PermissionsBoundary"] == aws_iam_policy.workload_boundary.arn])
    error_message = "creating roles or adding policies must require our permissions boundary"
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.apply_iam.policy, "DeleteRolePermissionsBoundary")
    error_message = "the apply role must never be able to remove a boundary"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.apply_iam.policy).Statement :
    s.Resource == "arn:aws:iam::123456789012:role/iiot-*"])
    error_message = "IAM rights only on project (iiot-*) roles"
  }
}

run "ci_roles_cannot_edit_themselves" {
  command = apply
  assert {
    condition     = !startswith(aws_iam_role.apply.name, "iiot-") && !startswith(aws_iam_role.plan.name, "iiot-")
    error_message = "CI roles must sit outside the iiot-* prefix the apply role may modify"
  }
}

run "pass_role_only_to_project_services" {
  command = apply
  assert {
    condition = toset(one([for s in jsondecode(aws_iam_role_policy.apply_iam.policy).Statement :
    s.Condition.StringEquals["iam:PassedToService"] if contains(s.Action, "iam:PassRole")])) == toset(["ecs-tasks.amazonaws.com", "iot.amazonaws.com"])
    error_message = "PassRole limited to ECS tasks and IoT"
  }
}

run "plan_role_writes_only_the_lock_file" {
  command = apply
  assert {
    condition     = jsondecode(aws_iam_role_policy.plan_state_lock.policy).Statement[0].Resource == "arn:aws:s3:::iiot-secure-monitor-tfstate-123456789012/envs/dev/terraform.tfstate.tflock"
    error_message = "plan role: write access to the lock object only, not the state"
  }
}

run "boundary_has_no_wildcard_actions" {
  command = apply
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.workload_boundary.policy).Statement :
    alltrue([for a in flatten([s.Action]) : !strcontains(a, "*")])])
    error_message = "the ceiling lists exact actions"
  }
}

run "rejects_wildcard_repo" {
  command = plan
  variables { github_repo = "example-owner/*" }
  expect_failures = [var.github_repo]
}

run "accepts_immutable_subject_form" {
  command = plan
  variables { github_repo = "example-owner@1234/iiot-secure-monitor@5678" }
  assert {
    condition     = strcontains(aws_iam_role.apply.assume_role_policy, "repo:example-owner@1234/iiot-secure-monitor@5678:environment:dev")
    error_message = "immutable-subject repos (owner@id/name@id) must produce the matching sub"
  }
}

run "boundary_covers_every_task_role_action" {
  # Lesson from M5: an action in a role policy but not in the boundary is denied in AWS while
  # every offline test passes. Keep this list in sync with modules/compute's task role.
  command = apply
  assert {
    condition = length(setsubtract(toset([
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "dynamodb:PutItem", "dynamodb:Scan",
      "sns:Publish", "iot:ListThings", "logs:FilterLogEvents", "aps:RemoteWrite",
    ]), toset(flatten([for s in jsondecode(aws_iam_policy.workload_boundary.policy).Statement : s.Action])))) == 0
    error_message = "every task-role action must also be allowed by the workload boundary"
  }
}
