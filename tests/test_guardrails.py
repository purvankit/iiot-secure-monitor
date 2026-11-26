"""Repo-wide guardrails: CLAUDE.md's cost, region, secret and least-privilege rules as tests.

They scan every Terraform file, so they also cover modules that don't exist yet (M4, M5).
An intentional exception must be marked on the offending line or the line above with
`# guardrail: allow-wildcard (<reason>)`, so it is visible in review.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TF_FILES = sorted(p for p in (ROOT / "infra").rglob("*.tf") if ".terraform" not in p.parts)
MOCK_ACCOUNT = "123456789012"
ALLOW_MARK = "guardrail: allow-wildcard"


def blocks(kind: str, type_: str, files=None):
    """Yield (file, name, body) for every `<kind> "<type_>" "<name>" {...}` block.

    Single-label blocks (`variable "x" {`, `provider "aws" {`) yield name == type_.
    """
    labels = r'"([\w-]+)"\s*' if kind == "resource" or kind == "data" else r"()"
    head = re.compile(rf'^{kind}\s+"{re.escape(type_)}"\s*{labels}{{', re.M)
    for f in files or TF_FILES:
        text = f.read_text()
        for m in head.finditer(text):
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            yield f, m.group(1) or type_, text[m.end():i - 1]


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def test_terraform_files_found():
    assert len(TF_FILES) >= 10  # sanity: the scan is actually looking at the infra


def test_block_finder_handles_one_and_two_label_blocks(tmp_path):
    # Regression: variable blocks have ONE label; a two-label-only parser silently found
    # nothing, which made the desired_count check pass without checking anything.
    f = tmp_path / "x.tf"
    f.write_text('variable "desired_count" {\n  default = 2\n}\n'
                 'resource "aws_sqs_queue" "q" {\n  name = "q"\n}\n')
    assert [(n, "default = 2" in b) for _, n, b in blocks("variable", "desired_count", [f])] \
        == [("desired_count", True)]
    assert [n for _, n, _ in blocks("resource", "aws_sqs_queue", [f])] == ["q"]
    assert [n for _, n, _ in blocks("variable", "log_retention_days")], "real infra var missed"


# ---------------------------------------------------------------- cost (CLAUDE.md §7)

FORBIDDEN = {
    "aws_nat_gateway": "NAT gateways bill hourly; use a public subnet (VPC endpoints in prod)",
    "aws_lb": "no idle load balancers", "aws_alb": "no idle load balancers",
    "aws_elb": "no idle load balancers",
    "aws_eks_cluster": "EKS bills even when idle", "aws_eks_node_group": "EKS bills when idle",
    "aws_instance": "Fargate instead of EC2 (no servers to patch)",
    "aws_db_instance": "out of scope, bills hourly", "aws_rds_cluster": "bills hourly",
    "aws_elasticache_cluster": "bills hourly", "aws_apprunner_service": "closed to new users",
}


@pytest.mark.parametrize("type_", sorted(FORBIDDEN))
def test_no_hourly_billed_or_forbidden_resources(type_):
    found = [f"{rel(f)}:{n}" for f, n, _ in blocks("resource", type_)]
    assert not found, f"{type_} is forbidden ({FORBIDDEN[type_]}): {found}"


def _var_default(module_dir: Path, var: str) -> str | None:
    """Default of `variable "<var>"` declared in the same module, if any."""
    for f, _name, body in blocks("variable", var):
        if f.parent == module_dir:
            m = re.search(r"default\s*=\s*(\S+)", body)
            return m.group(1) if m else None
    return None


def test_every_log_group_has_short_retention():
    for f, name, body in blocks("resource", "aws_cloudwatch_log_group"):
        m = re.search(r"retention_in_days\s*=\s*(\d+|var\.\w+)", body)
        assert m, f"{rel(f)}:{name} has no retention (default is never expire)"
        value = m.group(1)
        if value.startswith("var."):  # follow the variable to its default
            value = _var_default(f.parent, value[4:])
            assert value and value.isdigit(), f"{rel(f)}:{name}: {m.group(1)} needs a default"
        assert 1 <= int(value) <= 14, f"{rel(f)}:{name} retention must be 1-14 days"


def test_ecs_services_default_to_zero_tasks():
    # Fargate bills per running task-hour: anything that runs must default to off.
    for f, _name, body in blocks("variable", "desired_count"):
        assert re.search(r"default\s*=\s*0\b", body), f"{rel(f)}: desired_count must default to 0"


# ---------------------------------------------------------------- region + tags

def test_every_provider_pinned_to_mumbai():
    provs = []
    head = re.compile(r'^provider\s+"aws"\s*{', re.M)
    for f in TF_FILES:
        text = f.read_text()
        for m in head.finditer(text):
            body = text[m.end():m.end() + 2000]
            assert re.search(r'region\s*=\s*"ap-south-1"', body), \
                f'{rel(f)}: provider "aws" must pin region = "ap-south-1"'
            provs.append(f)
            assert re.search(r'project\s*=\s*"iiot-secure-monitor"', body), \
                f"{rel(f)}: provider default_tags must set project = iiot-secure-monitor"
    assert provs, "no provider blocks found"


# ---------------------------------------------------------------- least privilege

def _marked(lines, i):
    return ALLOW_MARK in lines[i] or (i > 0 and ALLOW_MARK in lines[i - 1])


def test_no_wildcard_actions_in_allow_statements():
    pat = re.compile(r'Action\s*=\s*(\[[^\]]*\]|"[^"]*")')
    for f in TF_FILES:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            m = pat.search(line)
            if not m or "*" not in m.group(1):
                continue
            window = "\n".join(lines[max(0, i - 6): i + 6])
            if '"Deny"' in window or _marked(lines, i):
                continue  # deny-all-insecure statements are fine; marked exceptions reviewed
            pytest.fail(f"{rel(f)}:{i + 1}: wildcard action in an Allow statement: {line.strip()}")


def test_no_wildcard_resources_unless_marked():
    pat = re.compile(r'Resource\s*=\s*"\*"')
    for f in TF_FILES:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            if pat.search(line) and not _marked(lines, i):
                pytest.fail(f'{rel(f)}:{i + 1}: Resource = "*" needs a "# {ALLOW_MARK} (why)" '
                            "comment (only for actions AWS can't scope, e.g. iot:ListThings)")


def test_no_aws_managed_admin_policies():
    for f in TF_FILES:
        for bad in ("AdministratorAccess", "PowerUserAccess", "IAMFullAccess"):
            assert bad not in f.read_text() or ALLOW_MARK in f.read_text(), \
                f"{rel(f)} attaches {bad}; scope it or mark + justify it"


def test_iam_trust_policies_for_aws_services_check_source_account():
    for f, name, body in blocks("resource", "aws_iam_role"):
        if '"iot.amazonaws.com"' in body:
            assert "aws:SourceAccount" in body, f"{rel(f)}:{name} lacks confused-deputy guard"


def test_every_bucket_blocks_public_access():
    pabs = {re.search(r"aws_s3_bucket\.([\w-]+)\.id", b).group(1)
            for _, _, b in blocks("resource", "aws_s3_bucket_public_access_block")}
    for f, name, _ in blocks("resource", "aws_s3_bucket"):
        assert name in pabs, f"{rel(f)}: bucket {name} has no public access block"


# ---------------------------------------------------------------- secrets + privacy

def tracked() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.splitlines()


def test_no_secrets_or_state_tracked_in_git():
    bad = re.compile(r"(\.key$|\.pem$|\.tfstate|/backend\.hcl$|/terraform\.tfvars$|"
                     r"^local/certs/|^local/aws-certs/)")
    leaks = [p for p in tracked() if bad.search(p)]
    assert not leaks, f"never commit these: {leaks}"


def test_no_real_account_ids_in_tracked_files():
    acct = re.compile(r"(?<![\d_])\d{12}(?![\d_])")
    hits = []
    for p in tracked():
        path = ROOT / p
        if path.suffix not in {".tf", ".hcl", ".py", ".md", ".yml", ".yaml", ".json", ".sh"}:
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            hits += [f"{p}:{n}" for a in acct.findall(line) if a != MOCK_ACCOUNT]
    assert not hits, f"possible AWS account ID committed (repo will be public): {hits}"
