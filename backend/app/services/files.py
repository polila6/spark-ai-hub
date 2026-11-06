"""Personal drive: typed storage of user files and safe text extraction (no macros, no code execution).

Layout: <data_root>/users/<user_id>/files/<file_id><ext> plus "<file_id>.extracted.txt" (extracted text) and
"<file_id>.pages.json" ([[page, text], ...]) once processed.
"""
from __future__ import annotations

import json
import mimetypes
import tempfile
import uuid
import zipfile
from pathlib import Path

from ..config import get_settings
from ..models import UserFile

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("application/x-sqlite3", ".sqlite")
mimetypes.add_type("application/x-sqlite3", ".db")
mimetypes.add_type("application/vnd.apache.parquet", ".parquet")

DOC_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".html", ".htm", ".rtf", ".tex", ".bib", ".rst",
           ".ipynb", ".log", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".srt", ".vtt"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb",
            ".php", ".r", ".m", ".jl", ".sh", ".sql", ".kt", ".swift", ".scala", ".lua", ".f90", ".vhd", ".v"}
DATA_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".parquet", ".sqlite", ".db", ".json", ".jsonl"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
BLOCKED_EXT = {".exe", ".dll", ".so", ".bat", ".cmd", ".msi", ".apk", ".com", ".scr", ".pkl", ".pickle", ".pt",
               ".pth", ".ckpt", ".joblib"}

MAGIC = [(b"%PDF-", {".pdf"}), (b"\x89PNG\r\n\x1a\n", {".png"}), (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
         (b"GIF8", {".gif"}), (b"BM", {".bmp"}), (b"SQLite format 3\x00", {".sqlite", ".db"}),
         (b"PAR1", {".parquet"}), (b"PK\x03\x04", {".docx", ".pptx", ".xlsx", ".xlsm"})]
MAX_TEXT_CHARS = 5_000_000
# A database is queried, not read as text: dumps and big table exports are never chunked and embedded.
DB_DUMP_EXT = {".sql", ".dump"}
MAX_DUMP_BYTES = 2 * 1024 * 1024  # a script of a few schema statements is a normal code file; a dump is not
TABULAR_EXT = {".csv", ".tsv", ".json", ".jsonl"}
MAX_TABULAR_INDEX_BYTES = 20 * 1024 * 1024


def skip_text_reason(ext: str, size: int | None) -> str | None:
    """Why a file must not be turned into searchable text (None = fine). The data tools keep working on it."""
    if ext in DB_DUMP_EXT and (size or 0) > MAX_DUMP_BYTES:
        return ("Volcado de base de datos (más de 2 MB): no se indexa como texto. Conecta la base de datos en "
                "«Conocimiento → Bases de datos» para consultarla en remoto.")
    if ext in TABULAR_EXT and (size or 0) > MAX_TABULAR_INDEX_BYTES:
        return ("Tabla muy grande (más de 20 MB): no se indexa como texto. Pregúntale al asistente con las herramientas de "
                "datos (describe_data / query_data) o conecta la base de datos de origen.")
    return None
MAX_PAGES = 3000


class FileError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def classify(filename: str, head: bytes) -> tuple[str, str]:
    """Return (kind, ext). Binary formats must match their magic bytes; text formats must decode as UTF-8."""
    ext = Path(filename).suffix.lower()
    if ext in BLOCKED_EXT:
        raise FileError("blocked_type", f"{ext} files are not accepted (executable or pickled content)")
    for magic, exts in MAGIC:
        if ext in exts and not head.startswith(magic):
            raise FileError("type_mismatch", f"The content is not a valid {ext} file")
    if ext == ".webp" and not (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        raise FileError("type_mismatch", "The content is not a valid .webp file")
    if ext in IMAGE_EXT:
        return "image", ext
    if ext in AUDIO_EXT:
        return "audio", ext
    if ext in DATA_EXT:
        return "data", ext
    if ext in DOC_EXT or ext in CODE_EXT:
        return "document", ext
    try:
        head.decode("utf-8")
        return "document", ext or ".txt"
    except UnicodeDecodeError:
        return "other", ext


def user_dir(user_id) -> Path:
    d = get_settings().data_root / "users" / str(user_id) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def abs_path(f: UserFile) -> Path:
    return get_settings().data_root / f.path


def text_path(f: UserFile) -> Path:
    return abs_path(f).with_name(f"{f.id}.extracted.txt")  # never the original (a .txt upload keeps its bytes)


def pages_path(f: UserFile) -> Path:
    return abs_path(f).with_name(f"{f.id}.pages.json")


def guess_mime(ext: str) -> str:
    return mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"


def read_text(f: UserFile, max_chars: int | None = None) -> str:
    p = text_path(f)
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8", errors="replace")
    return t[:max_chars] if max_chars else t


def read_pages(f: UserFile) -> list[tuple[int | None, str]]:
    p = pages_path(f)
    if p.exists():
        return [(a, b) for a, b in json.loads(p.read_text(encoding="utf-8"))]
    t = read_text(f)
    return [(None, t)] if t else []


# ------------------------------------------------------------------ extraction


def _zip_ok(path: Path) -> None:
    """Office files are zip archives: refuse zip bombs before parsing."""
    with zipfile.ZipFile(path) as z:
        total = sum(i.file_size for i in z.infolist())
        if total > 1_000_000_000 or len(z.infolist()) > 20000:
            raise FileError("archive_too_large", "Document expands to more than 1 GB")


def extract(path: Path, ext: str) -> list[tuple[int | None, str]]:
    """Return [(page_number_or_None, text)]. Never executes macros, scripts or embedded objects."""
    if ext == ".pdf":
        import pypdfium2 as pdfium

        from .web import _PDFIUM  # pdfium is not thread-safe: one PDF at a time per process

        with _PDFIUM:
            pdf = pdfium.PdfDocument(str(path))
            try:
                out = []
                for i in range(min(len(pdf), MAX_PAGES)):
                    page = pdf[i]
                    tp = page.get_textpage()
                    out.append((i + 1, tp.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")))
                    tp.close()
                    page.close()
                return out
            finally:
                pdf.close()
    if ext == ".docx":
        _zip_ok(path)
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return [(None, "\n".join(parts))]
    if ext == ".pptx":
        _zip_ok(path)
        from pptx import Presentation

        prs = Presentation(str(path))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                texts.append("Notas: " + slide.notes_slide.notes_text_frame.text)
            out.append((i, "\n".join(texts)))
        return out
    if ext in (".xlsx", ".xlsm"):
        _zip_ok(path)
        import openpyxl

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)  # data_only: cached values, no formulas
        out = []
        for ws in wb.worksheets:
            rows = []
            for r in ws.iter_rows(max_row=2000, values_only=True):
                rows.append(",".join("" if v is None else str(v) for v in r))
            out.append((None, f"# Hoja: {ws.title}\n" + "\n".join(rows)))
        wb.close()
        return out
    if ext in (".html", ".htm"):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(path.read_bytes()[:20_000_000], "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return [(None, soup.get_text("\n"))]
    if ext == ".ipynb":
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        cells = []
        for c in nb.get("cells", []):
            src = "".join(c.get("source") or [])
            cells.append(("```python\n" + src + "\n```") if c.get("cell_type") == "code" else src)
        return [(None, "\n\n".join(cells))]
    if ext in (".sqlite", ".db", ".parquet"):
        return []  # handled by the data tools, not as text
    raw = path.read_bytes()[: MAX_TEXT_CHARS * 2]
    return [(None, raw.decode("utf-8", errors="replace"))]


def _persist_extracted(f: UserFile, pages: list[tuple[int | None, str]]) -> dict:
    """Persist only searchable text and page boundaries, never the source bytes."""
    total = 0
    clean = []
    for pg, txt in pages:
        txt = "\n".join(line.rstrip() for line in (txt or "").split("\n"))
        if total + len(txt) > MAX_TEXT_CHARS:
            txt = txt[: max(0, MAX_TEXT_CHARS - total)]
        total += len(txt)
        clean.append([pg, txt])
        if total >= MAX_TEXT_CHARS:
            break
    text_path(f).write_text("\n\n".join(t for _, t in clean), encoding="utf-8")
    pages_path(f).write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    return {"status": "ready" if total else "empty", "chars": total, "pages": len(clean)}


def process(f: UserFile) -> dict:
    """Extract text from a stored file. Returns {"status", "chars", "pages"}."""
    path = abs_path(f)
    ext = path.suffix.lower()
    if f.kind not in ("document", "data") or ext in (".sqlite", ".db", ".parquet"):
        return {"status": "unsupported", "chars": 0, "pages": 0}
    why = skip_text_reason(ext, path.stat().st_size)
    if why:
        return {"status": "unsupported", "chars": 0, "pages": 0, "reason": why}
    return _persist_extracted(f, extract(path, ext))


def process_bytes(f: UserFile, data: bytes, ext: str) -> dict:
    """Extract from the container's RAM filesystem and persist only text/pages.

    Used by index-only connectors. ``/dev/shm`` is memory-backed, not persistent storage, and the temporary source is
    removed in ``finally`` as soon as extraction finishes.
    """
    if f.kind not in ("document", "data") or ext in (".sqlite", ".db", ".parquet"):
        return {"status": "unsupported", "chars": 0, "pages": 0}
    why = skip_text_reason(ext, len(data))
    if why:
        return {"status": "unsupported", "chars": 0, "pages": 0, "reason": why}
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="sah-index-only-", suffix=ext, dir="/dev/shm", delete=False) as ram_file:
            ram_file.write(data)
            ram_file.flush()
            tmp_path = Path(ram_file.name)
        return _persist_extracted(f, extract(tmp_path, ext))
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def render_pdf_pages(f: UserFile, max_pages: int = 4, scale: float = 1.4) -> list[bytes]:
    """PNG renders of the first pages of a PDF (for vision models reading scanned documents)."""
    import io

    import pypdfium2 as pdfium

    from .web import _PDFIUM

    out = []
    with _PDFIUM:
        pdf = pdfium.PdfDocument(str(abs_path(f)))
        try:
            for i in range(min(len(pdf), max_pages)):
                img = pdf[i].render(scale=scale).to_pil()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                out.append(buf.getvalue())
        finally:
            pdf.close()
    return out


def public(f: UserFile) -> dict:
    return {"id": str(f.id), "filename": f.filename, "folder": f.folder, "kind": f.kind, "mime": f.mime,
            "size_bytes": f.size_bytes, "sha256": f.sha256, "text_status": f.text_status, "text_chars": f.text_chars,
            "project_id": str(f.project_id) if f.project_id else None, "meta": f.meta, "source": f.source,
            "created_at": f.created_at, "url": f"/api/v1/files/{f.id}/content"}


_ = uuid
