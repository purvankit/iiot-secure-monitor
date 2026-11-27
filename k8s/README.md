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

## Why these choices

- **Pull here, push on Fargate.** Prometheus and the pod share the cluster network, so
  Prometheus scrapes `/metrics` directly; the Fargate task accepts no inbound connections, so
  a sidecar pushes from inside it.
- **NetworkPolicy = the Fargate security group.** Only the `monitoring` namespace may connect
  (port 8080); the pod may only call HTTPS and DNS.
- **A scoped role, not your login.** The pod gets the task role's permissions minus metrics
  push, for 1 hour at a time, renewed by the script. Only your IAM user can assume it.
- **`Recreate`, one replica.** Detection state is per-process (see the main README).
