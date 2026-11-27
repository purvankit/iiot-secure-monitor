#!/usr/bin/env bash
# Download the ONE Grafana plugin the monitoring stack needs, pinned by version and SHA-256,
# into local/grafana-plugins/ (gitignored). Grafana's own startup installs are switched off in
# docker-compose.yml: by default Grafana 13 fetches ~20 unpinned plugins from grafana.com on
# every start. The plugin is also Grafana-signed; Grafana checks that signature when loading it.
# Usage (repo root): ./scripts/fetch-grafana-plugin.sh
set -euo pipefail

PLUGIN=grafana-amazonprometheus-datasource
VERSION=3.3.5   # released 2026-11-11
SHA256=da56d2a2285a1ba5ddb9def3bcb67934921ce6bfa52f9c1df9d241f3e188b792   # linux-amd64
URL="https://grafana.com/api/plugins/${PLUGIN}/versions/${VERSION}/download?os=linux&arch=amd64"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/local/grafana-plugins"
# Marker lives OUTSIDE the plugin folder: any extra file inside it breaks Grafana's signature check.
MARKER="$ROOT/local/.grafana-plugin-$PLUGIN-$VERSION"
if [ -f "$MARKER" ] && [ -d "$DEST/$PLUGIN" ]; then
  echo "skip: $PLUGIN $VERSION already present"; exit 0
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/plugin.zip" "$URL"
echo "$SHA256  $TMP/plugin.zip" | sha256sum -c --quiet - || { echo "CHECKSUM MISMATCH: refusing to install" >&2; exit 1; }

rm -rf "${DEST:?}/$PLUGIN"; mkdir -p "$DEST"
# Extract keeping Unix permissions (zipfile drops them; the backend binary must be executable).
python3 - "$TMP/plugin.zip" "$DEST" <<'PY'
import os, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for info in z.infolist():
        path = z.extract(info, sys.argv[2])
        mode = (info.external_attr >> 16) & 0o777
        if mode:
            os.chmod(path, mode)
PY
rm -f "$ROOT"/local/.grafana-plugin-"$PLUGIN"-*; touch "$MARKER"
echo "installed: $PLUGIN $VERSION (sha256 verified) -> local/grafana-plugins/$PLUGIN"
