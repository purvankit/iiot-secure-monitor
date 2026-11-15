#!/usr/bin/env bash
# After `terraform apply`: write the signed device certs and Amazon's root CA next to
# the local private keys, and print the IoT endpoint for the simulator.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/local/aws-certs"
cd "$ROOT/infra/envs/dev"

terraform output -json device_certificate_pems | python3 -c '
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
for dev, pem in json.load(sys.stdin).items():
    (out / f"{dev}.crt").write_text(pem)
    print("cert", dev)
' "$OUT"

# Pin trust to Amazon Root CA 1 only (IoT Core ATS endpoint), not the whole OS trust store.
curl -fsS https://www.amazontrust.com/repository/AmazonRootCA1.pem -o "$OUT/ca.crt"
echo "ca.crt (AmazonRootCA1)"
echo "MQTT_HOST=$(terraform output -raw iot_endpoint)"
