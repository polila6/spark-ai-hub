# Storage

Detected: one Samsung MZALC4T0HBL1 NVMe (3.7 TB), ext4 root filesystem, 3.1 TB free at installation. No other disks.
Data root: **`/srv/spark-ai-hub`** (configurable with `SAH_DATA_ROOT`).

```
/srv/spark-ai-hub/
├── config/secrets/        Docker secrets (700/600) — back up separately, never in Git
├── database/              PostgreSQL 16 data (uid 999, 700)
├── models/
│   ├── hf/                shared Hugging Face cache (blobs + snapshots, revision-pinned)
│   ├── ollama/            Ollama store (separate from the host's /usr/share/ollama store, untouched)
│   ├── comfyui/{checkpoints,vae,…}  hard links into models/hf (no duplicated bytes)
│   └── custom/<user>/<model>/        Scientific Lab artifacts (safetensors + config.json)
├── datasets/<user>/<dataset>/        user datasets (private by default)
├── outputs/<user>/<job>/             generated images, video, audio, transcripts
├── experiments/<user>/<job>/         scientific predictions and metrics
├── jobs/  benchmarks/  logs/
├── cache/ {vllm (torch/triton compile caches), comfyui/{input,user,temp}, mlworker, hf-home}
└── backups/sah-backup-<UTC timestamp>/
```

## Policies

* **Shared weights**: public models are stored once in `models/hf`; ComfyUI sees them through hard links. Private custom
  models live under the owner's directory and are only readable through authorised API calls.
* **Gated models**: downloads use the admin-configured token only when needed; files are not exposed to users who lack
  access to the registry entry.
* **Disk reservation**: downloads reserve their total size; a download is refused when
  `free − reserved − storage_min_free_gb (200 GB default) < size`.
* **Quotas**: per-role/user storage quota for datasets + artifacts (researcher default 200 GB).
* **Garbage collection**: Almacenamiento → "Limpiar huérfanas" removes HF cache revisions that are neither registered nor
  being downloaded. Deleting a model removes its files only if no other registration references the same snapshot.
* **Never automatic**: user datasets, outputs and experiments are never deleted to free space. Retention jobs only prune
  job event logs, API usage metadata and metric samples (configurable days).
* `HF_XET_CHUNK_CACHE_SIZE_BYTES=0` avoids keeping a second copy of downloaded chunks on the same disk.

## Monitoring

Dashboard and Storage page show filesystem usage, download reservations, per-category and per-user usage. Alerts fire
above `storage_warn_percent` (85 %) or below the free-space threshold.

## External storage

Mount an NFS/SMB/USB destination on the host and enable `compose.backup.yaml` (see BACKUP_RECOVERY.md). Moving the
whole data root: stop the hub, `rsync -aHAX /srv/spark-ai-hub/ <new>/` (keep hard links with `-H`), set `SAH_DATA_ROOT`, start.
