#!/usr/bin/env bash
# Spark AI Hub — one-time host preparation. Run as: sudo bash scripts/host-setup.sh
# Idempotent. Does not touch SSH, ufw, drivers, existing containers or Tailscale ACLs.
set -euo pipefail

OWNER="${SAH_OWNER:-${SUDO_USER:-$(logname 2>/dev/null || echo "$USER")}}"
DATA_ROOT="${SAH_DATA_ROOT:-/srv/spark-ai-hub}"
PROXY_PORT="${SAH_PROXY_PORT:-8080}"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo" >&2; exit 1; fi

echo "[1/3] Creating data hierarchy in ${DATA_ROOT} (owner ${OWNER})"
for d in config database models datasets outputs experiments benchmarks jobs logs cache backups; do
  install -d -m 750 -o "${OWNER}" -g "${OWNER}" "${DATA_ROOT}/${d}"
done
chown "${OWNER}:${OWNER}" "${DATA_ROOT}"; chmod 750 "${DATA_ROOT}"
# PostgreSQL container runs as uid/gid 999 and requires a private data directory.
chown 999:999 "${DATA_ROOT}/database"; chmod 700 "${DATA_ROOT}/database"
# Postgres and Ollama containers run with their own uids and need traverse rights on the root.
chmod o+x "${DATA_ROOT}"

if ! command -v tailscale >/dev/null || [[ "${SAH_SKIP_TAILSCALE:-0}" == 1 ]]; then
  echo "[2/3] Tailscale not found (or skipped): the hub stays on http://127.0.0.1:${PROXY_PORT} only."
  echo "      Install Tailscale and re-run this script to reach it from your other devices over HTTPS."
  exit 0
fi
echo "[2/3] Allowing ${OWNER} to manage 'tailscale serve' (tailscale set --operator)"
tailscale set --operator="${OWNER}"

echo "[3/3] Configuring tailnet-only HTTPS: https:443 -> http://127.0.0.1:${PROXY_PORT} (no Funnel)"
tailscale serve --bg --https=443 "http://127.0.0.1:${PROXY_PORT}"
tailscale serve status

echo "Done. Rollback: tailscale serve --https=443 off; tailscale set --operator=; (data kept in ${DATA_ROOT})"
