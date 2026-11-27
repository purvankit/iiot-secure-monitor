#!/usr/bin/env bash
# Keep local Grafana supplied with AWS credentials from your `aws login` session.
#
# `aws login` credentials rotate about every 15 minutes, so handing Grafana a one-off copy
# makes it go blank after ~15 min. Instead, Grafana's AWS config uses
#   credential_process = cat /aws/creds.json
# which the AWS SDK re-runs whenever its credentials expire, and this loop rewrites
# local/grafana-aws/creds.json from the live session every 60 s.
# Usage (repo root, leave running in its own terminal): ./scripts/grafana-aws-creds.sh
set -euo pipefail
PROFILE="${AWS_PROFILE:-iiot}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/local/grafana-aws"
# 755: the Grafana container user (uid 472) must read it; your home dir (750) keeps other
# local users out. The credentials are temporary (~15 min) and never leave the laptop.
mkdir -p "$DIR" && chmod 755 "$DIR"
cat > "$DIR/config" <<CFG
[profile grafana]
region = ap-south-1
credential_process = cat /aws/creds.json
CFG
echo "writing $DIR/creds.json every 60 s from profile '$PROFILE' (Ctrl+C to stop)"
while true; do
  TMP="$(mktemp "$DIR/.creds.XXXXXX")"
  if aws configure export-credentials --profile "$PROFILE" --format process > "$TMP" 2>/dev/null; then
    chmod 644 "$TMP" && mv -f "$TMP" "$DIR/creds.json"   # atomic: Grafana never reads half a file
  else
    rm -f "$TMP"; echo "$(date -u +%T) could not export credentials: run 'aws login --profile $PROFILE'" >&2
  fi
  sleep 60
done
