"""Remote databases, queried live and read-only. The Spark never copies or indexes them.

A connection is a host, a database and a read-only account. Every call opens a connection on demand, runs ONE statement on
the REMOTE server and returns at most `row_limit` rows (a LIMIT is wrapped around the query, so the rest is never sent).
Nothing is synchronised, stored or embedded: the result goes back to the caller and is gone.

Read-only is enforced twice, independently:
  1. `check_sql` accepts a single SELECT / WITH / VALUES / EXPLAIN / SHOW statement and refuses everything that could
     write, escape or burn the server (INTO, COPY, SET, data-modifying CTEs, file/large-object/dblink/sleep functions…).
  2. The session itself is read-only (`default_transaction_read_only`, `BEGIN READ ONLY`) with a statement timeout, so even a
     statement that slipped past the guard cannot change anything.
The administrator's list of private networks (`connector_private_hosts`) decides which LAN hosts may be reached, exactly as
for the knowledge connectors, and the connection is pinned to the address that passed that check.

Only PostgreSQL for now (psycopg 3). Pure helpers (`check_sql`, `wrap`, formatting) are unit tested.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import DbConnection
from ..security import decrypt_str, encrypt_str
from . import connectors

KINDS = ("postgres",)
SSL_MODES = ("disable", "prefer", "require", "verify-ca", "verify-full")
MAX_SQL_CHARS = 20_000
CELL_CHARS = 2_000  # one value is cut here, on the server (left(...)) when the column can be large
RESULT_CHARS = 20_000  # a whole result is cut here
MAX_ROWS_HARD = 5_000
MAX_TABLES = 200
_SLOTS = threading.BoundedSemaphore(8)  # at most 8 remote statements at once from this process


class RemoteDbError(Exception):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.message, self.code = message, code


# ------------------------------------------------------------------ the SQL guard


@dataclass
class Checked:
    clean: str  # the statement with comments removed (strings kept), without the trailing ';'
    kind: str  # select | explain | show


_FORBIDDEN_WORDS = re.compile(
    r"\b(insert|update|delete|merge|into|copy|set|analyze|analyse|drop|alter|create|grant|revoke|truncate|vacuum|reindex)\b")
_ROW_LOCK = re.compile(r"\bfor\s+(?:no\s+key\s+)?(?:key\s+)?(?:share|update)\b")
_FORBIDDEN_NAMES = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_\w*|pg_stat_file|pg_file_\w*|lo_\w+|lowrite|loread|dblink\w*|pg_sleep\w*|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_switch_wal|pg_create_\w+|pg_drop_\w+|"
    r"pg_advisory\w*|set_config|nextval|setval|currval|lastval|(?:query|table|cursor|database|schema)_to_xml\w*|"
    r"pg_authid|pg_shadow|pg_user_mapping|pg_largeobject\w*|pg_execute_server_program|pg_logical_\w+|pg_replication_\w+|"
    r"pg_export_snapshot|pg_backup_\w+|pg_start_backup|pg_stop_backup|pg_promote|pg_wal_\w+|xpath_table|"
    r"pg_stat_statements_reset|pg_stat_reset\w*)\b")
_FIRST = {"select": "select", "with": "select", "values": "select", "explain": "explain", "show": "show"}


def _scan(sql: str) -> tuple[str, str, str]:
    """(clean, kw, fn): comments removed / + strings and quoted names blanked / + quoted names unquoted."""
    clean, kw, fn = [], [], []
    i, n = 0, len(sql)

    def add(c: str, k: str, f: str) -> None:
        clean.append(c); kw.append(k); fn.append(f)

    while i < n:
        c = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            add("", " ", " ")
            i = j
            continue
        if two == "/*":
            depth, j = 1, i + 2
            while j < n and depth:
                if sql[j:j + 2] == "/*":
                    depth, j = depth + 1, j + 2
                elif sql[j:j + 2] == "*/":
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise RemoteDbError("Comentario sin cerrar")
            add(" ", " ", " ")
            i = j
            continue
        if c == "'":
            escaped = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            j = i + 1
            while j < n:
                if escaped and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise RemoteDbError("Texto sin cerrar (comilla simple)")
            add(sql[i:j + 1], "''", "''")
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql[j + 1:j + 2] == '"':
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise RemoteDbError("Nombre sin cerrar (comillas dobles)")
            add(sql[i:j + 1], '""', " " + sql[i + 1:j].replace('""', '"') + " ")
            i = j + 1
            continue
        if c == "$" and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            m = re.match(r"\$([A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*)?\$", sql[i:])
            if m:
                end = sql.find(m.group(0), i + len(m.group(0)))
                if end < 0:
                    raise RemoteDbError("Texto entre $$ sin cerrar")
                j = end + len(m.group(0))
                add(sql[i:j], "''", "''")
                i = j
                continue
        add(c, c, c)
        i += 1
    return "".join(clean), "".join(kw), "".join(fn)


def check_sql(sql: str) -> Checked:
    """Accept one read-only statement or raise RemoteDbError saying why not."""
    if not isinstance(sql, str) or not sql.strip():
        raise RemoteDbError("La consulta está vacía")
    if len(sql) > MAX_SQL_CHARS:
        raise RemoteDbError(f"La consulta es demasiado larga (máximo {MAX_SQL_CHARS} caracteres)")
    clean, kw, fn = _scan(sql)
    clean, kw, fn = clean.strip(), kw.strip().lower(), fn.strip().lower()
    if kw.endswith(";"):  # one trailing semicolon is fine
        clean, kw, fn = clean.rstrip()[:-1].rstrip(), kw[:-1].rstrip(), fn.rstrip()[:-1].rstrip()
    if not kw:
        raise RemoteDbError("La consulta está vacía")
    if ";" in kw:
        raise RemoteDbError("Solo se admite una sentencia por consulta (sin «;» en medio)")
    m = re.match(r"[\s(]*([a-z_]+)", kw)
    first = m.group(1) if m else ""
    kind = _FIRST.get(first)
    if not kind:
        raise RemoteDbError(f"Solo se permiten consultas de lectura (SELECT, WITH, VALUES, EXPLAIN, SHOW); esta empieza por «{first or '?'}»")
    bad = _FORBIDDEN_WORDS.search(kw)
    if bad:
        raise RemoteDbError(f"No se permite «{bad.group(1).upper()}»: la conexión es de solo lectura")
    if _ROW_LOCK.search(kw):
        raise RemoteDbError("No se permiten bloqueos de filas (FOR UPDATE / FOR SHARE)")
    bad = _FORBIDDEN_NAMES.search(fn)
    if bad:
        raise RemoteDbError(f"No se permite «{bad.group(1)}»")
    return Checked(clean=clean, kind=kind)


def wrap(clean: str, limit: int) -> str:
    """The statement as a subquery with a LIMIT of limit+1 (the extra row tells the caller the result was cut)."""
    return f"SELECT * FROM (\n{clean}\n) AS _sah_q LIMIT {int(limit) + 1}"


# ------------------------------------------------------------------ configuration


_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$|^\[?[0-9A-Fa-f:.]+\]?$")
_NAME = re.compile(r"^[\w][\w .\-]{0,79}$", re.U)


def validate(cfg: dict) -> dict:
    """Clean the fields of a connection (without the password). Raises RemoteDbError."""
    kind = str(cfg.get("kind") or "postgres")
    if kind not in KINDS:
        raise RemoteDbError("Por ahora solo se admite PostgreSQL")
    name = str(cfg.get("name") or "").strip()
    if not _NAME.match(name):
        raise RemoteDbError("El nombre admite letras, números, espacios, «_», «-» y «.» (máximo 80)")
    host = str(cfg.get("host") or "").strip().strip("[]")
    if not host or len(host) > 255 or not _HOST.match(host):
        raise RemoteDbError("Servidor no válido: escribe solo el nombre o la IP, sin «postgres://» ni rutas")
    try:
        port = int(cfg.get("port") or 5432)
    except (TypeError, ValueError):
        raise RemoteDbError("El puerto debe ser un número")
    if not 1 <= port <= 65535:
        raise RemoteDbError("El puerto debe estar entre 1 y 65535")
    database = str(cfg.get("database") or "").strip()
    username = str(cfg.get("username") or "").strip()
    if not database or len(database) > 128:
        raise RemoteDbError("Falta el nombre de la base de datos")
    if not username or len(username) > 128:
        raise RemoteDbError("Falta el usuario")
    sslmode = str(cfg.get("sslmode") or "require")
    if sslmode not in SSL_MODES:
        raise RemoteDbError(f"sslmode debe ser uno de: {', '.join(SSL_MODES)}")
    try:
        row_limit = max(1, min(MAX_ROWS_HARD, int(cfg.get("row_limit") or 500)))
        timeout_s = max(1, min(120, int(cfg.get("timeout_s") or 20)))
    except (TypeError, ValueError):
        raise RemoteDbError("Límites no válidos")
    return {"kind": kind, "name": name, "host": host, "port": port, "database": database, "username": username,
            "sslmode": sslmode, "row_limit": row_limit, "timeout_s": timeout_s}


def set_secret(c: DbConnection, secret: str | None) -> None:
    """None = keep it, '' = clear it, anything else = replace it. It is encrypted and never returned by the API."""
    if secret is not None:
        c.secret = encrypt_str(secret) if secret else None


def public(c: DbConnection) -> dict:
    return {"id": str(c.id), "name": c.name, "kind": c.kind, "host": c.host, "port": c.port, "database": c.database,
            "username": c.username, "sslmode": c.sslmode, "row_limit": c.row_limit, "timeout_s": c.timeout_s,
            "has_secret": bool(c.secret), "created_at": c.created_at}


def find(db: Session, owner_id: uuid.UUID, name_or_id: str) -> DbConnection | None:
    """One of the user's connections by name (case-insensitive) or id."""
    from sqlalchemy import func, select

    key = str(name_or_id or "").strip()
    if not key:
        return None
    try:
        row = db.get(DbConnection, uuid.UUID(key))
        if row and row.owner_id == owner_id:
            return row
    except ValueError:
        pass
    return db.scalar(select(DbConnection).where(DbConnection.owner_id == owner_id, func.lower(DbConnection.name) == key.lower()))


def names(db: Session, owner_id: uuid.UUID) -> list[str]:
    from sqlalchemy import select

    return list(db.scalars(select(DbConnection.name).where(DbConnection.owner_id == owner_id).order_by(DbConnection.name)))


# ------------------------------------------------------------------ connecting


def _connect(db: Session, c: DbConnection):
    import psycopg

    try:
        ip = connectors.resolve(db, c.host, c.port)  # SSRF guard; the connection is pinned to this address
    except connectors.ConnectorError as e:
        raise RemoteDbError(e.message, "network") from e
    password = decrypt_str(c.secret) if c.secret else None
    ms = int(c.timeout_s) * 1000
    try:
        conn = psycopg.connect(
            host=c.host, hostaddr=ip, port=c.port, dbname=c.database, user=c.username, password=password, sslmode=c.sslmode,
            connect_timeout=10, application_name="spark-ai-hub", autocommit=False,
            keepalives=1, keepalives_idle=15, keepalives_interval=5, keepalives_count=3,
            options=f"-c default_transaction_read_only=on -c statement_timeout={ms} -c lock_timeout=2000 "
                    f"-c idle_in_transaction_session_timeout={ms + 10000}")
    except psycopg.OperationalError as e:
        raise RemoteDbError(f"No se pudo conectar con {c.host}:{c.port}: {_short(e)}", "connect") from e
    conn.read_only = True  # BEGIN READ ONLY
    return conn


def _short(e: Exception) -> str:
    diag = getattr(e, "diag", None)
    msg = (getattr(diag, "message_primary", None) or str(e)).strip().splitlines()[0] if (diag or str(e)) else type(e).__name__
    return msg[:300]


def _translate(e: Exception, c: DbConnection) -> RemoteDbError:
    import psycopg

    if isinstance(e, psycopg.errors.QueryCanceled):
        return RemoteDbError(f"La consulta superó el tiempo máximo ({c.timeout_s} s). Añade filtros o agrega los datos.", "timeout")
    if isinstance(e, psycopg.errors.ReadOnlySqlTransaction):
        return RemoteDbError("La conexión es de solo lectura", "read_only")
    if isinstance(e, psycopg.errors.InsufficientPrivilege):
        return RemoteDbError(f"Permiso denegado en el servidor: {_short(e)}", "denied")
    if isinstance(e, psycopg.OperationalError):
        return RemoteDbError(f"Se perdió la conexión con el servidor: {_short(e)}", "connect")
    return RemoteDbError(f"Error SQL: {_short(e)}", "sql")


def _run(db: Session, c: DbConnection, fn):
    if not _SLOTS.acquire(timeout=5):
        raise RemoteDbError("Hay demasiadas consultas a bases de datos a la vez; inténtalo en unos segundos", "busy")
    try:
        conn = _connect(db, c)
        try:
            with conn:
                return fn(conn)
        except RemoteDbError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _translate(e, c) from e
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        _SLOTS.release()


# ------------------------------------------------------------------ test / schema / query

_SMALL_TYPES = {16, 20, 21, 23, 26, 700, 701, 1700, 1082, 1083, 1114, 1184, 1186, 2950}  # bool, ints, floats, numeric, dates, uuid


def test(db: Session, c: DbConnection) -> dict:
    t0 = time.perf_counter()

    def go(conn):
        cur = conn.cursor()
        cur.execute("SELECT version(), current_database(), current_user")
        version, dbname, user = cur.fetchone()
        cur.execute(
            "SELECT coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false),"
            " (SELECT count(*) FROM information_schema.table_privileges WHERE grantee = current_user"
            "   AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
            "   AND table_schema NOT IN ('pg_catalog','information_schema')),"
            " has_database_privilege(current_user, current_database(), 'CREATE')")
        sup, grants, can_create = cur.fetchone()
        return version, dbname, user, bool(sup), int(grants), bool(can_create)

    version, dbname, user, sup, grants, can_create = _run(db, c, go)
    warnings = []
    if sup:
        warnings.append("Esta cuenta es superusuario. Crea una cuenta de solo lectura para el Spark.")
    elif grants or can_create:
        warnings.append("Esta cuenta puede escribir en la base de datos. El Spark solo lee, pero lo recomendable es "
                        "una cuenta con permiso únicamente de SELECT.")
    if c.sslmode in ("disable", "prefer"):
        warnings.append("La conexión puede ir sin cifrar. Usa sslmode «require» o «verify-full» si el servidor lo permite.")
    return {"ok": True, "server": str(version).split(",")[0][:120], "database": dbname, "user": user, "warnings": warnings,
            "ms": round((time.perf_counter() - t0) * 1000)}


def schema(db: Session, c: DbConnection, table: str | None = None) -> dict:
    """Tables and columns from the catalog. Row counts are the server's own estimates: no table is ever counted or read."""
    flt = (table or "").strip().strip('"').lower()

    def go(conn):
        cur = conn.cursor()
        params: list = []
        where = ""
        if flt:
            if "." in flt:
                where = " AND lower(n.nspname) = %s AND lower(c.relname) = %s"
                params = flt.split(".", 1)
            else:
                where = " AND lower(c.relname) = %s"
                params = [flt]
        cur.execute(
            "SELECT n.nspname, c.relname, c.relkind::text, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r','v','m','p','f') AND n.nspname NOT IN ('pg_catalog','information_schema')"
            " AND n.nspname NOT LIKE 'pg\\_toast%%' AND n.nspname NOT LIKE 'pg\\_temp%%'" + where +
            f" ORDER BY n.nspname, c.relname LIMIT {MAX_TABLES + 1}", params)
        rows = cur.fetchall()
        truncated = len(rows) > MAX_TABLES
        rows = rows[:MAX_TABLES]
        tables = {(s, t): {"schema": s, "name": t, "type": {"r": "table", "v": "view", "m": "materialized view", "p": "partitioned table", "f": "foreign table"}.get(k, k),
                           "approx_rows": int(n) if n is not None and int(n) >= 0 else None, "columns": []} for s, t, k, n in rows}
        if tables:
            cur.execute(
                "SELECT table_schema, table_name, column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE (table_schema, table_name) IN (SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                f"   WHERE c.relkind IN ('r','v','m','p','f') AND n.nspname NOT IN ('pg_catalog','information_schema'){where})"
                " ORDER BY table_schema, table_name, ordinal_position", params)
            for s, t, col, typ, nul in cur.fetchall():
                if (s, t) in tables:
                    tables[(s, t)]["columns"].append({"name": col, "type": typ, "nullable": nul == "YES"})
        return {"tables": list(tables.values()), "truncated": truncated}

    return _run(db, c, go)


def schema_markdown(res: dict, max_chars: int = 12_000) -> str:
    out, size = [], 0
    for t in res["tables"]:
        approx = "" if t["approx_rows"] is None else f" ~{t['approx_rows']:,} filas".replace(",", ".")
        head = f"## {t['schema']}.{t['name']} ({t['type']}{approx})"
        cols = ", ".join(f"{c['name']} {c['type']}{'' if c['nullable'] else ' NOT NULL'}" for c in t["columns"])
        block = f"{head}\n{cols}\n"
        if size + len(block) > max_chars:
            out.append(f"… {len(res['tables']) - len(out)} tablas más: pide una concreta con el parámetro «table».")
            break
        out.append(block)
        size += len(block)
    if res.get("truncated"):
        out.append(f"(Solo se listan las primeras {MAX_TABLES} tablas)")
    return "\n".join(out) or "No hay tablas visibles para este usuario."


def _cell(v, cell: int = CELL_CHARS):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return f"<{len(b)} bytes: {b[:16].hex()}…>" if len(b) > 16 else f"<{len(b)} bytes: {b.hex()}>"
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple, dict)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    s = str(v)
    return s if len(s) <= cell else s[:cell] + f"… [{len(s) - cell} caracteres más]"


def query(db: Session, c: DbConnection, sql: str) -> dict:
    """Run one read-only statement on the remote server. At most `row_limit` rows and ~20k characters come back."""
    chk = check_sql(sql)
    limit = int(c.row_limit)
    t0 = time.perf_counter()

    def go(conn):
        cur = conn.cursor()
        if chk.kind == "select":
            # first learn the columns (LIMIT 0), so big text/json/bytea values are cut on the server and not sent whole
            select_list = "*"
            try:
                cur.execute(f"SELECT * FROM (\n{chk.clean}\n) AS _sah_q LIMIT 0")
                desc = cur.description or []
                names_ = [d.name for d in desc]
                if desc and len(set(names_)) == len(names_) and all(names_):
                    q = lambda s: '"' + s.replace('"', '""') + '"'  # noqa: E731
                    select_list = ", ".join(f"t.{q(d.name)}" if d.type_code in _SMALL_TYPES else f"left(t.{q(d.name)}::text, {CELL_CHARS}) AS {q(d.name)}"
                                            for d in desc)
                conn.rollback()
            except Exception:  # noqa: BLE001 — describing is an optimisation, never a reason to fail
                conn.rollback()
                select_list = "*"
            stmt = f"SELECT {select_list} FROM (\n{chk.clean}\n) AS t LIMIT {limit + 1}"
        else:
            stmt = chk.clean
        cur.execute(stmt)
        cols = [d.name for d in (cur.description or [])]
        got = cur.fetchmany(limit + 1)
        return cols, got

    cols, got = _run(db, c, go)
    truncated = len(got) > limit
    rows, chars, cut_by_size = [], 0, False
    for r in got[:limit]:
        cells = [_cell(v) for v in r]
        chars += sum(len(str(x)) for x in cells if x is not None) + len(cells)
        if rows and chars > RESULT_CHARS:
            cut_by_size = True
            break
        rows.append(cells)
    notes = []
    if truncated:
        notes.append(f"Hay más filas: se devuelven las primeras {limit}. Filtra, agrega (GROUP BY) o usa LIMIT/OFFSET para ver otras.")
    if cut_by_size:
        notes.append("El resultado se cortó por tamaño; pide menos columnas o filas.")
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated or cut_by_size, "limit": limit,
            "ms": round((time.perf_counter() - t0) * 1000), "notes": notes}


def as_markdown(res: dict, max_rows: int = 200) -> str:
    cols = res.get("columns") or []
    if not cols:
        return "(sin resultados)"

    def esc(v) -> str:
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(esc(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in res["rows"][:max_rows]:
        lines.append("| " + " | ".join(esc(v) for v in r) + " |")
    out = "\n".join(lines)
    if len(res["rows"]) > max_rows:
        out += f"\n… {len(res['rows']) - max_rows} filas más no se muestran aquí"
    if res.get("notes"):
        out += "\n\n" + "\n".join(res["notes"])
    return out
