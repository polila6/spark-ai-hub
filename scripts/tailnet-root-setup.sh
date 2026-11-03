#!/usr/bin/env bash
# One-time root step so Tailscale changes need no sudo again: logging in with another account clears the "operator"
# permission, without which scripts/tailnet-sync.py (run as your user) cannot re-create Tailscale Serve.
# Installs a root timer that only runs `tailscale set --operator=<user>`: it runs the tailscale binary directly, never a
# script from this repository, because those files are writable without root.
#   sudo bash scripts/tailnet-root-setup.sh [user]      Undo: systemctl disable --now sah-tailscale-operator.timer
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run: sudo bash $0" >&2; exit 1; }
OWNER="${1:-${SUDO_USER:-}}"
[[ "$OWNER" =~ ^[a-z_][a-z0-9_-]*$ ]] && id "$OWNER" >/dev/null 2>&1 || { echo "Usage: sudo bash $0 <user>" >&2; exit 1; }
TS="$(command -v tailscale)"
cat > /etc/systemd/system/sah-tailscale-operator.service <<UNIT
[Unit]
Description=Spark AI Hub: let ${OWNER} manage tailscale serve (lost when the Tailscale account changes)
After=tailscaled.service

[Service]
Type=oneshot
ExecStart=${TS} set --operator=${OWNER}
UNIT
cat > /etc/systemd/system/sah-tailscale-operator.timer <<'UNIT'
[Unit]
Description=Spark AI Hub: keep the tailscale operator set

[Timer]
OnBootSec=15s
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now sah-tailscale-operator.timer
loginctl enable-linger "$OWNER"   # the sync timer also runs before anyone logs in
"$TS" set --operator="$OWNER"
echo "Done: ${OWNER} can manage tailscale serve, also after future account changes."
