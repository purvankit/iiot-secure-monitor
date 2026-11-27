"""S1 SPEC (written before the code, 2026-11-27): the same worker image on local Kubernetes
(kind), deployed with a Helm chart, scraped by kube-prometheus-stack.

Contract for k8s/charts/iiot-worker (rendered with tests/spec/s1-values.yaml), the kind
cluster config and the bring-up script. Needs `helm` on PATH; CI installs it, and a missing
helm in CI is a failure, not a skip. Frozen: changing an assertion needs the owner's approval.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "charts" / "iiot-worker"
VALUES = Path(__file__).with_name("s1-values.yaml")
WORKER_ENV = {"AWS_REGION", "QUEUE_URL", "TABLE_READINGS", "TABLE_LATEST", "TABLE_ALERTS",
              "ALERT_TOPIC_ARN"}
STATIC_KEYS = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}

if shutil.which("helm") is None:
    if os.getenv("CI"):
        raise RuntimeError("helm must be installed in CI to check the S1 spec")
    pytest.skip("helm not installed", allow_module_level=True)


def helm(*args, check=True):
    assert CHART.is_dir(), "k8s/charts/iiot-worker does not exist yet"
    return subprocess.run(["helm", "template", "iiot-worker", str(CHART), *args],
                          capture_output=True, text=True, check=check, timeout=120)


@pytest.fixture(scope="module")
def docs():
    out = helm("-f", str(VALUES), "--namespace", "iiot").stdout
    return [d for d in yaml.safe_load_all(out) if d]


def one(docs, kind):
    found = [d for d in docs if d["kind"] == kind]
    assert len(found) == 1, f"expected exactly one {kind}, found {len(found)}"
    return found[0]


def pod(docs):
    return one(docs, "Deployment")["spec"]["template"]["spec"]


def worker(docs):
    return next(c for c in pod(docs)["containers"] if c["name"] == "worker")


# ---------------------------------------------------------------- image + rollout

def test_image_must_be_a_commit_sha():
    # Same rule as ECR: immutable, traceable tags. Never `latest`, never missing.
    base = ["-f", str(VALUES)]
    assert helm(*base, "--set", "image.tag=latest", check=False).returncode != 0
    assert helm(*base, "--set", "image.tag=", check=False).returncode != 0


def test_runs_the_exact_image(docs):
    v = yaml.safe_load(VALUES.read_text())["image"]
    assert worker(docs)["image"] == f"{v['repository']}:{v['tag']}"


def test_exactly_one_worker_and_old_one_stops_first(docs):
    # One-worker rule (detection state is per-process), like the Fargate service's
    # minimum_healthy 0 / maximum 100.
    spec = one(docs, "Deployment")["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"]["type"] == "Recreate"


# ---------------------------------------------------------------- hardening

def test_pod_is_hardened(docs):
    p = pod(docs)
    assert p["securityContext"]["runAsNonRoot"] is True
    assert p["securityContext"]["runAsUser"] == 10001
    assert p["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert p.get("automountServiceAccountToken") is False, "the worker never calls the K8s API"
    assert not p.get("hostNetwork") and not p.get("hostPID") and not p.get("hostIPC")
    assert not any("hostPath" in v for v in p.get("volumes", []))


def test_container_is_hardened(docs):
    sc = worker(docs)["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert not sc.get("privileged")


def test_resources_match_the_fargate_task(docs):
    r = worker(docs)["resources"]
    assert r["limits"]["cpu"] == "250m" and r["limits"]["memory"] == "512Mi"
    assert r["requests"]["cpu"] and r["requests"]["memory"]


def test_probes_use_the_health_endpoint(docs):
    c = worker(docs)
    for probe in ("livenessProbe", "readinessProbe"):
        assert c[probe]["httpGet"]["path"] == "/health"
        assert c[probe]["httpGet"]["port"] in (8080, "metrics")


# ---------------------------------------------------------------- configuration + credentials

def test_env_contract_and_region(docs):
    env = {e["name"]: e.get("value") for e in worker(docs).get("env", [])}
    assert WORKER_ENV <= set(env), WORKER_ENV - set(env)
    assert env["AWS_REGION"] == "ap-south-1"


def test_no_static_aws_keys_anywhere(docs):
    c = worker(docs)
    assert not STATIC_KEYS & {e["name"] for e in c.get("env", [])}
    assert not c.get("envFrom"), "no bulk env from Secrets/ConfigMaps (keys could slip in)"
    assert not [d for d in docs if d["kind"] == "Secret"], \
        "credentials are never part of the chart; a script creates the Secret at runtime"


def test_credentials_come_from_a_read_only_secret_volume(docs):
    c, p = worker(docs), pod(docs)
    env = {e["name"]: e.get("value") for e in c.get("env", [])}
    secret_vols = {v["name"] for v in p["volumes"] if "secret" in v}
    mounts = [m for m in c["volumeMounts"] if m["name"] in secret_vols]
    assert mounts and all(m.get("readOnly") for m in mounts)
    assert any(env.get("AWS_CONFIG_FILE", "").startswith(m["mountPath"]) for m in mounts), \
        "AWS_CONFIG_FILE must point into the mounted Secret (credential_process, auto-refresh)"


# ---------------------------------------------------------------- metrics + network

def test_prometheus_scrapes_the_worker(docs):
    svc = one(docs, "Service")
    assert any(p["port"] == 8080 and p["name"] == "metrics" for p in svc["spec"]["ports"])
    sm = one(docs, "ServiceMonitor")
    eps = sm["spec"]["endpoints"]
    assert any(e["port"] == "metrics" and e.get("path", "/metrics") == "/metrics" for e in eps)


def test_network_policy_mirrors_the_fargate_security_group(docs):
    np = one(docs, "NetworkPolicy")["spec"]
    assert set(np["policyTypes"]) == {"Ingress", "Egress"}
    ing = np["ingress"]
    assert len(ing) == 1 and [p["port"] for p in ing[0]["ports"]] in ([8080], ["metrics"])
    sel = ing[0]["from"][0]["namespaceSelector"]["matchLabels"]
    assert sel == {"kubernetes.io/metadata.name": "monitoring"}, "only Prometheus may connect"
    allowed = {(p.get("protocol", "TCP"), p["port"]) for rule in np["egress"]
               for p in rule.get("ports", [])}
    assert allowed <= {("TCP", 443), ("UDP", 53), ("TCP", 53)} and ("TCP", 443) in allowed
    assert all(rule.get("ports") for rule in np["egress"]), "no egress rule without ports"


def test_dashboard_reuses_the_m6_queries(docs):
    # One source of truth for panels: the K8s Grafana shows the same PromQL as dashboards/.
    cm = [d for d in docs if d["kind"] == "ConfigMap"
          and d["metadata"].get("labels", {}).get("grafana_dashboard") == "1"]
    assert len(cm) == 1, "one dashboard ConfigMap labelled for the Grafana sidecar"
    k8s = json.loads(next(iter(cm[0]["data"].values())))
    m6 = json.loads((ROOT / "dashboards" / "iiot-worker.json").read_text())

    def exprs(d):
        return sorted(t["expr"] for p in d["panels"] for t in p.get("targets", []))
    assert exprs(k8s) == exprs(m6)
    assert {t["datasource"]["type"] for p in k8s["panels"] for t in p["targets"]} == {"prometheus"}


# ---------------------------------------------------------------- cluster + monitoring pins

def test_kind_node_image_pinned_by_digest():
    cfg = yaml.safe_load((ROOT / "k8s" / "kind-cluster.yaml").read_text())
    images = [n.get("image", "") for n in cfg["nodes"]]
    assert images and all(re.fullmatch(r"kindest/node:v[\d.]+@sha256:[0-9a-f]{64}", i)
                          for i in images)


def test_monitoring_stack_chart_version_pinned():
    script = (ROOT / "scripts" / "k8s-up.sh").read_text()
    m = re.search(r"kube-prometheus-stack[^\n]*--version[ =](\d+\.\d+\.\d+)\b", script) or \
        re.search(r"--version[ =](\d+\.\d+\.\d+)\b[^\n]*kube-prometheus-stack", script)
    assert m, "install kube-prometheus-stack with an exact --version"
