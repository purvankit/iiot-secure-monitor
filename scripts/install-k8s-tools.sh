#!/usr/bin/env bash
# Install the S1 tools into ~/.local/bin (no sudo), pinned by version AND SHA-256.
# A download whose checksum doesn't match the published one is refused and nothing is installed.
#   kind  v0.33.0 (2026-08-26)  checksum = GitHub asset digest = kind's .sha256sum file
#   helm  v3.22.1 (2026-10-15)  checksum = get.helm.sh .sha256sum file
# kubectl comes with Docker Desktop. Usage (repo root): ./scripts/install-k8s-tools.sh
set -euo pipefail

BIN="$HOME/.local/bin"
KIND_VERSION=v0.33.0
KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
HELM_VERSION=v3.22.1
HELM_SHA256=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea

mkdir -p "$BIN"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

verify() {  # verify <file> <sha256>
  echo "$2  $1" | sha256sum -c --quiet - || { echo "CHECKSUM MISMATCH for $1: refusing to install" >&2; exit 1; }
}

if [ "$(kind version 2>/dev/null | awk '{print $2}')" = "$KIND_VERSION" ]; then
  echo "skip: kind $KIND_VERSION already installed"
else
  curl -fsSL -o "$TMP/kind" "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-linux-amd64"
  verify "$TMP/kind" "$KIND_SHA256"
  install -m 0755 "$TMP/kind" "$BIN/kind"
  echo "installed: kind $KIND_VERSION (sha256 verified)"
fi

if helm version --short 2>/dev/null | grep -q "^$HELM_VERSION+"; then
  echo "skip: helm $HELM_VERSION already installed"
else
  curl -fsSL -o "$TMP/helm.tgz" "https://get.helm.sh/helm-$HELM_VERSION-linux-amd64.tar.gz"
  verify "$TMP/helm.tgz" "$HELM_SHA256"
  tar -xzf "$TMP/helm.tgz" -C "$TMP" linux-amd64/helm
  install -m 0755 "$TMP/linux-amd64/helm" "$BIN/helm"
  echo "installed: helm $HELM_VERSION (sha256 verified)"
fi

kind version
helm version --short
