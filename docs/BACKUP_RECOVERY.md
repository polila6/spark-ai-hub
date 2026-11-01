# Backup & recovery

## What is backed up

| Item | Included | Notes |
|---|---|---|
| PostgreSQL (all metadata: users, registry, jobs, benchmarks, experiments, keys, audit) | always | `pg_dump -Fc`, verified with `pg_restore --list` |
| `config/` (non-secret) | always | secrets excluded by pattern |
| Model registry export (`models_registry.json`: repo, **revision SHA**, engine ref, licence) | always | weights are re-downloadable at the exact revision |
| User data (`datasets/`, `outputs/`, `experiments/`, `benchmarks/`) | optional (toggle / `include_user_data`) | ComfyUI scratch and ML worker temp excluded |
| Model weights, caches | **no** | re-download from the registry export; private custom models are inside `models/custom` → back up that directory separately if it holds irreplaceable artifacts |
| Secrets (`config/secrets/`) | **no** | copy once to a secure offline location (password manager / encrypted USB). Without `encryption_key` the stored HF token cannot be decrypted; without `postgres_password` restore needs a password reset |

Each backup directory `sah-backup-<UTC>` contains `manifest.json` (format version, app version, Alembic revision,
row counts of key tables, sha256 + size of every file).

## Scheduling

Ajustes → Copias de seguridad: off/daily/weekly, hour (UTC), number kept (default 7, local retention only).
Manual: button "Crear copia ahora" or `scripts/sah.sh backup`.

## Off-host copies (required for disaster recovery)

Local backups share the NVMe with the data. Mount an external destination and enable the override:

```bash
sudo mount -t nfs <server>:/export/spark /mnt/spark-backups      # or SMB/USB
export SAH_BACKUP_EXTERNAL=/mnt/spark-backups
docker compose -f compose.yaml -f compose.backup.yaml up -d worker
# Ajustes → Copias de seguridad → "Subcarpeta externa": spark-ai-hub
```

## Integrity check / restore test

"Probar restauración" (or `POST /api/v1/admin/backups/<name>/restore-test`) verifies every sha256 in the manifest,
restores the dump into a scratch database `sah_restore_test`, compares row counts with the manifest and drops the
scratch DB. Verified on 2026-10-29 (TEST_REPORT §21).

## Restore (application failure, no OS reinstall)

```bash
cd ~/spark-ai-hub
B=/srv/spark-ai-hub/backups/sah-backup-YYYYMMDDTHHMMSSZ
scripts/sah.sh down
docker compose up -d postgres && sleep 10
docker compose exec -T postgres dropdb -U sah sah
docker compose exec -T postgres createdb -U sah sah
docker compose exec -T postgres pg_restore -U sah -d sah --no-owner < $B/database.dump
# optional user data
tar -xzf $B/user_data.tar.gz -C /srv/spark-ai-hub
scripts/sah.sh up            # backend applies newer migrations if the code is newer
```

Then: Biblioteca → Importar / re-install models listed in `models_registry.json` if weights were lost (exact revisions
are pinned), and run smoke tests.

## Full machine loss

New DGX Spark (or reinstalled OS) → follow DEPLOYMENT.md first installation, put the saved secrets in
`/srv/spark-ai-hub/config/secrets` **before** the first start, restore the DB as above, re-download models.
