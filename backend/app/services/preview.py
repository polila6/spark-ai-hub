"""What the document viewer needs from any stored file: tables for spreadsheets and PDF renderings of Office documents.

Office files are converted inside the code sandbox (no network, no privileges): LibreOffice is never run on the server
itself, because these documents may come from anybody. The PDF is cached by the content hash of the document.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User
from . import sandbox

OFFICE_EXT = {".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".odp", ".xls", ".ods"}
VIDEO_MIME = {".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg", ".mov": "video/quicktime", ".m4v": "video/mp4",
              ".mkv": "video/x-matroska"}
MAX_OFFICE_BYTES = 100 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
CACHE_DAYS = 30
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class PreviewError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


def is_office(name: str) -> bool:
    return Path(name).suffix.lower() in OFFICE_EXT


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _cache_dir() -> Path:
    d = get_settings().data_root / "cache" / "previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def office_pdf(db: Session, user: User, path: Path, name: str, sha256: str | None = None) -> Path:
    """PDF rendering of a Word/PowerPoint/OpenDocument/old Excel file. Same document = same cached PDF."""
    ext = Path(name).suffix.lower()
    if ext not in OFFICE_EXT:
        raise PreviewError("Este tipo de archivo no se convierte a PDF")
    if path.stat().st_size > MAX_OFFICE_BYTES:
        raise PreviewError("El documento supera 100 MB: descárgalo para verlo", 413)
    sha = sha256 if sha256 and re.fullmatch(r"[0-9a-f]{64}", sha256) else _sha256(path)
    out = _cache_dir() / f"{sha}.pdf"
    with _lock(sha):  # several people opening the same document convert it once
        if out.exists():
            os.utime(out)
            return out
        if not sandbox.enabled(db):
            raise PreviewError("La vista previa de documentos necesita la ejecución de código, que está desactivada", 503)
        res = sandbox.run_raw(db, user, script=f"set -e\nmkdir -p out\nsoffice --headless --convert-to pdf --outdir out in/doc{ext} >/dev/null 2>&1\nls out",
                              files={f"doc{ext}": path.read_bytes()}, timeout_s=180)
        pdf = res["outputs"].get("out/doc.pdf")
        if res["exit_code"] != 0 or not pdf:
            raise PreviewError("No se pudo convertir el documento para verlo. Puedes descargarlo.")
        tmp = out.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(pdf)
        tmp.replace(out)
    return out


def purge_cache(days: int = CACHE_DAYS) -> int:
    limit, removed = time.time() - days * 86400, 0
    for p in _cache_dir().glob("*.pdf"):
        if p.stat().st_mtime < limit:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def table(path: Path, name: str, rows: int = 500) -> dict:
    """First rows of a CSV/TSV/Excel file: {sheets: [{name, rows, truncated}]}."""
    ext = Path(name).suffix.lower()
    if ext in (".csv", ".tsv"):
        import csv

        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            sample = fh.read(65536)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel_tab if ext == ".tsv" else csv.excel
            data = [r for _, r in zip(range(rows + 2), csv.reader(fh, dialect))]
        return {"sheets": [{"name": name, "rows": data[:rows + 1], "truncated": len(data) > rows + 1}]}
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        sheets = []
        for wsh in wb.worksheets[:20]:
            data = [["" if v is None else str(v) for v in r] for _, r in zip(range(rows + 2), wsh.iter_rows(values_only=True))]
            sheets.append({"name": wsh.title, "rows": data[:rows + 1], "truncated": len(data) > rows + 1})
        wb.close()
        return {"sheets": sheets}
    raise PreviewError("Vista de tabla solo para CSV y Excel")


def plain_text(path: Path) -> str:
    """The start of a text file as it is on disk (code, Markdown, logs…)."""
    with open(path, "rb") as fh:
        data = fh.read(MAX_TEXT_BYTES + 1)
    if b"\0" in data[:8192]:
        raise PreviewError("Es un archivo binario: descárgalo para verlo")
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    return text + ("\n… (archivo recortado a 2 MB; descárgalo para verlo entero)" if len(data) > MAX_TEXT_BYTES else "")
