"""Backup, restore verification and maintenance jobs."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from ...config import get_settings
from ...db import SessionLocal
from ...models import RegisteredModel
from ...settings_store import get_setting
from ..context import JobContext
from . import Handler, register

KEY_TABLES = ["users", "registered_models", "jobs", "benchmark_runs", "datasets", "experiments", "api_keys",
              "audit_events", "artifacts", "conversations"]


def _pg_env() -> tuple[dict, list[str]]:
    s = get_settings()
    url = s.effective_database_url.replace("postgresql+psycopg://", "postgresql://")
    from urllib.parse import urlparse

    u = urlparse(url)
    env = {**os.environ, "PGPASSWORD": u.password or ""}
    args = ["-h", u.hostname or "postgres", "-p", str(u.port or 5432), "-U", u.username or "sah"]
    return env, args + [u.path.lstrip("/")]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(16 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(db) -> dict:
    return {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in KEY_TABLES}  # noqa: S608 (fixed list)


@register
class Backup(Handler):
    type = "backup"

    def run(self, ctx: JobContext) -> None:
        s = get_settings()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = s.backups_dir / f"sah-backup-{ts}"
        dest.mkdir(parents=True)
        env, args = _pg_env()
        ctx.progress(0.1, "Dumping PostgreSQL", force=True)
        dump = dest / "database.dump"
        r = subprocess.run(["pg_dump", "-Fc", "-Z", "6", "-f", str(dump), *args[:-1], "-d", args[-1]],
                           env=env, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {r.stderr[-1500:]}")
        ctx.progress(0.4, "Exporting registry metadata", force=True)
        with SessionLocal() as db:
            counts = table_counts(db)
            alembic = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            models = [{"alias": m.alias, "source": m.source, "repo_id": m.repo_id, "revision": m.revision,
                       "engine_ref": m.engine_ref, "backend": m.backend, "size_bytes": m.size_bytes,
                       "license": m.license} for m in db.query(RegisteredModel).all()]
            include_user = bool(ctx.params.get("include_user_data", get_setting(db, "backup_include_user_data")))
            keep = int(get_setting(db, "backup_keep") or 7)
            external = get_setting(db, "backup_external_path")
        (dest / "models_registry.json").write_text(json.dumps(models, indent=2, default=str))
        cfg_src = s.data_root / "config"
        if cfg_src.exists():
            shutil.copytree(cfg_src, dest / "config", ignore=shutil.ignore_patterns("*.key", "*secret*", "*token*"))
        if include_user:
            ctx.progress(0.5, "Archiving user data (drives, workspaces, datasets, outputs, experiments)", force=True)
            skip_dirs = {"node_modules", ".local", ".cache", "__pycache__", ".venv"}

            def member(ti):
                parts = ti.name.split("/")
                if "/comfyui/" in ti.name or ti.name.startswith("outputs/mlworker") or skip_dirs & set(parts):
                    return None
                return None if ti.issym() or ti.islnk() or not (ti.isfile() or ti.isdir()) else ti
            with tarfile.open(dest / "user_data.tar.gz", "w:gz") as tar:
                for sub in ("users", "workspaces", "datasets", "outputs", "experiments", "benchmarks", "apps"):
                    p = s.data_root / sub
                    if p.exists():
                        tar.add(p, arcname=sub, filter=member)
                    ctx.raise_if_cancelled()
        ctx.progress(0.8, "Writing manifest and verifying", force=True)
        files = []
        for p in sorted(dest.rglob("*")):
            if p.is_file():
                files.append({"path": str(p.relative_to(dest)), "size": p.stat().st_size, "sha256": _sha(p)})
        manifest = {"format": "spark-ai-hub-backup/1", "created_at": ts, "app_version": s.version,
                    "alembic_revision": alembic, "table_counts": counts, "files": files,
                    "includes_user_data": include_user,
                    "excluded": ["model weights (re-downloadable: see models_registry.json)", "caches",
                                 "secrets directory (back up separately and securely)"]}
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
        lst = subprocess.run(["pg_restore", "--list", str(dump)], capture_output=True, text=True, timeout=600)
        if lst.returncode != 0:
            raise RuntimeError("Backup verification failed: pg_restore --list could not read the dump")
        copied_to = None
        if external:
            ext = Path("/backups-external") / Path(external).name
            if Path("/backups-external").is_dir():
                ext.mkdir(parents=True, exist_ok=True)
                shutil.copytree(dest, ext / dest.name)
                copied_to = str(ext / dest.name)
            else:
                ctx.log("External backup path configured but /backups-external is not mounted", "warning")
        # retention (local only)
        backups = sorted([p for p in s.backups_dir.glob("sah-backup-*") if p.is_dir()])
        for old in backups[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
        size = sum(f["size"] for f in files)
        ctx.set_result({"backup": dest.name, "size_bytes": size, "files": len(files), "verified": True,
                        "table_counts": counts, "external_copy": copied_to})


@register
class RestoreTest(Handler):
    """Restores a backup into a scratch database and compares row counts with the manifest."""

    type = "restore_test"

    def run(self, ctx: JobContext) -> None:
        s = get_settings()
        name = ctx.params["backup"]
        if "/" in name or not name.startswith("sah-backup-"):
            raise ValueError("invalid backup name")
        dest = s.backups_dir / name
        manifest = json.loads((dest / "manifest.json").read_text())
        ctx.progress(0.1, "Checking file hashes", force=True)
        for f in manifest["files"]:
            if _sha(dest / f["path"]) != f["sha256"]:
                raise RuntimeError(f"Checksum mismatch in {f['path']}")
        env, args = _pg_env()
        conn = args[:-1]
        scratch = "sah_restore_test"
        subprocess.run(["dropdb", "--if-exists", *conn, scratch], env=env, capture_output=True, timeout=120)
        r = subprocess.run(["createdb", *conn, scratch], env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"createdb failed: {r.stderr[-800:]}")
        try:
            ctx.progress(0.3, "Restoring into scratch database", force=True)
            r = subprocess.run(["pg_restore", "--no-owner", *conn, "-d", scratch, str(dest / "database.dump")],
                               env=env, capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise RuntimeError(f"pg_restore failed: {r.stderr[-1500:]}")
            counts = {}
            for t in KEY_TABLES:
                q = subprocess.run(["psql", *conn, "-d", scratch, "-tAc", f"SELECT count(*) FROM {t}"],
                                   env=env, capture_output=True, text=True, timeout=60)
                counts[t] = int(q.stdout.strip() or -1)
        finally:
            subprocess.run(["dropdb", "--if-exists", *conn, scratch], env=env, capture_output=True, timeout=120)
        mismatches = {t: (manifest["table_counts"].get(t), c) for t, c in counts.items()
                      if manifest["table_counts"].get(t) != c}
        ctx.set_result({"backup": name, "restored_counts": counts, "manifest_counts": manifest["table_counts"],
                        "ok": not mismatches, "mismatches": mismatches})
        if mismatches:
            raise RuntimeError(f"Restore test row-count mismatch: {mismatches}")
