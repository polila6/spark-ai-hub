"""Natural-language questions over the user's data files: schema description and sandboxed SQL execution."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..models import UserFile
from . import files as files_svc

RUNNER = Path(__file__).with_name("dataq_runner.py")
DATA_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".parquet", ".sqlite", ".db", ".json", ".jsonl"}
TIMEOUT_S = 60


class DataError(Exception):
    pass


def _run(f: UserFile, req: dict) -> dict:
    path = files_svc.abs_path(f)
    ext = path.suffix.lower()
    if ext not in DATA_EXT:
        raise DataError(f"{f.filename} is not a data file (CSV, TSV, Excel, Parquet, JSON or SQLite)")
    try:
        proc = subprocess.run([sys.executable, "-I", str(RUNNER)], input=json.dumps({**req, "path": str(path), "ext": ext}),
                              capture_output=True, text=True, timeout=TIMEOUT_S, env={"PATH": "/usr/bin:/bin",
                                                                                     "HOME": "/tmp"})
    except subprocess.TimeoutExpired:
        raise DataError(f"Query exceeded {TIMEOUT_S} s")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise DataError(f"Data runner failed: {proc.stderr[-500:]}")
    if not out.get("ok"):
        raise DataError(out.get("error") or "query failed")
    out.pop("ok", None)
    return out


def describe(f: UserFile) -> dict:
    return _run(f, {"op": "describe"})


def query(f: UserFile, sql: str) -> dict:
    sql = sql.strip().rstrip(";")
    if not sql:
        raise DataError("empty SQL")
    if ";" in sql:
        raise DataError("Only one SQL statement per call")
    return _run(f, {"op": "query", "sql": sql})


def as_markdown(res: dict, max_rows: int = 50) -> str:
    cols = res.get("columns") or []
    rows = (res.get("rows") or [])[:max_rows]
    if not cols:
        return "(sin resultados)"
    head = "| " + " | ".join(map(str, cols)) + " |\n|" + "---|" * len(cols)
    body = "\n".join("| " + " | ".join("" if v is None else str(v).replace("|", "\\|") for v in r) + " |" for r in rows)
    more = f"\n… {len(res['rows']) - max_rows} filas más" if len(res.get("rows") or []) > max_rows else ""
    trunc = "\n(resultado truncado a 200 filas)" if res.get("truncated") else ""
    return f"{head}\n{body}{more}{trunc}"
