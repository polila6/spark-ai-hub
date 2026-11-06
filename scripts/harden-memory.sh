#!/usr/bin/env bash
# Keep a memory crisis from freezing the Spark for hours (needs root; changes nothing unless you pass --apply).
#
#   scripts/harden-memory.sh             show what would change (default, safe)
#   sudo scripts/harden-memory.sh --apply
#   sudo scripts/harden-memory.sh --revert
#
# On this machine the GPU memory is the system memory. When loading a big model leaves almost nothing free, the NVIDIA driver
# cannot allocate, tasks block and the whole box stops answering (network and Tailscale included) until somebody power-cycles
# it. What this does:
#   * vm.min_free_kbytes = 4 GiB      the kernel keeps real headroom for the driver instead of running at ~0 free
#   * hung tasks -> panic -> reboot   a task blocked for 5 minutes now reboots the machine 30 s later instead of waiting hours
#   * systemd hardware watchdog       if even systemd stops answering for 2 minutes, the board resets the machine
# After a reboot the hub starts by itself; a model that died for lack of memory is no longer re-loaded automatically.
set -euo pipefail

SYSCTL=/etc/sysctl.d/90-sah-memory.conf
WATCHDOG=/etc/systemd/system.conf.d/90-sah-watchdog.conf
MODE="${1:---dry-run}"

SYSCTL_BODY='# Spark AI Hub: survive memory crises (see scripts/harden-memory.sh)
vm.min_free_kbytes = 4194304
kernel.hung_task_timeout_secs = 300
kernel.hung_task_panic = 1
kernel.panic = 30
'
WATCHDOG_BODY='# Spark AI Hub: reset the machine if systemd stops answering (see scripts/harden-memory.sh)
[Manager]
RuntimeWatchdogSec=120
RebootWatchdogSec=10min
'

show() {
  echo "Current: min_free_kbytes=$(cat /proc/sys/vm/min_free_kbytes) hung_task_panic=$(cat /proc/sys/kernel/hung_task_panic) panic=$(cat /proc/sys/kernel/panic)"
  echo "Watchdog device: $(ls /dev/watchdog 2>/dev/null || echo 'none (the watchdog part is skipped)')"
  echo; echo "--- $SYSCTL"; printf '%s' "$SYSCTL_BODY"
  echo; echo "--- $WATCHDOG"; printf '%s' "$WATCHDOG_BODY"
}

case "$MODE" in
  --dry-run|-n) show; echo; echo "Nothing changed. Run: sudo $0 --apply" ;;
  --apply)
    [[ $EUID -eq 0 ]] || { echo "Run it with sudo." >&2; exit 1; }
    printf '%s' "$SYSCTL_BODY" > "$SYSCTL"
    sysctl -q -p "$SYSCTL"
    if [[ -e /dev/watchdog ]]; then
      mkdir -p "$(dirname "$WATCHDOG")"; printf '%s' "$WATCHDOG_BODY" > "$WATCHDOG"
      systemctl daemon-reexec
    fi
    echo "Applied. Now: min_free_kbytes=$(cat /proc/sys/vm/min_free_kbytes) hung_task_panic=$(cat /proc/sys/kernel/hung_task_panic) panic=$(cat /proc/sys/kernel/panic)" ;;
  --revert)
    [[ $EUID -eq 0 ]] || { echo "Run it with sudo." >&2; exit 1; }
    rm -f "$SYSCTL" "$WATCHDOG"
    sysctl -q vm.min_free_kbytes=45167 kernel.hung_task_panic=0 kernel.panic=0 kernel.hung_task_timeout_secs=120 || true
    systemctl daemon-reexec
    echo "Reverted to the stock values." ;;
  *) echo "usage: $0 [--dry-run|--apply|--revert]" >&2; exit 2 ;;
esac
