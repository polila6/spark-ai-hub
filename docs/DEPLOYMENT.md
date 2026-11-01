# Deployment

## Prerequisites (verified on a DGX Spark)

DGX OS 7 (Ubuntu 24.04), Docker 27+ with Compose v2, NVIDIA Container Toolkit, and optionally Tailscale with HTTPS
certificates enabled for the tailnet. The operating user must be in the `docker` group.

## First installation (one command)

```bash
git clone https://github.com/polila6/spark-ai-hub ~/spark-ai-hub && cd ~/spark-ai-hub
./install.sh                    # add --with-studios for image/video/audio engines, --help for options
```

`install.sh` checks the requirements, writes `.env` (address from Tailscale when available), prepares
`/srv/spark-ai-hub` (asks for sudo once), generates secrets, pulls the vLLM image, builds and starts everything and
prints the admin login. Re-running it is safe.

### Manual steps (what the installer does)

```bash
cp .env.example .env && $EDITOR .env           # address, data root, docker gid, organisation name
sudo bash scripts/host-setup.sh                # /srv/spark-ai-hub tree (database owned by uid 999), Tailscale Serve
bash scripts/init-secrets.sh                   # secrets + initial admin password (config/secrets/admin_password)
docker pull nvcr.io/nvidia/vllm:26.09-py3      # ~26 GB
scripts/sah.sh build && scripts/sah.sh build-engines   # backend, frontend, engine-manager (+ ComfyUI, ML worker)
scripts/sah.sh up && scripts/sah.sh status
```

Open the address from `.env`, log in as `admin`, change the password (Settings → Change password).

## Configuration

`.env` (see `.env.example`) — data root, proxy port, public URL, docker gid, vLLM image. Runtime settings (scheduler,
quotas, thresholds, backups, features, HF token) are edited in **Ajustes → Sistema** and stored in Postgres.

## Start / stop / restart

```bash
scripts/sah.sh up            # docker compose up -d
scripts/sah.sh down          # stops on-demand engines (label sah.managed) and compose services; data kept
scripts/sah.sh restart worker
```

Restart policy `unless-stopped`: the hub comes back after a reboot of the DGX (Docker starts at boot). On-demand engines
are not restarted automatically; they are recreated on the next request.

## Upgrade

```bash
scripts/sah.sh backup                  # always back up first
git pull --ff-only
scripts/sah.sh build && scripts/sah.sh up   # Alembic migrations run on backend start
```

Engine upgrades (vLLM/ComfyUI/PyTorch) are explicit: change the pinned tag in `compose.yaml` / `compose.optional.yaml`,
rebuild, re-run smoke tests on installed models (Biblioteca → Prueba rápida) and record the result.

## Rollback

1. `git checkout <previous tag>`; `scripts/sah.sh build && scripts/sah.sh up`.
2. If the database schema changed, restore the backup taken before the upgrade (docs/BACKUP_RECOVERY.md §Restore).
3. Model files are unaffected (weights live in `/srv/spark-ai-hub/models`).

## Removing Tailscale Serve / the hub

```bash
tailscale serve --https=443 off       # stop HTTPS exposure (tailnet-only anyway)
scripts/sah.sh down
# data remains in /srv/spark-ai-hub until deleted deliberately (after backing it up)
```

## Exposure check

```bash
ss -tlnp | grep -E ':(8080|5432|6379|11434|8000|9000|8188)'   # expect only 127.0.0.1:8080
tailscale serve status                                         # expect "(tailnet only)", never Funnel
```
