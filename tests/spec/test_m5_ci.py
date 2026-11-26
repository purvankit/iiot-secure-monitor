"""M5 SPEC (written before the code, 2026-11-26): GitHub Actions pipeline.

ci.yml (every PR + push to main, no AWS access): ruff, pytest, terraform fmt/validate/test,
docker build + Trivy scan. deploy.yml: plan on PR (read-only role), build/push + apply on
main (separate role, protected environment = manual approval), all via OIDC.
Frozen: changing an assertion needs the owner's approval.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"


def load(name):
    path = WF / name
    assert path.is_file(), f".github/workflows/{name} does not exist yet"
    doc = yaml.safe_load(path.read_text())
    doc["on"] = doc.get("on", doc.get(True))  # YAML 1.1 parses the key `on` as True
    return doc, path.read_text()


def steps(doc):
    return [s for job in doc["jobs"].values() for s in job.get("steps", [])]


def runs(doc):
    return "\n".join(s.get("run", "") for s in steps(doc))


def tf(sub: str) -> re.Pattern:
    """`terraform <sub>`, allowing global options such as -chdir=DIR before the subcommand."""
    return re.compile(r"terraform(?:\s+-chdir=\S+)*\s+" + sub)


@pytest.fixture(params=["ci.yml", "deploy.yml"])
def workflow(request):
    return load(request.param)


# ---------------------------------------------------------------- every workflow

def test_top_level_permissions_declared(workflow):
    doc, _ = workflow
    assert "permissions" in doc, "default GITHUB_TOKEN permissions are too broad"


def test_no_long_lived_aws_keys(workflow):
    _, text = workflow
    for bad in ("aws-access-key-id", "aws-secret-access-key", "AWS_SECRET_ACCESS_KEY"):
        assert bad not in text, "use OIDC (role-to-assume), never stored keys"


def test_actions_pinned_to_commit_sha(workflow):
    doc, _ = workflow
    for s in steps(doc):
        uses = s.get("uses")
        if uses and not uses.startswith("./"):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), \
                f"{uses}: pin third-party actions to a full commit SHA (tags can be moved)"


def test_no_pull_request_target(workflow):
    doc, _ = workflow
    assert "pull_request_target" not in (doc["on"] or {}), \
        "pull_request_target runs fork code with secrets"


# ---------------------------------------------------------------- ci.yml

def test_ci_triggers_and_has_no_aws_access():
    doc, _ = load("ci.yml")
    assert {"pull_request", "push"} <= set(doc["on"])
    assert "id-token" not in str(doc["permissions"]), "CI never touches AWS"
    assert doc["permissions"] in ({"contents": "read"}, "read-all")


def test_ci_runs_lint_tests_and_terraform_checks():
    doc, _ = load("ci.yml")
    r = runs(doc)
    assert "ruff check" in r and "pytest" in r, "ci.yml must run ruff and pytest"
    for sub in (r"fmt\s+(-recursive\s+)?-check", r"validate\b", r"test\b"):
        assert tf(sub).search(r), f"ci.yml must run `terraform {sub}`"
    for target in ("simulator", "service", "tests"):
        assert re.search(rf"pytest[^\n]*{target}|cd {target}[^\n]*\n?[^\n]*pytest", r), \
            f"ci.yml must run the {target} test suite"


def test_ci_builds_and_scans_image_failing_on_high():
    doc, _ = load("ci.yml")
    assert "docker build" in runs(doc) or any("docker/build-push-action" in s.get("uses", "")
                                              for s in steps(doc))
    trivy = [s for s in steps(doc) if "aquasecurity/trivy-action" in s.get("uses", "")]
    assert trivy, "ci.yml must scan the image with Trivy"
    w = trivy[0].get("with", {})
    assert "CRITICAL" in str(w.get("severity", "")) and "HIGH" in str(w.get("severity", ""))
    assert str(w.get("exit-code")) == "1", "a HIGH/CRITICAL finding must fail the build"


# ---------------------------------------------------------------- deploy.yml

def test_deploy_uses_oidc_in_ap_south_1():
    doc, _ = load("deploy.yml")
    assert doc["permissions"].get("id-token") == "write"
    creds = [s for s in steps(doc) if "aws-actions/configure-aws-credentials" in s.get("uses", "")]
    assert creds, "deploy.yml must authenticate with configure-aws-credentials"
    for s in creds:
        assert "role-to-assume" in s["with"] and s["with"].get("aws-region") == "ap-south-1"


def test_plan_on_pr_apply_only_on_main_with_approval():
    doc, _ = load("deploy.yml")
    assert {"pull_request", "push"} <= set(doc["on"])
    jobs = doc["jobs"]
    def job_runs(j):
        return "\n".join(s.get("run", "") for s in j.get("steps", []))
    apply = [n for n, j in jobs.items() if tf(r"apply\b").search(job_runs(j))]
    plan = [n for n, j in jobs.items() if tf(r"plan\b").search(job_runs(j))]
    assert plan and apply
    for n in apply:
        j = jobs[n]
        assert j.get("environment"), "apply must run in a protected environment (manual approval)"
        assert "refs/heads/main" in str(j.get("if", "")) or doc["on"]["push"] == {
            "branches": ["main"]}, "apply only from main"
    roles = {n: [s["with"]["role-to-assume"] for s in jobs[n].get("steps", [])
                 if "configure-aws-credentials" in s.get("uses", "")] for n in plan + apply}
    assert set(sum((roles[n] for n in plan), [])).isdisjoint(sum((roles[n] for n in apply), [])),\
        "plan (read-only) and apply must use different roles"


def test_deploy_never_runs_concurrently():
    doc, _ = load("deploy.yml")
    assert doc.get("concurrency"), "two applies at once could corrupt state / race deploys"


def test_image_pushed_with_immutable_commit_tag():
    _, text = load("deploy.yml")
    assert "github.sha" in text, "tag images with the commit SHA (ECR tags are immutable)"
