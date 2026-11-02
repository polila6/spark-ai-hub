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
gen searxng_secret     "python3 -c 'import secrets;print(secrets.token_hex(32))'"
if [[ ! -s "$DIR/admin_password" ]]; then
  (umask 077; python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("Sah-"+"".join(secrets.choice(a) for _ in range(16))+"9")' > "$DIR/admin_password")
  echo "created admin_password (initial password for user 'admin'): $(cat "$DIR/admin_password")"
  echo "Change it after first login. The file is only read when no users exist."
fi
chmod 600 "$DIR"/*

# SearXNG (chat web search): rendered from the template on every run (engine list updates reach existing installs),
# with its secret kept in config/secrets/searxng_secret. Rewritten in place so the file keeps its owner (uid 1000).
SX="$DATA_ROOT/config/searxng"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
install -d -m 750 "$SX"
NEW="$(sed "s/__SECRET__/$(cat "$DIR/searxng_secret")/" "$REPO/infrastructure/searxng/settings.yml")"
if [[ ! -s "$SX/settings.yml" ]]; then
  (umask 027; printf '%s\n' "$NEW" > "$SX/settings.yml")
  echo "created searxng/settings.yml"
elif [[ "$(cat "$SX/settings.yml")" != "$NEW" ]]; then
  printf '%s\n' "$NEW" > "$SX/settings.yml"
  echo "updated searxng/settings.yml (apply with: docker compose restart searxng)"
fi
