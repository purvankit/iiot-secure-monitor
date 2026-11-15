#!/usr/bin/env bash
# Generate a throwaway local CA plus one X.509 cert per identity for the local
# Mosquitto broker. Mirrors AWS IoT Core: every device has its own key pair and
# certificate, and the certificate's CN is the device identity.
#
# LOCAL TESTING ONLY. In AWS (M2) certificates are issued by IoT Core via Terraform.
set -euo pipefail

OUT="${1:-$(dirname "$0")/../local/certs}"
DAYS=365
DEVICES=(feeder-01 feeder-02 xfmr-01 motor-01 edge-gw-01)
CLIENTS=("${DEVICES[@]}" monitor)

mkdir -p "$OUT"
cd "$OUT"

if [[ ! -f ca.key ]]; then
  openssl ecparam -name prime256v1 -genkey -noout -out ca.key
  openssl req -x509 -new -key ca.key -sha256 -days "$DAYS" -subj "/CN=iiot-local-ca" -out ca.crt
fi

issue() {  # issue <cn> <extfile-content>
  local cn="$1" ext="$2"
  [[ -f "$cn.crt" ]] && return
  openssl ecparam -name prime256v1 -genkey -noout -out "$cn.key"
  openssl req -new -key "$cn.key" -subj "/CN=$cn" -out "$cn.csr"
  openssl x509 -req -in "$cn.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 \
    -days "$DAYS" -extfile <(printf '%s\n' "$ext") -out "$cn.crt" 2>/dev/null
  rm -f "$cn.csr"
  echo "issued $cn"
}

issue mosquitto "subjectAltName=DNS:mosquitto,DNS:localhost,IP:127.0.0.1
extendedKeyUsage=serverAuth"
for c in "${CLIENTS[@]}"; do
  issue "$c" "extendedKeyUsage=clientAuth"
done

# Containers run as non-root (mosquitto uid 1883, simulator uid 10001): keys must be readable.
chmod 644 ./*.key ./*.crt
echo "certs in $(pwd)"
