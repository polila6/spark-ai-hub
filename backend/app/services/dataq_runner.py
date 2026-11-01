"""Isolated data-query runner, executed as `python -I dataq_runner.py` with a JSON request on stdin.

The file is copied into an in-memory DuckDB database (or opened read-only for SQLite); afterwards external access is
disabled and configuration locked, so a query cannot read other files, install extensions or reach the network.
Only stdlib, duckdb and openpyxl are imported.
"""
import csv
import json
import os
import sqlite3
import sys
import tempfile

MAX_ROWS = 200
MAX_CELL = 2000


def _cell(v):
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return s if len(s) <= MAX_CELL else s[:MAX_CELL] + "…"


def _duck():
    import duckdb

    con = duckdb.connect(":memory:", config={"autoinstall_known_extensions": False, "threads": 2,
                                             "memory_limit": "3GB"})
    return con


def _load(con, path, ext, tmpdir):
    tables = []
    if ext in (".csv", ".tsv"):
        con.execute("CREATE TABLE datos AS SELECT * FROM read_csv_auto(?, sample_size=-1)", [path])
        tables = ["datos"]
    elif ext == ".parquet":
        con.execute("CREATE TABLE datos AS SELECT * FROM read_parquet(?)", [path])
        tables = ["datos"]
    elif ext in (".json", ".jsonl"):
        con.execute("CREATE TABLE datos AS SELECT * FROM read_json_auto(?)", [path])
        tables = ["datos"]
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for i, ws in enumerate(wb.worksheets):
            name = "".join(c if c.isalnum() else "_" for c in ws.title).strip("_").lower() or f"hoja{i + 1}"
            if name[0].isdigit():
                name = "h_" + name
            tmp = os.path.join(tmpdir, f"{i}.csv")
            with open(tmp, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                for row in ws.iter_rows(values_only=True):
                    w.writerow(["" if v is None else v for v in row])
            if os.path.getsize(tmp) == 0:
                continue
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM read_csv_auto(?, sample_size=-1)', [tmp])
            tables.append(name)
        wb.close()
    else:
        raise ValueError(f"unsupported data format {ext}")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return tables


def _describe_duck(con, tables):
    out = []
    for t in tables:
        cols = con.execute(f'DESCRIBE "{t}"').fetchall()
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        sample = con.execute(f'SELECT * FROM "{t}" LIMIT 5')
        names = [d[0] for d in sample.description]
        out.append({"table": t, "rows": n, "columns": [{"name": c[0], "type": c[1]} for c in cols],
                    "sample": [dict(zip(names, map(_cell, r))) for r in sample.fetchall()]})
    return out


def _sqlite(path, req):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")

    def auth(action, *_):
        return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK

    con.set_authorizer(auth)
    if req["op"] == "describe":
        out = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"):
            cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            cur = con.execute(f'SELECT * FROM "{t}" LIMIT 5')
            names = [d[0] for d in cur.description]
            out.append({"table": t, "rows": n, "columns": [{"name": c[1], "type": c[2]} for c in cols],
                        "sample": [dict(zip(names, map(_cell, r))) for r in cur.fetchall()]})
        return {"engine": "sqlite", "tables": out}
    cur = con.execute(req["sql"])
    names = [d[0] for d in (cur.description or [])]
    rows = cur.fetchmany(MAX_ROWS + 1)
    return {"engine": "sqlite", "columns": names, "rows": [list(map(_cell, r)) for r in rows[:MAX_ROWS]],
            "truncated": len(rows) > MAX_ROWS}


def main():
    req = json.loads(sys.stdin.read())
    path, ext = req["path"], req["ext"]
    try:
        if ext in (".sqlite", ".db"):
            res = _sqlite(path, req)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                con = _duck()
                tables = _load(con, path, ext, tmpdir)
                if req["op"] == "describe":
                    res = {"engine": "duckdb", "tables": _describe_duck(con, tables)}
                else:
                    cur = con.execute(req["sql"])
                    names = [d[0] for d in (cur.description or [])]
                    rows = cur.fetchmany(MAX_ROWS + 1)
                    res = {"engine": "duckdb", "columns": names, "rows": [list(map(_cell, r)) for r in rows[:MAX_ROWS]],
                           "truncated": len(rows) > MAX_ROWS}
        print(json.dumps({"ok": True, **res}, default=str, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
