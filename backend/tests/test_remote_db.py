"""Remote databases: the SQL guard, the LIMIT wrapper, result formatting, secrets and network checks (no server needed)."""
import datetime as dt
import decimal
import uuid
from types import SimpleNamespace

import pytest

from app.services import remote_db as R


# ------------------------------------------------------------------ the guard


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from users",
    "SELECT * FROM users;",
    "  SELECT id, name FROM people WHERE name = 'a;b' AND note = 'DROP TABLE x'  ",
    "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
    "VALUES (1), (2)",
    "EXPLAIN SELECT * FROM users",
    "SHOW server_version",
    "(SELECT 1) UNION (SELECT 2)",
    "SELECT jsonb_set(data, '{a}', '1') FROM docs",                      # jsonb_set is a different word from SET
    "SELECT created_at, updated_at, delete_flag, insert_count FROM t",   # words that merely contain keywords
    "SELECT \"update\", \"set\" FROM t",                                 # quoted column names that look like keywords
    "SELECT 'it''s fine; really' AS s",
    "SELECT $$a; DROP TABLE x$$",
    "SELECT $tag$ INSERT INTO x $tag$",
    "SELECT /* delete from x; */ 1",
    "SELECT 1 -- ; drop table x",
    "SELECT * FROM t ORDER BY id FETCH FIRST 5 ROWS ONLY",
    "SELECT count(*) FILTER (WHERE x > 1) FROM t",
    "SELECT current_setting('server_version')",
])
def test_reads_are_accepted(sql):
    assert R.check_sql(sql).kind in ("select", "explain", "show")


@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "TRUNCATE t",
    "ALTER TABLE t ADD COLUMN x int",
    "CREATE TABLE x AS SELECT 1",
    "GRANT ALL ON t TO public",
    "COPY t TO '/tmp/x'",
    "COPY (SELECT 1) TO PROGRAM 'id'",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE t",
    "SELECT 1 /* x */; /* y */ DELETE FROM t",
    "SELECT * INTO newtable FROM t",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i",
    "WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM u",
    "EXPLAIN ANALYZE DELETE FROM t",
    "EXPLAIN (ANALYZE) SELECT 1",
    "SET search_path = x",
    "RESET ALL",
    "CALL do_something()",
    "DO $$ BEGIN PERFORM 1; END $$",
    "EXECUTE foo",
    "PREPARE x AS SELECT 1",
    "LOCK TABLE t",
    "VACUUM t",
    "BEGIN",
    "COMMIT",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR SHARE",
    "SELECT * FROM t FOR NO KEY UPDATE",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT \"pg_read_file\"('/etc/passwd')",
    "SELECT PG_READ_FILE('/etc/passwd')",
    "SELECT pg_catalog.pg_read_binary_file('/x')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)",
    "SELECT dblink_exec('x', 'drop table t')",
    "SELECT pg_sleep(100)",
    "SELECT pg_terminate_backend(123)",
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT nextval('s')",
    "SELECT setval('s', 1)",
    "SELECT query_to_xml('delete from t', true, true, '')",
    "SELECT * FROM pg_authid",
    "SELECT * FROM pg_shadow",
    "SELECT pg_advisory_lock(1)",
    "SELECT 'unterminated",
    "SELECT /* unterminated",
    "SELECT \"unterminated",
    "SELECT $$unterminated",
    "\\copy t to x",
    "SELECT 1 " + "x" * (R.MAX_SQL_CHARS + 1),
])
def test_everything_that_writes_or_escapes_is_refused(sql):
    with pytest.raises(R.RemoteDbError):
        R.check_sql(sql)


def test_the_guard_cannot_be_fooled_by_comments_or_strings():
    # a comment may not hide the closing of the LIMIT wrapper
    chk = R.check_sql("SELECT 1 -- hola")
    wrapped = R.wrap(chk.clean, 10)
    assert "--" not in wrapped and wrapped.rstrip().endswith("LIMIT 11")
    # a keyword inside a string or a comment is not a keyword
    assert R.check_sql("SELECT 'DELETE FROM t' AS note").clean == "SELECT 'DELETE FROM t' AS note"
    assert R.check_sql("SELECT /* UPDATE */ 1").clean.replace(" ", "") == "SELECT1"
    # E'' strings with escaped quotes do not end early
    with pytest.raises(R.RemoteDbError):
        R.check_sql(r"SELECT E'it\'s'; DROP TABLE t")


def test_the_trailing_semicolon_is_dropped_and_the_statement_stays_one():
    assert R.check_sql("SELECT 1 ;  ").clean == "SELECT 1"
    assert R.check_sql("SELECT 1;").kind == "select"


# ------------------------------------------------------------------ the limit wrapper


def test_the_wrapper_asks_for_one_row_more_than_the_limit():
    sql = R.wrap("SELECT * FROM big", 500)
    assert sql == "SELECT * FROM (\nSELECT * FROM big\n) AS _sah_q LIMIT 501"
    assert R.wrap("VALUES (1)", 1).endswith("LIMIT 2")


# ------------------------------------------------------------------ configuration, secrets, network


def test_connection_settings_are_validated_and_clamped():
    ok = R.validate({"name": "Lab DB", "host": "db.example.org", "port": "5433", "database": "lab", "username": "reader",
                     "row_limit": 99999, "timeout_s": 9999})
    assert ok["port"] == 5433 and ok["row_limit"] == R.MAX_ROWS_HARD and ok["timeout_s"] == 120 and ok["sslmode"] == "require"
    for bad in ({"name": "", "host": "h", "database": "d", "username": "u"},
                {"name": "x", "host": "postgres://u:p@h/db", "database": "d", "username": "u"},
                {"name": "x", "host": "h/evil", "database": "d", "username": "u"},
                {"name": "x", "host": "h", "port": 70000, "database": "d", "username": "u"},
                {"name": "x", "host": "h", "database": "", "username": "u"},
                {"name": "x", "host": "h", "database": "d", "username": ""},
                {"name": "x", "host": "h", "database": "d", "username": "u", "sslmode": "bogus"},
                {"name": "x", "kind": "mysql", "host": "h", "database": "d", "username": "u"}):
        with pytest.raises(R.RemoteDbError):
            R.validate(bad)


def test_the_password_is_stored_encrypted_and_never_serialized(monkeypatch):
    monkeypatch.setattr(R, "encrypt_str", lambda s: "enc:" + s[::-1])
    c = SimpleNamespace(secret=None, id=uuid.uuid4(), name="n", kind="postgres", host="h", port=5432, database="d", username="u",
                        sslmode="require", row_limit=500, timeout_s=20, created_at=dt.datetime(2026, 1, 1))
    R.set_secret(c, "hunter2")
    assert c.secret == "enc:2retnuh"
    pub = R.public(c)
    assert pub["has_secret"] is True and "hunter2" not in str(pub) and "2retnuh" not in str(pub) and "secret" not in pub
    R.set_secret(c, None)
    assert c.secret == "enc:2retnuh"  # None keeps it
    R.set_secret(c, "")
    assert c.secret is None and R.public(c)["has_secret"] is False


def test_private_hosts_are_refused_unless_an_administrator_allowed_them(monkeypatch):
    from app.services import connectors

    monkeypatch.setattr(connectors.socket, "getaddrinfo", lambda host, port, **k: [(2, 1, 6, "", ("10.1.2.3", port))])
    monkeypatch.setattr(connectors, "get_setting", lambda db, k: [])
    c = SimpleNamespace(host="db.lan", port=5432, secret=None, database="d", username="u", sslmode="require", timeout_s=5)
    with pytest.raises(R.RemoteDbError) as e:
        R._connect(object(), c)
    assert e.value.code == "network" and "privada" in e.value.message
    monkeypatch.setattr(connectors.socket, "getaddrinfo", lambda host, port, **k: [(2, 1, 6, "", ("127.0.0.1", port))])
    with pytest.raises(R.RemoteDbError):
        R._connect(object(), c)  # loopback is never allowed, not even listed


# ------------------------------------------------------------------ formatting


def test_values_become_json_safe_and_big_ones_are_cut():
    assert R._cell(decimal.Decimal("1.50")) == "1.50"
    assert R._cell(dt.date(2026, 11, 5)) == "2026-11-05"
    assert R._cell(b"\x00\x01").startswith("<2 bytes")
    assert R._cell(uuid.UUID(int=1)).startswith("00000000")
    assert R._cell({"a": 1}) == '{"a": 1}'
    long = R._cell("x" * 5000)
    assert len(long) < 2100 and "caracteres más" in long
    assert R._cell(None) is None and R._cell(3) == 3


def test_markdown_escapes_cells_and_reports_cut_rows():
    md = R.as_markdown({"columns": ["a", "b"], "rows": [["x|y", None], ["z", "line\nbreak"]], "notes": ["Hay más filas"]})
    assert "x\\|y" in md and "line break" in md and md.endswith("Hay más filas")
    assert R.as_markdown({"columns": [], "rows": []}) == "(sin resultados)"


def test_schema_markdown_is_bounded_and_marks_unknown_row_counts():
    tables = [{"schema": "public", "name": f"t{i}", "type": "table", "approx_rows": None if i == 0 else 1200,
               "columns": [{"name": "id", "type": "integer", "nullable": False}, {"name": "note", "type": "text", "nullable": True}]} for i in range(400)]
    md = R.schema_markdown({"tables": tables, "truncated": True}, max_chars=2000)
    assert "## public.t0 (table)" in md and "~1.200 filas" in md and "id integer NOT NULL" in md
    assert len(md) < 2600 and "tablas más" in md
    assert R.schema_markdown({"tables": [], "truncated": False}).startswith("No hay tablas")


def test_finding_a_connection_by_name_or_id():
    uid = uuid.uuid4()
    row = SimpleNamespace(id=uuid.uuid4(), owner_id=uid, name="Lab")

    class DB:
        def get(self, model, key):
            return row if key == row.id else None

        def scalar(self, q):
            return row

    assert R.find(DB(), uid, str(row.id)) is row
    assert R.find(DB(), uid, "lab") is row
    assert R.find(DB(), uid, "") is None
    assert R.find(DB(), uuid.uuid4(), str(row.id)) is not None  # falls back to the name lookup, which is owner-scoped in SQL


# ------------------------------------------------------------------ the assistant's tools


class _Sess:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        return False


def _runner(monkeypatch, names=("Lab",)):
    from unittest.mock import MagicMock

    from app.services import agent

    t = agent.Turn(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), model_id=uuid.uuid4(), content="x")
    r = agent.Runner(t, lambda e: None, lambda: False)
    r.db_names = list(names)
    r.workspace = None
    r.flags = {"web": False, "skills": False, "agents": False, "auto_install": False, "code": False}
    db = MagicMock()
    monkeypatch.setattr(agent, "SessionLocal", lambda: _Sess(db))
    return agent, r, db


def test_the_db_tools_exist_only_when_the_user_has_a_connection(monkeypatch):
    agent, r, _ = _runner(monkeypatch)
    assert {"db_schema", "db_query"} <= {s["function"]["name"] for s in r.tool_specs()}
    assert r._tool_allowed("db_query")
    r.db_names = []
    assert not ({"db_schema", "db_query"} & {s["function"]["name"] for s in r.tool_specs()})
    assert not r._tool_allowed("db_query") and not r._tool_allowed("db_schema")
    # small models keep them too (they are core tools, and only exist with a connection)
    assert {"db_schema", "db_query"} <= agent.CORE_TOOLS


def test_the_db_tools_have_labels_in_the_server_and_the_ui():
    from app.services import agent

    assert agent.TOOL_LABELS["db_query"] and agent.TOOL_LABELS["db_schema"]
    import pathlib

    ui = pathlib.Path(__file__).resolve().parents[2] / "frontend/components/live-activity.tsx"
    if ui.exists():
        assert "db_query" in ui.read_text()


def test_db_query_runs_remotely_audits_metadata_and_reports_errors(monkeypatch):
    agent, r, _ = _runner(monkeypatch)
    conn = SimpleNamespace(id=uuid.uuid4(), name="Lab")
    calls, audits = [], []
    monkeypatch.setattr(agent.remote_db, "find", lambda db, uid, name: conn if name.lower() == "lab" else None)
    monkeypatch.setattr(agent.remote_db, "query", lambda db, c, sql: calls.append(sql) or {"columns": ["n"], "rows": [[42]], "row_count": 1, "truncated": True, "ms": 5, "notes": ["Hay más filas"]})
    monkeypatch.setattr("app.audit.audit", lambda db, action, **k: audits.append((action, k)))
    out = r.tool_db_query("c1", {"connection": "lab", "sql": "SELECT count(*) AS n FROM orders"})
    assert out["ok"] and calls == ["SELECT count(*) AS n FROM orders"] and "42" in out["table"] and "(hay más)" in out["summary"]
    assert audits[0][0] == "databases.query" and audits[0][1]["details"]["rows"] == 1 and "orders" not in str(audits[0][1]["details"])  # metadata only
    # a refused statement is reported to the model, not raised
    monkeypatch.setattr(agent.remote_db, "query", lambda db, c, sql: (_ for _ in ()).throw(R.RemoteDbError("No se permite «DELETE»", "invalid")))
    bad = r.tool_db_query("c2", {"connection": "Lab", "sql": "DELETE FROM orders"})
    assert not bad["ok"] and "DELETE" in bad["content"] and audits[-1][1]["success"] is False
    with pytest.raises(agent.AgentError):
        r.tool_db_query("c3", {"connection": "nope", "sql": "SELECT 1"})


def test_db_schema_tool(monkeypatch):
    agent, r, _ = _runner(monkeypatch)
    monkeypatch.setattr(agent.remote_db, "find", lambda db, uid, name: SimpleNamespace(id=uuid.uuid4(), name="Lab"))
    seen = {}
    monkeypatch.setattr(agent.remote_db, "schema", lambda db, c, table: seen.update(table=table) or {"tables": [{"schema": "public", "name": "orders", "type": "table", "approx_rows": 10, "columns": [{"name": "id", "type": "integer", "nullable": False}]}], "truncated": False})
    out = r.tool_db_schema("c", {"connection": "Lab", "table": "orders"})
    assert out["ok"] and "public.orders" in out["content"] and seen["table"] == "orders"


def test_the_prompt_names_the_connections_but_never_their_credentials(monkeypatch):
    import inspect

    from app.services import agent

    src = inspect.getsource(agent.Runner._system)
    assert "self.db_names" in src and "descargue" in src and "password" not in src.lower()


# ------------------------------------------------------------------ the MCP server


def test_mcp_exposes_the_database_tools_with_the_same_guards(monkeypatch):
    from unittest.mock import MagicMock

    from app.routers import mcp_server as M

    assert {"list_databases", "db_schema", "db_query"} <= {t["name"] for t in M.TOOLS}
    user = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.get.return_value = user
    monkeypatch.setattr(M, "SessionLocal", lambda: _Sess(db))
    conn = SimpleNamespace(id=uuid.uuid4(), name="Lab")
    monkeypatch.setattr(M.remote_db, "find", lambda d, uid, name: conn if name == "Lab" else None)
    monkeypatch.setattr(M, "audit", lambda *a, **k: None)
    monkeypatch.setattr(M.remote_db, "query", lambda d, c, sql: {"columns": ["a"], "rows": [[1]], "row_count": 1, "truncated": False, "ms": 1, "notes": []})
    out = M.call_tool(user.id, "db_query", {"connection": "Lab", "sql": "SELECT 1 AS a"})
    assert "| a |" in out[0]["text"]
    with pytest.raises(M.ToolError):
        M.call_tool(user.id, "db_query", {"connection": "other", "sql": "SELECT 1"})

    def refuse(d, c, sql):
        raise R.RemoteDbError("No se permite «DROP»")

    monkeypatch.setattr(M.remote_db, "query", refuse)
    with pytest.raises(M.ToolError) as e:
        M.call_tool(user.id, "db_query", {"connection": "Lab", "sql": "DROP TABLE t"})
    assert "DROP" in str(e.value)


# ------------------------------------------------------------------ the API never returns the password


def test_the_api_creates_connections_without_ever_returning_the_password(monkeypatch):
    from unittest.mock import MagicMock

    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from app.routers import databases as D

    monkeypatch.setattr(D.connectors, "resolve", lambda db, host, port: "93.184.216.34")
    monkeypatch.setattr(D, "audit", lambda *a, **k: None)
    monkeypatch.setattr(R, "encrypt_str", lambda s: "ENC")
    db = MagicMock()
    db.scalar.return_value = 0
    p = SimpleNamespace(id=uuid.uuid4(), user=SimpleNamespace(id=uuid.uuid4()))
    body = D.DbIn(name="Lab", host="db.example.org", database="lab", username="reader", password="s3cret-pw")
    out = D.create_database(body, MagicMock(), p, db)
    assert "s3cret-pw" not in str(out) and out["has_secret"] is True and "secret" not in out and "password" not in out
    added = db.add.call_args[0][0]
    assert added.secret == "ENC" and added.owner_id == p.id
    # a duplicate name is a clean 409, not a 500
    db.commit.side_effect = IntegrityError("x", {}, Exception("dup"))
    with pytest.raises(HTTPException) as e:
        D.create_database(body, MagicMock(), p, db)
    assert e.value.status_code == 409
    # a private/loopback host is refused at creation
    from app.services import connectors

    monkeypatch.setattr(D.connectors, "resolve", lambda db, host, port: (_ for _ in ()).throw(connectors.ConnectorError("dirección no permitida")))
    db2 = MagicMock()
    db2.scalar.return_value = 0
    with pytest.raises(HTTPException) as e2:
        D.create_database(body, MagicMock(), p, db2)
    assert e2.value.status_code == 422 and e2.value.detail["code"] == "network"
    # too many connections
    db3 = MagicMock()
    db3.scalar.return_value = D.MAX_PER_USER
    with pytest.raises(HTTPException) as e3:
        D.create_database(body, MagicMock(), p, db3)
    assert e3.value.status_code == 409


def test_a_query_over_the_api_is_audited_by_size_never_by_content(monkeypatch):
    from unittest.mock import MagicMock

    from app.routers import databases as D

    events = []
    monkeypatch.setattr(D, "audit", lambda db, action, **k: events.append((action, k)))
    conn = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="Lab")
    db = MagicMock()
    db.get.return_value = conn
    monkeypatch.setattr(D.rdb, "query", lambda d, c, sql: {"columns": ["salary"], "rows": [[123456]], "row_count": 1, "truncated": False, "limit": 500, "ms": 3, "notes": []})
    p = SimpleNamespace(id=conn.owner_id, user=SimpleNamespace(id=conn.owner_id))
    out = D.query_database(conn.id, D.QueryIn(sql="SELECT salary FROM staff"), MagicMock(), p, db)
    assert "123456" in out["markdown"]
    assert events[0][0] == "databases.query" and "123456" not in str(events) and "salary" not in str(events[0][1]["details"])


# ------------------------------------------------------------------ a database file is never copied or read as text


def test_connectors_never_download_database_files_or_big_dumps():
    from app.services import connectors as C

    for name in ("data/lab.sqlite", "x/app.db", "a.sqlite3", "b.mdb", "c.accdb", "backup.dump", "old.bak"):
        assert C.skip_reason(name, 10) and "Bases de datos" in C.skip_reason(name, 10)
    assert C.skip_reason("dump.sql", 50 * 1024 * 1024)
    assert C.skip_reason("schema.sql", 40_000) is None  # a small script is a normal code file
    assert C.skip_reason("report.pdf", 10_000_000) is None and C.skip_reason("data.csv", 90_000_000) is None


def test_big_dumps_and_big_tables_are_not_turned_into_searchable_text(tmp_path, monkeypatch):
    from app.services import files as F

    big = tmp_path / "dump.sql"
    big.write_bytes(b"INSERT INTO t VALUES (1);\n" * 200_000)  # ~5 MB
    monkeypatch.setattr(F, "abs_path", lambda f: big)
    out = F.process(SimpleNamespace(kind="document"))
    assert out["status"] == "unsupported" and "Bases de datos" in out["reason"] and out["chars"] == 0
    assert F.skip_text_reason(".csv", 30 * 1024 * 1024) and "describe_data" in F.skip_text_reason(".csv", 30 * 1024 * 1024)
    assert F.skip_text_reason(".csv", 1_000_000) is None and F.skip_text_reason(".sql", 1000) is None and F.skip_text_reason(".pdf", 10**9) is None
    # SQLite and Parquet were never text-indexed
    small = tmp_path / "x.sqlite"
    small.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    monkeypatch.setattr(F, "abs_path", lambda f: small)
    assert F.process(SimpleNamespace(kind="data"))["status"] == "unsupported"
