"""Workspaces: a persistent folder per user and workspace, edited from the browser and by the assistant, where commands
run in the no-network sandbox (engine-manager mounts it as /work).

Files written by sandboxed programs are untrusted: every access resolves the path inside the workspace, refuses
symbolic links anywhere on the way and only reads regular files (no FIFOs or devices)."""
from __future__ import annotations

import io
import mimetypes
import os
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User, UserFile, Workspace
from ..settings_store import get_setting
from . import files as files_svc
from . import orchestrator
from .sandbox import SandboxError, store_bytes

MAX_TEXT_READ = 2 * 1024 * 1024
MAX_TREE = 3000
HIDDEN = {".git", "__pycache__", "node_modules", ".venv", ".cache", ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache",
          ".local"}
TEXT_EXT = files_svc.DOC_EXT | files_svc.CODE_EXT | {".csv", ".tsv", ".json", ".jsonl", ".txt", ".css", ".svg", ".env.example",
                                                      ".gitignore", ".dockerfile", ".mk", ".cmake", ".bib", ".tex"}
WELCOME = """# Bienvenido a tu workspace

Este es tu espacio de trabajo remoto en el Spark. Todo lo que hay aquí se conserva.

- **Archivos**: sube datos (CSV, Excel, PDF…) o crea archivos nuevos.
- **Terminal**: ejecuta comandos (`python script.py`, `ls`, `pandoc …`). Sin internet; Python 3.12 con numpy, pandas,
  matplotlib, scikit-learn, openpyxl, python-docx, python-pptx, reportlab; Node, pandoc y LibreOffice.
- **Asistente**: pídele cosas como «con ventas.csv hazme un Excel con un resumen por mes y un gráfico».
"""


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


# ------------------------------------------------------------------ paths


def root(ws: Workspace) -> Path:
    return get_settings().data_root / "workspaces" / str(ws.owner_id) / str(ws.id)


def ensure(ws: Workspace) -> Path:
    r = root(ws)
    if not r.exists():
        r.mkdir(parents=True)
        (r / "LEEME.md").write_text(WELCOME)
    return r


def clean_rel(path: str) -> str:
    p = PurePosixPath("/" + (path or "").replace("\\", "/")).as_posix().lstrip("/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise WorkspaceError("Ruta no válida")
    if any(len(x) > 255 for x in parts) or len(parts) > 40:
        raise WorkspaceError("Ruta demasiado larga")
    return "/".join(parts)


def resolve(ws: Workspace, path: str, *, must_exist: bool = True) -> Path:
    """Absolute path inside the workspace; refuses symlinks on every component."""
    base = ensure(ws)
    rel = clean_rel(path)
    cur = base
    for part in rel.split("/") if rel else []:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if must_exist:
                raise WorkspaceError(f"No existe: {rel}", 404)
            continue
        if stat.S_ISLNK(st.st_mode):
            raise WorkspaceError(f"{rel}: los enlaces simbólicos no se pueden abrir desde la web")
    real = Path(os.path.realpath(base / rel)) if rel else base
    if real != base and base not in real.parents:
        raise WorkspaceError("Ruta fuera del workspace")
    return base / rel if rel else base


def _regular(p: Path) -> os.stat_result:
    st = os.lstat(p)
    if not stat.S_ISREG(st.st_mode):
        raise WorkspaceError("No es un archivo normal")
    return st


# ------------------------------------------------------------------ listing / reading / writing


def tree(ws: Workspace) -> list[dict]:
    base = ensure(ws)
    out: list[dict] = []

    def walk(d: Path, rel: str, depth: int):
        try:
            entries = sorted(os.scandir(d), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if len(out) >= MAX_TREE:
                return
            r = f"{rel}/{e.name}" if rel else e.name
            st = e.stat(follow_symlinks=False)
            if e.is_symlink():
                out.append({"path": r, "type": "link", "size": 0, "mtime": st.st_mtime})
            elif e.is_dir(follow_symlinks=False):
                skip = e.name in HIDDEN
                out.append({"path": r, "type": "dir", "size": 0, "mtime": st.st_mtime, "collapsed": skip})
                if not skip and depth < 12:
                    walk(Path(e.path), r, depth + 1)
            elif e.is_file(follow_symlinks=False):
                out.append({"path": r, "type": "file", "size": st.st_size, "mtime": st.st_mtime})
    walk(base, "", 0)
    return out


def size_of(ws: Workspace) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root(ws)):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _check_quota(db: Session, ws: Workspace, extra: int = 0) -> None:
    limit = float(get_setting(db, "workspace_max_gb") or 10) * 1024 ** 3
    if size_of(ws) + extra > limit:
        raise WorkspaceError(f"El workspace supera {get_setting(db, 'workspace_max_gb') or 10} GB: borra archivos o pide más espacio", 409)


def is_text(path: Path, head: bytes) -> bool:
    if path.suffix.lower() in TEXT_EXT or path.name in ("Makefile", "Dockerfile", "LICENSE", "README"):
        return True
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read(ws: Workspace, path: str) -> dict:
    p = resolve(ws, path)
    st = _regular(p)
    with open(p, "rb") as fh:
        head = fh.read(MAX_TEXT_READ + 1)
    text = is_text(p, head[:4096])
    return {"path": clean_rel(path), "size": st.st_size, "mtime": st.st_mtime, "text": text,
            "truncated": text and st.st_size > MAX_TEXT_READ,
            "content": head[:MAX_TEXT_READ].decode("utf-8", errors="replace") if text else None,
            "mime": mimetypes.guess_type(p.name)[0] or ("text/plain" if text else "application/octet-stream")}


def raw(ws: Workspace, path: str) -> tuple[Path, str]:
    p = resolve(ws, path)
    _regular(p)
    return p, mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def write(db: Session, ws: Workspace, path: str, data: bytes) -> dict:
    rel = clean_rel(path)
    if not rel:
        raise WorkspaceError("Falta el nombre del archivo")
    p = resolve(ws, rel, must_exist=False)
    if p.exists() and not p.is_file():
        raise WorkspaceError("Ya existe una carpeta con ese nombre")
    _check_quota(db, ws, len(data))
    resolve(ws, str(PurePosixPath(rel).parent), must_exist=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
    touch(ws)
    return {"path": rel, "size": len(data)}


def write_stream(db: Session, ws: Workspace, path: str, fh, max_bytes: int) -> dict:
    """Like write() for uploads: copies in chunks (no whole file in memory) and enforces the size limits."""
    rel = clean_rel(path)
    if not rel:
        raise WorkspaceError("Falta el nombre del archivo")
    p = resolve(ws, rel, must_exist=False)
    if p.exists() and not p.is_file():
        raise WorkspaceError("Ya existe una carpeta con ese nombre")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:6]}.tmp")
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := fh.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise WorkspaceError(f"Máximo {max_bytes // (1024 * 1024)} MB por archivo", 413)
                out.write(chunk)
        _check_quota(db, ws)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)
    touch(ws)
    return {"path": rel, "size": size}


def mkdir(ws: Workspace, path: str) -> None:
    p = resolve(ws, path, must_exist=False)
    p.mkdir(parents=True, exist_ok=True)


def move(ws: Workspace, src: str, dst: str) -> None:
    a, b = resolve(ws, src), resolve(ws, dst, must_exist=False)
    if b.exists():
        raise WorkspaceError("El destino ya existe")
    b.parent.mkdir(parents=True, exist_ok=True)
    os.rename(a, b)


def delete(ws: Workspace, path: str) -> None:
    if not clean_rel(path):
        raise WorkspaceError("No se puede borrar la raíz")
    p = resolve(ws, path)
    if os.path.islink(p) or p.is_file():
        p.unlink()
    else:
        shutil.rmtree(p)


def zip_bytes(ws: Workspace, path: str = "") -> bytes:
    base = resolve(ws, path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if base.is_file():
            z.write(base, base.name)
        else:
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".venv", "__pycache__")]
                for f in files:
                    fp = Path(dirpath) / f
                    if fp.is_symlink() or not fp.is_file():
                        continue
                    z.write(fp, fp.relative_to(base))
    return buf.getvalue()


def import_files(db: Session, ws: Workspace, user: User, file_ids: list[uuid.UUID], folder: str = "") -> list[str]:
    out = []
    for fid in file_ids[:50]:
        f = db.get(UserFile, fid)
        if not f or f.owner_id != user.id:
            raise WorkspaceError("Archivo no encontrado", 404)
        rel = f"{clean_rel(folder)}/{f.filename}".lstrip("/")
        write(db, ws, rel, files_svc.abs_path(f).read_bytes())
        out.append(rel)
    return out


def export_file(db: Session, ws: Workspace, user: User, path: str) -> dict:
    p, _ = raw(ws, path)
    saved = store_bytes(db, user, p.name, p.read_bytes(), folder=f"Workspaces/{ws.name}"[:255], generated_by="workspace")
    if not saved:
        raise WorkspaceError("Ese tipo de archivo no se puede guardar en Mis archivos")
    return saved


def table_preview(ws: Workspace, path: str, rows: int = 200) -> dict:
    """First rows of a CSV/TSV/Excel file for the browser preview."""
    p, _ = raw(ws, path)
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv"):
        import csv

        with open(p, newline="", encoding="utf-8", errors="replace") as fh:
            sample = fh.read(65536)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel_tab if ext == ".tsv" else csv.excel
            data = [r for _, r in zip(range(rows + 1), csv.reader(fh, dialect))]
        return {"sheets": [{"name": p.name, "rows": data}]}
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(p, read_only=True, data_only=True)
        sheets = []
        for wsh in wb.worksheets[:10]:
            data = [["" if v is None else str(v) for v in r] for _, r in zip(range(rows + 1), wsh.iter_rows(values_only=True))]
            sheets.append({"name": wsh.title, "rows": data})
        wb.close()
        return {"sheets": sheets}
    raise WorkspaceError("Vista de tabla solo para CSV y Excel")


# ------------------------------------------------------------------ running commands


def _snapshot(base: Path) -> dict[str, tuple[int, int]]:
    """Regular files and their (size, mtime), skipping installs and caches (node_modules can hold 100 000 files)."""
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in HIDDEN]
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out[os.path.relpath(fp, base)] = (st.st_size, st.st_mtime_ns)
                if len(out) > 50_000:
                    return out
    return out


def run(db: Session, ws: Workspace, command: str, *, language: str = "bash", timeout_s: int | None = None) -> dict:
    """Run a command (or a whole script) in the sandbox with the workspace as /work. Returns output + changed files."""
    if not get_setting(db, "code_exec_enabled"):
        raise SandboxError("La ejecución de código está desactivada.", "Un administrador puede activarla en Ajustes → Sistema.")
    base = ensure(ws)
    _check_quota(db, ws)
    before = _snapshot(base)
    rid = uuid.uuid4().hex
    job = get_settings().data_root / "sandbox" / rid
    job.mkdir(parents=True)
    try:
        name = {"python": ".main.py", "node": ".main.js"}.get(language, ".main.sh")
        (job / name).write_text(command if language != "bash" else f"cd /work\n{command}\n")
        limit = int(timeout_s or get_setting(db, "workspace_timeout_s") or 600)
        from .sandbox import user_slot

        try:
            with user_slot(db, ws.owner_id):
                res = orchestrator.run_sandbox({"run_id": rid, "language": language if language in ("python", "node") else "bash",
                                                "timeout_s": max(5, min(900, limit)),
                                                "memory_mb": int(get_setting(db, "code_exec_memory_mb") or 4096),
                                                "workspace": f"{ws.owner_id}/{ws.id}",
                                                "packages": bool(get_setting(db, "workspace_packages_enabled"))})
        except orchestrator.OrchestratorError as e:
            if getattr(e, "status", None) == 424:
                raise SandboxError("Falta la imagen del entorno de ejecución.", "En el servidor: scripts/sah.sh build")
            raise SandboxError(f"No se pudo ejecutar: {e}")
    finally:
        shutil.rmtree(job, ignore_errors=True)
    after = _snapshot(base)
    changed = sorted(k for k, v in after.items() if before.get(k) != v)[:200]
    removed = sorted(k for k in before if k not in after)[:200]
    ws.size_bytes = size_of(ws)
    touch(ws)
    db.commit()
    return {**res, "changed": changed, "removed": removed}


# ------------------------------------------------------------------ terminal runs kept on the server
# The page may be closed while a command runs: runs execute in a thread and their state lives in Redis
# (last 30 per workspace, kept 7 days), so any page or window shows them again.

RUN_KEEP = 30
RUN_TTL = 7 * 86400


def _rkey(wid, rid=None) -> str:
    return f"sah:ws:{wid}:runs" if rid is None else f"sah:ws:run:{rid}"


def start_background_run(ws: Workspace, command: str, timeout_s: int | None) -> dict:
    import json
    import threading

    from ..db import SessionLocal
    from ..redis_client import get_redis

    r = get_redis()
    rid = uuid.uuid4().hex
    rec = {"id": rid, "command": command[:100_000], "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    r.set(_rkey(ws.id, rid), json.dumps(rec), ex=RUN_TTL)
    r.rpush(_rkey(ws.id), rid)
    r.ltrim(_rkey(ws.id), -RUN_KEEP, -1)
    r.expire(_rkey(ws.id), RUN_TTL)
    wid = ws.id

    def go():
        out = dict(rec)
        try:
            with SessionLocal() as db:
                w = db.get(Workspace, wid)
                res = run(db, w, command, timeout_s=timeout_s)
            out.update(status="done", **{k: res.get(k) for k in ("exit_code", "timed_out", "oom_killed", "seconds",
                                                                   "stdout", "stderr", "changed", "removed")})
        except Exception as e:  # noqa: BLE001  (sandbox/engine errors end up in the terminal)
            out.update(status="done", exit_code=-1, stderr=str(getattr(e, "message", e))[:2000], seconds=0)
        out["finished_at"] = datetime.now(timezone.utc).isoformat()
        r.set(_rkey(wid, rid), json.dumps(out), ex=RUN_TTL)

    threading.Thread(target=go, daemon=True, name=f"ws-run-{rid[:8]}").start()
    return rec


def list_runs(ws: Workspace) -> list[dict]:
    import json

    from ..redis_client import get_redis

    r = get_redis()
    ids = r.lrange(_rkey(ws.id), 0, -1)
    out = []
    for rid in ids:
        raw = r.get(_rkey(ws.id, rid))
        if raw:
            rec = json.loads(raw)
            if rec.get("status") == "running":  # a backend restart kills the thread: do not show it running forever
                started = datetime.fromisoformat(rec["started_at"])
                if (datetime.now(timezone.utc) - started).total_seconds() > 960:
                    rec.update(status="done", exit_code=-1, stderr="Interrumpido (el servidor se reinició)")
            out.append(rec)
    return out


def clear_runs(ws: Workspace) -> None:
    from ..redis_client import get_redis

    r = get_redis()
    for rid in r.lrange(_rkey(ws.id), 0, -1):
        r.delete(_rkey(ws.id, rid))
    r.delete(_rkey(ws.id))


def touch(ws: Workspace) -> None:
    ws.last_used_at = datetime.now(timezone.utc)


def public(ws: Workspace) -> dict:
    return {"id": str(ws.id), "name": ws.name, "description": ws.description, "size_bytes": ws.size_bytes,
            "conversation_id": str(ws.conversation_id) if ws.conversation_id else None,
            "created_at": ws.created_at, "last_used_at": ws.last_used_at}


def get_or_default(db: Session, user: User) -> list[Workspace]:
    rows = list(db.scalars(select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at)))
    if not rows:
        ws = Workspace(owner_id=user.id, name="Mi workspace")
        db.add(ws)
        db.commit()
        ensure(ws)
        rows = [ws]
    return rows
