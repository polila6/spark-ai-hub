#!/usr/bin/env bash
# Spark AI Hub operations helper.  Usage: scripts/sah.sh <command>
#   status | up | down | restart [svc] | logs [svc] | build | build-engines | backup | reset-link <user> | update | test | unit | tailnet-install
set -euo pipefail
cd "$(dirname "$0")/.."
DC="docker compose"
DATA="${SAH_DATA_ROOT:-$(sed -n 's/^SAH_DATA_ROOT=\([^ #]*\).*/\1/p' .env 2>/dev/null)}"; DATA="${DATA:-/srv/spark-ai-hub}"
dirs() { mkdir -p "$DATA/run/pkgproxy" "$DATA/workspaces" "$DATA/sandbox" 2>/dev/null || true; }
case "${1:-status}" in
  status)   $DC ps --format 'table {{.Service}}\t{{.Status}}'; docker ps -a --filter name=sah- --format 'table {{.Names}}\t{{.Status}}';
            curl -s https://"$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"/api/v1/system/health; echo ;;
  up)       dirs; $DC up -d ;;
  down)     # Stops the hub. On-demand engine containers (sah-*) are stopped too. Data is kept.
            docker ps -q --filter label=sah.managed=true | xargs -r docker stop; $DC down ;;
  restart)  shift; $DC restart "$@" ;;
  tailnet-install)  # follow Tailscale changes (address, account) automatically: every minute, also before anyone logs in
            UD="$HOME/.config/systemd/user"; mkdir -p "$UD"
            printf '[Unit]\nDescription=Spark AI Hub: follow this machine'"'"'s Tailscale identity\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 %s/scripts/tailnet-sync.py\n' "$PWD" > "$UD/sah-tailnet-sync.service"
            printf '[Unit]\nDescription=Spark AI Hub: Tailscale sync every minute\n\n[Timer]\nOnBootSec=20s\nOnUnitActiveSec=60s\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n' > "$UD/sah-tailnet-sync.timer"
            systemctl --user daemon-reload && systemctl --user enable --now sah-tailnet-sync.timer
            loginctl enable-linger "$USER" 2>/dev/null || echo "Run once: sudo loginctl enable-linger $USER (so it also runs before anyone logs in)"
            python3 scripts/tailnet-sync.py; python3 scripts/tailnet-sync.py --status ;;
  logs)     shift; $DC logs -f --tail 200 "$@" ;;
  build)    $DC build && $DC -f compose.yaml -f compose.optional.yaml --profile sandbox build sandbox ;;
  build-engines) $DC -f compose.yaml -f compose.optional.yaml --profile engines --profile sandbox build comfyui mlworker sandbox ;;
  backup)   $DC exec -T backend python - <<'PY'
from app.db import SessionLocal
from app.models import Role, SchedClass, User
from app.services.jobs import create_job
with SessionLocal() as db:
    admin = db.query(User).filter(User.role == Role.admin, User.is_active.is_(True)).first()
    j = create_job(db, admin, "backup", {"include_user_data": True}, title="CLI backup", sched_class=SchedClass.maintenance)
    print("backup job", j.id)
PY
            ;;
  reset-link)  # Emergency one-time password reset link for a user (e.g. lost admin password). Requires host shell access.
            $DC exec -T -e U="${2:?usage: sah.sh reset-link <username>}" backend python - <<'PY'
import os
from datetime import datetime, timedelta, timezone
from app.config import get_settings
from app.db import SessionLocal
from app.models import Invitation, User
from app.security import new_token, token_digest
with SessionLocal() as db:
    u = db.query(User).filter(User.username == os.environ["U"]).one()
    t = new_token()
    db.add(Invitation(token_hash=token_digest(t), kind="reset", user_id=u.id,
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    u.locked_until = None; u.failed_logins = 0
    db.commit()
    print(f"{get_settings().public_base_url}/invite/{t}  (valid 1 h)")
PY
            ;;
  update)   git pull --ff-only && dirs && $DC build && $DC -f compose.yaml -f compose.optional.yaml --profile sandbox build sandbox && $DC up -d && "$0" tailnet-install ;;   # migrations run automatically on backend start
  unit)     docker run --rm --user 1000:1000 -e HOME=/tmp -v "$PWD/backend:/app" -w /app spark-ai-hub/backend:"${SAH_VERSION:-0.1.0}" \
              sh -c "pip install -q --user pytest==9.2.0 && python -m pytest -q tests" ;;
  test)     .venv-tests/bin/pytest -v tests/acceptance ;;
  *) echo "unknown command"; exit 1 ;;
esac
