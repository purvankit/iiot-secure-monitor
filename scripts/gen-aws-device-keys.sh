#!/usr/bin/env bash
# Generate each device's private key + CSR locally ("on the device").
# Only the CSR (public key + CN) goes to AWS IoT Core via Terraform; the private key
# never leaves this machine and never appears in Terraform state.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEYS="$ROOT/local/aws-certs"
CSRS="$ROOT/infra/envs/dev/device-csrs"
DEVICES=(feeder-01 feeder-02 xfmr-01 motor-01 edge-gw-01)

mkdir -p "$KEYS" "$CSRS"
chmod 700 "$KEYS"
for d in "${DEVICES[@]}"; do
  if [[ -f "$KEYS/$d.key" ]]; then echo "skip $d (key exists)"; continue; fi
  openssl ecparam -name prime256v1 -genkey -noout -out "$KEYS/$d.key"
  chmod 600 "$KEYS/$d.key"
  openssl req -new -key "$KEYS/$d.key" -subj "/CN=$d" -out "$CSRS/$d.csr"
  echo "key+csr $d"
done
