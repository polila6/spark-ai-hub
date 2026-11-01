#!/usr/bin/env bash
# Generates Spark AI Hub secrets once (idempotent: existing files are kept).
set -euo pipefail
DATA_ROOT="${SAH_DATA_ROOT:-/srv/spark-ai-hub}"
DIR="$DATA_ROOT/config/secrets"
install -d -m 700 "$DIR"
gen() { [[ -s "$DIR/$1" ]] || { (umask 077; eval "$2" > "$DIR/$1"); echo "created $1"; }; }
gen postgres_password  "python3 -c 'import secrets;print(secrets.token_urlsafe(32))'"
gen session_secret     "python3 -c 'import secrets;print(secrets.token_urlsafe(48))'"
gen encryption_key     "python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'"
gen orchestrator_token "python3 -c 'import secrets;print(secrets.token_urlsafe(32))'"
if [[ ! -s "$DIR/admin_password" ]]; then
  (umask 077; python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("Sah-"+"".join(secrets.choice(a) for _ in range(16))+"9")' > "$DIR/admin_password")
  echo "created admin_password (initial password for user 'admin'): $(cat "$DIR/admin_password")"
  echo "Change it after first login. The file is only read when no users exist."
fi
chmod 600 "$DIR"/*

# SearXNG (chat web search): settings with its own random secret, outside git.
SX="$DATA_ROOT/config/searxng"
if [[ ! -s "$SX/settings.yml" ]]; then
  install -d -m 750 "$SX"
  REPO="$(cd "$(dirname "$0")/.." && pwd)"
  (umask 027; sed "s/__SECRET__/$(python3 -c 'import secrets;print(secrets.token_hex(32))')/" \
    "$REPO/infrastructure/searxng/settings.yml" > "$SX/settings.yml")
  echo "created searxng/settings.yml"
fi
