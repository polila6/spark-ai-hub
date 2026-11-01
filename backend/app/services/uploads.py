"""Upload validation: size limits, magic-byte checks, safe archive extraction, no deserialisation."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from fastapi import UploadFile

from ..config import get_settings


class UploadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


IMAGE_MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp"}
AUDIO_MAGIC = {b"RIFF": ".wav", b"ID3": ".mp3", b"\xff\xfb": ".mp3", b"\xff\xf3": ".mp3", b"fLaC": ".flac",
               b"OggS": ".ogg"}
DATASET_EXT = {".csv", ".json", ".jsonl", ".npy", ".npz", ".zip"}
MODEL_FILE_EXT = {".safetensors", ".json", ".txt", ".md"}
EXECUTABLE_MODEL_EXT = {".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".bin", ".joblib", ".h5", ".keras"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


async def save_stream(upload: UploadFile, dest: Path, max_bytes: int | None = None) -> tuple[int, str]:
    max_bytes = max_bytes or get_settings().max_upload_mb * 1024 * 1024
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    with open(dest, "wb") as f:
        while chunk := await upload.read(4 << 20):
            size += len(chunk)
            if size > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise UploadError("too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB")
            h.update(chunk)
            f.write(chunk)
    return size, h.hexdigest()


def sniff(head: bytes, table: dict) -> str | None:
    for magic, ext in table.items():
        if head.startswith(magic):
            if magic == b"RIFF":
                if table is IMAGE_MAGIC and head[8:12] != b"WEBP":
                    continue
                if table is AUDIO_MAGIC and head[8:12] != b"WAVE":
                    continue
            return ext
    if table is AUDIO_MAGIC and head[4:8] == b"ftyp":
        return ".m4a"
    return None


def safe_name(name: str) -> str:
    base = Path(name or "file").name
    keep = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:120]
    return keep or "file"


def inspect_dataset(path: Path, ext: str) -> dict:
    """Extract schema information without executing any embedded code."""
    if ext == ".csv":
        import csv

        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            r = csv.reader(f)
            header = next(r, [])
            rows = sum(1 for _ in r)
        return {"columns": header[:500], "rows": rows}
    if ext == ".jsonl":
        n = 0
        keys: set = set()
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    n += 1
                    if n <= 50:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            keys |= set(obj)
        return {"rows": n, "keys": sorted(keys)[:200]}
    if ext == ".json":
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(obj, list):
            return {"rows": len(obj), "keys": sorted(obj[0].keys())[:200] if obj and isinstance(obj[0], dict) else []}
        return {"type": type(obj).__name__, "keys": sorted(obj)[:200] if isinstance(obj, dict) else []}
    if ext in (".npy", ".npz"):
        import numpy as np

        if ext == ".npy":
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
            return {"shape": list(arr.shape), "dtype": str(arr.dtype)}
        with np.load(path, allow_pickle=False) as z:
            return {"arrays": {k: {"shape": list(z[k].shape), "dtype": str(z[k].dtype)} for k in list(z.files)[:50]}}
    raise UploadError("unsupported", f"Unsupported dataset format {ext}")


def extract_image_zip(path: Path, dest: Path, max_files: int = 20000, max_total: int = 20 * 1024**3) -> dict:
    """Extract an image archive safely (no traversal, no symlinks, bounded size, images only)."""
    dest.mkdir(parents=True, exist_ok=True)
    count, total, classes = 0, 0, set()
    with zipfile.ZipFile(path) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        if len(members) > max_files:
            raise UploadError("too_many_files", f"Archive has more than {max_files} files")
        for m in members:
            p = Path(m.filename)
            if p.is_absolute() or ".." in p.parts or (m.external_attr >> 16) & 0o170000 == 0o120000:
                raise UploadError("unsafe_archive", f"Unsafe path in archive: {m.filename}")
            if p.suffix.lower() not in IMG_EXT or p.name.startswith("."):
                continue
            total += m.file_size
            if total > max_total:
                raise UploadError("too_large", "Archive expands beyond the allowed size")
            target = (dest / p).resolve()
            if dest.resolve() not in target.parents:
                raise UploadError("unsafe_archive", "Path traversal detected")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(target, "wb") as out:
                out.write(src.read())
            count += 1
            if len(p.parts) > 1:
                classes.add(p.parts[-2])
    return {"images": count, "bytes": total, "labels_from_folders": sorted(classes)[:1000]}


def safetensors_header(path: Path) -> dict:
    """Read the safetensors JSON header only (no tensor deserialisation, no code)."""
    with open(path, "rb") as f:
        n = int.from_bytes(f.read(8), "little")
        if n <= 0 or n > 100 * 1024 * 1024:
            raise UploadError("invalid_safetensors", "Invalid safetensors header")
        hdr = json.loads(f.read(n))
    return {k: {"dtype": v["dtype"], "shape": v["shape"]} for k, v in hdr.items() if k != "__metadata__"}


_ = io
