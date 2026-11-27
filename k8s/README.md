# S1: the detection worker on local Kubernetes (kind)

The **same image** that runs on ECS Fargate (CI-built, Trivy-scanned, from ECR) runs in a
one-node kind cluster, against the **same AWS pipeline** (SQS, DynamoDB, SNS). In-cluster,
kube-prometheus-stack **pulls** the worker's metrics through a ServiceMonitor, the
counterpart of the push sidecar on Fargate (ADR 0001), and Grafana shows the same dashboard.

| Piece | File | Notes |
|---|---|---|
| Cluster | `kind-cluster.yaml` | One node, `kindest/node:v1.37.0` pinned by digest |
| Worker | `charts/iiot-worker/` | Helm chart: one replica + `Recreate`, hardened pod, Fargate-equal limits, NetworkPolicy, ServiceMonitor, dashboard ConfigMap |
| Monitoring | `monitoring/kube-prometheus-stack-values.yaml` | Chart `93.2.2` (pinned in `scripts/k8s-up.sh`), trimmed for kind |
| AWS access | `scripts/k8s-aws-creds.sh` | 1-hour credentials of the scoped `iiot-worker-local` role in a Secret; no keys in the chart |

The contract is frozen in `tests/spec/test_s1_k8s.py` and
`tests/spec/tf/tests/s1_local_worker_role.tftest.hcl`.

## Run it

**0. One-time tools** (kind v0.33.0, helm v3.22.1, checksum-verified, into `~/.local/bin`):
```bash
cd ~/projects/iiot-secure-monitor
./scripts/install-k8s-tools.sh
```

**1. Fargate must be at 0** (one worker at a time): GitHub → Actions → deploy → Run workflow →
`desired_count` 0. The script refuses to start otherwise.

**2. Bring everything up** (first run downloads images, ~5-10 min):
```bash
cd ~/projects/iiot-secure-monitor
./scripts/k8s-up.sh
```

**3. Keep the pod's credentials fresh** (own terminal, leave running):
```bash
cd ~/projects/iiot-secure-monitor
./scripts/k8s-aws-creds.sh
```

**4. Open Grafana** (another terminal):
```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3001:80
```
Then http://localhost:3001 → dashboard "IIoT detection worker" (anonymous, read-only).
Run the simulator as usual (`infra/README.md`) to see traffic.

**5. Tear down** (deletes the local cluster only; nothing in AWS):
```bash
kind delete cluster --name iiot
```

## Measured (2026-11-27, first run)

| Check | Result |
|---|---|
| Bring-up (`scripts/k8s-up.sh`, cold, images downloaded) | ~5 min to all pods ready |
| `kubectl get pods -A` | 14/14 Running; worker restarts 0 |
| Worker identity | `assumed-role/iiot-worker-local/iiot-kind-worker` (not the owner's login) |
| Prometheus target for the worker | `up`, no scrape error (~1 min after install: operator config reload) |
| In-cluster Grafana | Dashboard loaded from the chart's ConfigMap; 8/8 panel queries returned data |
| End to end | Simulator 36 messages published → dashboard "messages processed" 37 seconds later |
| NetworkPolicy, ingress | from `default`: blocked; from `monitoring`: allowed |
| NetworkPolicy, egress | HTTPS to AWS (443): allowed; HTTP to example.com (80): blocked |
| Cluster footprint (`docker stats`) | 2.25 GiB RAM of 7.7, ~26% CPU |

## Why these choices

- **Pull here, push on Fargate.** Prometheus and the pod share the cluster network, so
  Prometheus scrapes `/metrics` directly; the Fargate task accepts no inbound connections, so
  a sidecar pushes from inside it.
- **NetworkPolicy = the Fargate security group.** Only the `monitoring` namespace may connect
  (port 8080); the pod may only call HTTPS and DNS.
- **A scoped role, not your login.** The pod gets the task role's permissions minus metrics
  push, for 1 hour at a time, renewed by the script. Only your IAM user can assume it.
- **`Recreate`, one replica.** Detection state is per-process (see the main README).
