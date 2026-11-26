"""M4 SPEC (written before the code, 2026-11-26): the service container image.

Frozen: changing an assertion needs the owner's approval. Needs Docker.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "service"
TAG = "iiot-monitor:spec"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=600)


@pytest.fixture(scope="module")
def image():
    assert (SERVICE / "Dockerfile").is_file(), "service/Dockerfile does not exist yet"
    run("docker", "build", "-t", TAG, str(SERVICE))
    return json.loads(run("docker", "image", "inspect", TAG).stdout)[0]


# Every test takes the `image` fixture: without it, `docker run` on a missing image fails and
# "X is NOT importable" style assertions would pass vacuously.
def in_image(*cmd, check=False):
    return run("docker", "run", "--rm", "--entrypoint", cmd[0], TAG, *cmd[1:], check=check)


def test_runs_as_non_root(image):
    assert image["Config"]["User"] not in ("", "root", "0")
    assert in_image("id", "-u", check=True).stdout.strip() != "0"


def test_default_command_starts_the_worker(image):
    cmd = " ".join((image["Config"].get("Entrypoint") or []) + (image["Config"].get("Cmd") or []))
    assert "iiot_monitor.worker" in cmd


def test_production_deps_present(image):
    assert in_image("python", "-c", "import boto3, prometheus_client, iiot_monitor.worker")\
        .returncode == 0


@pytest.mark.parametrize("dev_only", ["pytest", "ruff", "awscrt"])
def test_dev_dependencies_not_in_image(image, dev_only):
    # Smaller attack surface + fewer CVEs for Trivy; awscrt is only for local `aws login`.
    assert in_image("python", "-c", f"import {dev_only}").returncode != 0


def test_tests_and_secrets_not_copied_into_image(image):
    out = in_image("sh", "-c", "find / -xdev \\( -name '*.key' -o -name '*.pem' -o -name "
                               "'*.tfvars' -o -name 'tests' -path '*iiot*' \\) 2>/dev/null "
                               "| grep -v -e /usr/lib/ssl -e /etc/ssl -e site-packages || true")
    assert out.stdout.strip() == ""


def test_fails_fast_without_region(image):
    # No silent fallback to a profile/default region (CLAUDE.md: never rely on the default).
    r = run("docker", "run", "--rm", TAG, check=False)
    assert r.returncode != 0 and "AWS_REGION" in (r.stdout + r.stderr)


def test_no_bytecode_writes_needed_for_read_only_rootfs(image):
    # The task definition sets readonlyRootFilesystem; Python must not need to write .pyc.
    r = run("docker", "run", "--rm", "--read-only", "--entrypoint", "python", TAG, "-c",
            "import iiot_monitor.worker", check=False)
    assert r.returncode == 0, r.stderr
