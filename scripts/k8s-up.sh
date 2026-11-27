#!/usr/bin/env bash
# S1: run the detection worker on a local kind cluster, scraped by kube-prometheus-stack.
# Idempotent: re-running upgrades in place. Usage (repo root): ./scripts/k8s-up.sh
#
# Uses the SAME image Fargate runs (CI-built, Trivy-scanned, pulled from ECR and loaded into
# kind) against the SAME AWS pipeline, with a scoped role (see scripts/k8s-aws-creds.sh).
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-iiot}" AWS_REGION=ap-south-1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
tf() { terraform -chdir=infra/envs/dev output -raw "$1"; }

for t in kind helm kubectl docker aws terraform; do
  command -v "$t" >/dev/null || { echo "missing: $t (see scripts/install-k8s-tools.sh)" >&2; exit 1; }
done

# One worker at a time: detection state is per-process, so Fargate must be scaled to 0.
running=$(aws ecs describe-services --cluster iiot --services iiot-worker --query 'services[0].desiredCount' --output text)
[ "$running" = "0" ] || { echo "Fargate worker desired_count=$running: scale it to 0 first (Run workflow)" >&2; exit 1; }

# 1. Cluster
kind get clusters | grep -qx iiot || kind create cluster --config k8s/kind-cluster.yaml
kubectl config use-context kind-iiot >/dev/null

# 2. Monitoring stack (pinned chart version)
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1 || \
  kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-user=admin \
    --from-literal=admin-password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community >/dev/null
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack --version 93.2.2 \
  --namespace monitoring -f k8s/monitoring/kube-prometheus-stack-values.yaml --wait --timeout 10m

# 3. The image Fargate runs now (from its task definition), loaded into the kind node
td=$(aws ecs describe-services --cluster iiot --services iiot-worker --query 'services[0].taskDefinition' --output text)
image=$(aws ecs describe-task-definition --task-definition "$td" \
  --query 'taskDefinition.containerDefinitions[?name==`worker`].image | [0]' --output text)
repo="${image%:*}"; tag="${image##*:}"
aws ecr get-login-password | docker login --username AWS --password-stdin "${repo%%/*}" >/dev/null
docker pull -q "$image" >/dev/null
kind load docker-image --name iiot "$image"

# 4. Credentials Secret (scoped role, 1 h); keep scripts/k8s-aws-creds.sh running afterwards
./scripts/k8s-aws-creds.sh --once

# 5. The worker
helm upgrade --install iiot-worker k8s/charts/iiot-worker --namespace iiot --create-namespace \
  --set image.repository="$repo" --set image.tag="$tag" \
  --set aws.queueUrl="$(tf telemetry_queue_url)" \
  --set aws.alertTopicArn="$(tf service_env | sed -n 's/^export ALERT_TOPIC_ARN=//p')" \
  --wait --timeout 5m

kubectl get pods -A -o wide | grep -E "NAMESPACE|iiot|monitoring"
cat <<EOF

Up. Next:
  ./scripts/k8s-aws-creds.sh                     # own terminal: keeps the pod's credentials fresh
  kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3001:80
  open http://localhost:3001  (dashboard "IIoT detection worker"; anonymous read-only)
Tear down: kind delete cluster --name iiot
EOF
