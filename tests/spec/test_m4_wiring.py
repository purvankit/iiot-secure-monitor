"""M4 SPEC (2026-11-26): the dev environment wires the compute module, and the alarm's log
pattern matches what the worker really logs. Frozen: changes need the owner's approval."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dev_env_uses_compute_module():
    main = (ROOT / "infra/envs/dev/main.tf").read_text()
    assert re.search(r'module\s+"compute"\s*{[^}]*source\s*=\s*"\.\./\.\./modules/compute"', main)


def test_compute_desired_count_is_a_dev_variable_defaulting_to_zero():
    # Lets you run `terraform apply -var desired_count=1` for a demo, and 0 otherwise.
    dev = "\n".join(p.read_text() for p in (ROOT / "infra/envs/dev").glob("*.tf"))
    m = re.search(r'variable\s+"desired_count"\s*{([^}]*)}', dev)
    assert m and re.search(r"default\s*=\s*0\b", m.group(1))


def test_worker_logs_the_string_the_alarm_matches():
    src = (ROOT / "service/iiot_monitor/worker.py").read_text()
    assert "processing failed" in src
