"""Code execution for the chat: one short-lived container per run (no network, read-only root, non-root, limits).

The backend prepares /data/sandbox/<run_id>/ (code, input files under in/, skill files under skills/<name>/),
engine-manager runs the container with that folder mounted as /work, and every new file the program writes there
(outside in/ and skills/) is saved to the user's drive under "Resultados de código".
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Skill, User, UserFile
from ..settings_store import get_setting
from . import files as files_svc
from . import orchestrator, storage
from . import sandbox_helpers as helpers
from .uploads import safe_name

OUT_FOLDER = "Resultados de código"
MAX_OUTPUT_FILES = 20
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_INPUT_BYTES = 300 * 1024 * 1024
MAX_KEPT_BYTES = 2 * 1024 * 1024 * 1024  # working files a conversation keeps between runs
SKIP_PARTS = {"in", "skills", ".sah", "__pycache__", ".cache", ".config", ".npm", ".local", "node_modules"}


class SandboxError(Exception):
    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.message, self.remediation = message, remediation


class SlotBusy(SandboxError):
    """The user already runs the maximum number of sandbox programs (callers may wait and retry)."""


class user_slot:
    """At most N sandbox programs per user at once (the engine runs 4 for everybody): one person cannot take them all.
    Each run is an entry with a timestamp, so a crashed process frees its slot after 20 minutes on its own."""

    def __init__(self, db: Session, user_id):
        self.key = f"sah:sandbox:user:{user_id}"
        self.limit = int(get_setting(db, "code_exec_per_user") or 2)
        self.member = uuid.uuid4().hex

    def __enter__(self):
        import time

        from ..redis_client import get_redis

        r = get_redis()
        now = time.time()
        r.zremrangebyscore(self.key, 0, now - 1200)
        if r.zcard(self.key) >= self.limit:
            raise SlotBusy(f"Ya tienes {self.limit} programas ejecutándose a la vez.",
                               "Espera a que terminen (o páralos) y vuelve a intentarlo.")
        r.zadd(self.key, {self.member: now})
        r.expire(self.key, 1200)
        return self

    def __exit__(self, *exc):
        from ..redis_client import get_redis

        get_redis().zrem(self.key, self.member)
        return False


def enabled(db: Session) -> bool:
    return bool(get_setting(db, "code_exec_enabled"))


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def _conv_dir(conversation_id) -> Path:
    return get_settings().data_root / "sandbox-conv" / uuid.UUID(str(conversation_id)).hex


def _kept_files(root: Path) -> dict[str, Path]:
    """Regular files a conversation keeps between runs. Symbolic links are never followed: a program could point one at
    any file of this machine. Inputs, skills and the program itself are written again on every run."""
    out: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in ("in", "skills", ".sah")]
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            p = Path(dirpath) / name
            if not p.is_symlink() and p.is_file() and not (rel_dir == Path(".") and name.startswith(".main.")):
                out[str(rel_dir / name)] = p
    return out


def restore_conversation_files(conversation_id, work: Path) -> set[str]:
    """Copy what earlier runs of this conversation left into a new run's /work. Returns the restored paths."""
    store = _conv_dir(conversation_id)
    if not store.is_dir():
        return set()
    files = _kept_files(store)
    for rel, src in files.items():
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    return set(files)


def keep_conversation_files(conversation_id, work: Path, restored: set[str], limit_bytes: int = MAX_KEPT_BYTES) -> bool:
    """Merge a finished run back into the conversation's folder: changed files are copied and only what this run deleted
    is removed, so agents running at once keep each other's files. False if it exceeds the limit (nothing is kept)."""
    store = _conv_dir(conversation_id)
    files = _kept_files(work)
    if sum(p.stat().st_size for p in files.values()) > limit_bytes:
        return False
    for rel, src in files.items():
        dst = store / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime != src.stat().st_mtime or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst, follow_symlinks=False)
    for rel in restored - set(files):
        (store / rel).unlink(missing_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    os.utime(store)  # last use, for purge_old_conversation_files
    return True


def forget_conversation_files(conversation_id) -> None:
    shutil.rmtree(_conv_dir(conversation_id), ignore_errors=True)


def purge_old_conversation_files(days: int = 30) -> int:
    """Conversations not used for a month lose their working files (the files given to the user stay in My files)."""
    root = get_settings().data_root / "sandbox-conv"
    limit, removed = time.time() - days * 86400, 0
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


def run(db: Session, user: User, *, code: str, language: str = "python", inputs: list[UserFile] | None = None,
        skills: list[Skill] | None = None, project_id: uuid.UUID | None = None, timeout_s: int | None = None,
        conversation_id=None) -> dict:
    """Run code and return {exit_code, stdout, stderr, timed_out, seconds, files: [public UserFile dicts]}.
    With `conversation_id`, /work keeps the files of the earlier runs of that conversation."""
    if not enabled(db):
        raise SandboxError("La ejecución de código está desactivada.", "Un administrador puede activarla en Ajustes → Sistema.")
    language = language if language in ("python", "bash", "node") else "python"
    rid = uuid.uuid4().hex
    work = get_settings().data_root / "sandbox" / rid
    (work / "in").mkdir(parents=True)
    try:
        total = 0
        for f in inputs or []:
            src = files_svc.abs_path(f)
            total += f.size_bytes
            if total > MAX_INPUT_BYTES:
                raise SandboxError("Los archivos de entrada superan 300 MB.")
            shutil.copyfile(src, work / "in" / safe_name(f.filename))
        for sk in skills or []:
            base = work / "skills" / sk.name
            base.mkdir(parents=True, exist_ok=True)
            (base / "SKILL.md").write_text(sk.content)
            for rel, text in (sk.files or {}).items():
                dst = (base / rel).resolve()
                if base.resolve() not in dst.parents:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text)
        restored = restore_conversation_files(conversation_id, work) if conversation_id else set()
        helpers.install(work / ".sah")
        (work / {"python": ".main.py", "bash": ".main.sh", "node": ".main.js"}[language]).write_text(helpers.with_path(code, language))
        before = _snapshot(work)
        limit = int(timeout_s or get_setting(db, "code_exec_timeout_s") or 120)
        try:
            with user_slot(db, user.id):
                res = orchestrator.run_sandbox({"run_id": rid, "language": language, "timeout_s": limit,
                                                "memory_mb": int(get_setting(db, "code_exec_memory_mb") or 4096)})
        except orchestrator.OrchestratorError as e:
            if getattr(e, "status", None) == 424 or "not present" in str(e):
                raise SandboxError("Falta la imagen del entorno de ejecución.",
                                   "En el servidor: scripts/sah.sh build-engines (o ./install.sh) y vuelve a intentarlo.")
            raise SandboxError(f"No se pudo ejecutar el código: {e}")
        kept = keep_conversation_files(conversation_id, work, restored) if conversation_id else None
        return {**res, "files": _collect(db, user, work, before, project_id), "kept": kept}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_raw(db: Session, user: User, *, script: str, files: dict[str, bytes], timeout_s: int = 120) -> dict:
    """Run a bash script with in-memory inputs (in/<name>) and return every new file as bytes (nothing is saved)."""
    rid = uuid.uuid4().hex
    work = get_settings().data_root / "sandbox" / rid
    (work / "in").mkdir(parents=True)
    try:
        for name, data in files.items():
            (work / "in" / safe_name(name)).write_bytes(data)
        (work / ".main.sh").write_text(script)
        before = _snapshot(work)
        try:
            res = orchestrator.run_sandbox({"run_id": rid, "language": "bash", "timeout_s": timeout_s,
                                            "memory_mb": int(get_setting(db, "code_exec_memory_mb") or 4096)})
        except orchestrator.OrchestratorError as e:
            raise SandboxError(f"No se pudo ejecutar la conversión: {e}")
        outputs = {rel: (work / rel).read_bytes() for rel, sig in _snapshot(work).items()
                   if before.get(rel) != sig and not rel.startswith(".main.") and (work / rel).stat().st_size < MAX_OUTPUT_BYTES}
        return {**res, "outputs": outputs}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def store_bytes(db: Session, user: User, filename: str, data: bytes, *, folder: str = OUT_FOLDER, project_id=None,
                generated_by: str = "code", commit: bool = True) -> dict | None:
    """Save generated bytes into the user's drive; returns the public file dict (None if the type is refused)."""
    try:
        kind, ext = files_svc.classify(filename, data[:64])
    except files_svc.FileError:
        return None
    storage.ensure_user_quota(db, user, len(data))
    fid = uuid.uuid4()
    dst = files_svc.user_dir(user.id) / f"{fid}{ext}"
    dst.write_bytes(data)
    f = UserFile(id=fid, owner_id=user.id, project_id=project_id, folder=folder,
                 filename=safe_name(Path(filename).stem) + ext, mime=files_svc.guess_mime(ext), kind=kind,
                 size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                 path=str(dst.relative_to(get_settings().data_root)), text_status="none", source="generated",
                 meta={"generated_by": generated_by})
    if kind == "image":
        try:
            from PIL import Image

            with Image.open(dst) as im:
                f.meta = {**f.meta, "width": im.width, "height": im.height}
        except Exception:  # noqa: BLE001
            pass
    db.add(f)
    if commit:
        db.commit()
    return files_svc.public(f)


def _collect(db: Session, user: User, work: Path, before: dict, project_id) -> list[dict]:
    out: list[dict] = []
    used = 0
    for rel, sig in sorted(_snapshot(work).items()):
        parts = Path(rel).parts
        if before.get(rel) == sig or parts[0] in SKIP_PARTS or parts[-1].startswith(".main.") or len(out) >= MAX_OUTPUT_FILES:
            continue
        p = work / rel
        size = p.stat().st_size
        if size == 0 or used + size > MAX_OUTPUT_BYTES:
            continue
        try:
            f = store_bytes(db, user, p.name, p.read_bytes(), project_id=project_id, commit=False)
        except storage.StorageError:
            break
        if f:
            used += size
            out.append(f)
    db.commit()
    return out
