"""Storage accounting: free space, download reservations, per-user quotas, safe paths."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Artifact, Dataset, DownloadStatus, DownloadTask, ResourcePolicy, Role, User, UserFile
from ..settings_store import get_setting

GB = 1e9


class StorageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def safe_join(base: Path, *parts: str) -> Path:
    """Join untrusted path components under base, refusing traversal and symlink escapes."""
    base = base.resolve()
    p = base.joinpath(*parts).resolve()
    if p != base and base not in p.parents:
        raise StorageError("path_traversal", "Path escapes the allowed directory")
    return p


def fs_stats() -> dict:
    s = get_settings()
    st = os.statvfs(s.data_root)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return {"total_bytes": total, "free_bytes": free, "used_bytes": total - st.f_bfree * st.f_frsize}


def reserved_download_bytes(db: Session) -> int:
    rows = db.execute(select(DownloadTask.reserved_bytes, DownloadTask.downloaded_bytes).where(
        DownloadTask.status.in_([DownloadStatus.pending, DownloadStatus.running, DownloadStatus.paused]))).all()
    return sum(max(0, r - d) for r, d in rows)


def available_for_downloads(db: Session) -> int:
    min_free = float(get_setting(db, "storage_min_free_gb") or 0) * GB
    return int(fs_stats()["free_bytes"] - reserved_download_bytes(db) - min_free)


def ensure_download_space(db: Session, needed: int) -> None:
    avail = available_for_downloads(db)
    if needed > avail:
        raise StorageError(
            "insufficient_disk",
            f"Download needs {needed / GB:.1f} GB but only {max(avail, 0) / GB:.1f} GB are available above the "
            f"configured safety threshold",
        )


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                st = os.lstat(os.path.join(root, f))
                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
            except OSError:
                pass
    return total


def user_usage_bytes(db: Session, user_id: uuid.UUID) -> int:
    ds = db.scalar(select(func.coalesce(func.sum(Dataset.size_bytes), 0)).where(Dataset.owner_id == user_id)) or 0
    ar = db.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(Artifact.owner_id == user_id)) or 0
    uf = db.scalar(select(func.coalesce(func.sum(UserFile.size_bytes), 0)).where(UserFile.owner_id == user_id)) or 0
    return int(ds + ar + uf)


def effective_policy(db: Session, user: User) -> ResourcePolicy:
    row = db.scalar(select(ResourcePolicy).where(ResourcePolicy.scope == "user", ResourcePolicy.target == str(user.id)))
    if row:
        return row
    row = db.scalar(select(ResourcePolicy).where(ResourcePolicy.scope == "role", ResourcePolicy.target == user.role.value))
    if row:
        return row
    defaults = {Role.admin: dict(max_concurrent_jobs=4, max_queued_jobs=100, storage_quota_gb=2000, api_rpm=600),
                Role.manager: dict(max_concurrent_jobs=3, max_queued_jobs=50, storage_quota_gb=500, api_rpm=300),
                Role.researcher: dict(max_concurrent_jobs=2, max_queued_jobs=20, storage_quota_gb=200, api_rpm=60),
                Role.viewer: dict(max_concurrent_jobs=0, max_queued_jobs=0, storage_quota_gb=1, api_rpm=20)}
    return ResourcePolicy(scope="role", target=user.role.value, allow_video=user.role != Role.viewer,
                          monthly_token_quota=None, priority_boost=0, **defaults[user.role])


def ensure_user_quota(db: Session, user: User, extra_bytes: int) -> None:
    pol = effective_policy(db, user)
    used = user_usage_bytes(db, user.id)
    if used + extra_bytes > pol.storage_quota_gb * GB:
        raise StorageError("quota_exceeded",
                           f"Storage quota exceeded: {used / GB:.2f} GB used of {pol.storage_quota_gb:.0f} GB")


def ensure_layout() -> None:
    s = get_settings()
    for p in (s.hf_cache_dir, s.custom_models_dir, s.models_dir / "comfyui" / "checkpoints",
              s.models_dir / "comfyui" / "vae", s.models_dir / "comfyui" / "loras",
              s.models_dir / "comfyui" / "upscale_models", s.models_dir / "comfyui" / "text_encoders",
              s.models_dir / "comfyui" / "diffusion_models", s.models_dir / "comfyui" / "clip_vision",
              s.models_dir / "ollama", s.datasets_dir, s.outputs_dir / "comfyui", s.outputs_dir / "mlworker",
              s.experiments_dir, s.benchmarks_dir, s.jobs_dir, s.backups_dir, s.data_root / "cache" / "vllm",
              s.data_root / "cache" / "comfyui" / "input", s.data_root / "cache" / "comfyui" / "user",
              s.data_root / "cache" / "comfyui" / "temp", s.data_root / "cache" / "mlworker",
              s.data_root / "config", s.data_root / "logs", s.data_root / "users"):
        p.mkdir(parents=True, exist_ok=True)
