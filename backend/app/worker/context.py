"""Execution context handed to job handlers."""
from __future__ import annotations

import hashlib
import mimetypes
import time
import uuid
from pathlib import Path

from ..config import get_settings
from ..db import SessionLocal
from ..models import Artifact, Job, Visibility
from ..redis_client import publish
from ..services.jobs import add_event

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("text/plain", ".srt")


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: Job):
        self.job_id: uuid.UUID = job.id
        self.owner_id = job.owner_id
        self.params: dict = dict(job.params or {})
        self.type = job.type
        self._last_progress = 0.0
        self._last_cancel_check = 0.0
        self._cancelled = False
        self.actual: dict = {}

    # -- progress / logs
    def progress(self, value: float, message: str | None = None, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_progress < 1.0:
            return
        self._last_progress = now
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            job.progress = max(0.0, min(1.0, value))
            if message is not None:
                job.message = message[:2000]
            db.commit()
        publish("sah:job-events", {"job_id": str(self.job_id), "progress": value, "message": message})

    def log(self, message: str, level: str = "info", data: dict | None = None) -> None:
        with SessionLocal() as db:
            add_event(db, self.job_id, message, level, data)
            db.commit()

    def set_result(self, result: dict) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            job.result = result
            db.commit()

    # -- cancellation
    def cancelled(self) -> bool:
        if self._cancelled:
            return True
        now = time.time()
        if now - self._last_cancel_check < 1.0:
            return False
        self._last_cancel_check = now
        with SessionLocal() as db:
            self._cancelled = bool(db.get(Job, self.job_id).cancel_requested)
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled()

    # -- files
    def job_dir(self) -> Path:
        d = get_settings().jobs_dir / str(self.job_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_artifact(self, path: Path, kind: str, meta: dict | None = None,
                     visibility: Visibility = Visibility.private) -> dict:
        s = get_settings()
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8 << 20), b""):
                h.update(chunk)
        rel = str(path.resolve().relative_to(s.data_root.resolve()))
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with SessionLocal() as db:
            a = Artifact(owner_id=self.owner_id, job_id=self.job_id, kind=kind, path=rel, filename=path.name,
                         mime=mime, size_bytes=path.stat().st_size, sha256=h.hexdigest(), meta=meta or {},
                         visibility=visibility)
            db.add(a)
            db.commit()
            return {"id": str(a.id), "filename": a.filename, "mime": a.mime, "size_bytes": a.size_bytes,
                    "kind": kind, "url": f"/api/v1/media/artifacts/{a.id}/file"}
