#!/usr/bin/env bash
# Give the kind worker pod AWS credentials of the scoped iiot-worker-local role (S1).
#
# Assumes the role (1-hour session) with your `aws login` identity and stores the result in
# the Kubernetes Secret the pod mounts read-only at /aws. The pod's botocore reads it through
#   credential_process = cat /aws/creds.json
# and re-runs that when the credentials expire; this loop renews them every 10 minutes, so
# there are always ~50 minutes left. The kubelet syncs Secret updates into the pod by itself.
#
# Usage (repo root): ./scripts/k8s-aws-creds.sh          # keep running in its own terminal
#                    ./scripts/k8s-aws-creds.sh --once   # just create/refresh the Secret once
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-iiot}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=iiot
SECRET=iiot-worker-aws

ROLE_ARN="$(terraform -chdir="$ROOT/infra/envs/dev" output -raw local_worker_role_arn)"
[ -n "$ROLE_ARN" ] && [ "$ROLE_ARN" != "null" ] || { echo "no local_worker_role_arn output: apply infra/envs/dev first" >&2; exit 1; }

refresh() {
  local tmp; tmp="$(mktemp -d)"
  aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name iiot-kind-worker \
      --duration-seconds 3600 --region ap-south-1 --output json \
    | python3 -c '
import json, sys
c = json.load(sys.stdin)["Credentials"]
print(json.dumps({"Version": 1, "AccessKeyId": c["AccessKeyId"], "SecretAccessKey": c["SecretAccessKey"],
                  "SessionToken": c["SessionToken"], "Expiration": c["Expiration"]}))' > "$tmp/creds.json"
  printf '[profile worker]\nregion = ap-south-1\ncredential_process = cat /aws/creds.json\n' > "$tmp/config"
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n "$NS" create secret generic "$SECRET" --from-file="$tmp/config" --from-file="$tmp/creds.json" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  rm -rf "$tmp"
  echo "$(date -u +%T) Secret $NS/$SECRET refreshed (role session valid 1 h)"
}

refresh
[ "${1:-}" = "--once" ] && exit 0
while true; do
  sleep 600
  refresh || echo "$(date -u +%T) refresh failed (expired aws login? run: aws login --profile $AWS_PROFILE)" >&2
done
