"""Remote databases: connect once, then ask questions that run on the remote server (read-only, row-limited).

The Spark never downloads, syncs or indexes these databases; see services/remote_db.py for the guarantees."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import DbConnection
from ..services import connectors
from ..services import remote_db as rdb
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])
MAX_PER_USER = 10


class DbIn(BaseModel):
    name: str = Field(max_length=80)
    kind: str = "postgres"
    host: str = Field(max_length=255)
    port: int = 5432
    database: str = Field(max_length=128)
    username: str = Field(max_length=128)
    password: str | None = Field(default=None, max_length=500)  # write-only: None keeps the stored one, "" clears it
    sslmode: str = "require"
    row_limit: int = 500
    timeout_s: int = 20


class QueryIn(BaseModel):
    sql: str = Field(max_length=rdb.MAX_SQL_CHARS + 1000)


def _err(e: rdb.RemoteDbError, status: int = 422) -> HTTPException:
    return HTTPException(status, detail={"code": e.code, "message": e.message})


def _own(db: Session, p: Principal, cid: uuid.UUID) -> DbConnection:
    c = db.get(DbConnection, cid)
    if not c or c.owner_id != p.id:
        raise not_found("database connection")
    return c


def _apply(db: Session, c: DbConnection, body: DbIn) -> None:
    try:
        cfg = rdb.validate(body.model_dump())
        connectors.resolve(db, cfg["host"], cfg["port"])  # refuse loopback / unlisted private hosts right away
    except rdb.RemoteDbError as e:
        raise _err(e)
    except connectors.ConnectorError as e:
        raise HTTPException(422, detail={"code": "network", "message": e.message})
    for k, v in cfg.items():
        setattr(c, k, v)
    rdb.set_secret(c, body.password)


@router.get("")
def list_databases(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(DbConnection).where(DbConnection.owner_id == p.id).order_by(DbConnection.name)).all()
    return {"databases": [rdb.public(c) for c in rows], "private_hosts": get_setting(db, "connector_private_hosts") or []}


@router.post("")
def create_database(body: DbIn, request: Request, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if (db.scalar(select(func.count()).select_from(DbConnection).where(DbConnection.owner_id == p.id)) or 0) >= MAX_PER_USER:
        raise HTTPException(409, detail={"code": "too_many", "message": f"Máximo {MAX_PER_USER} conexiones por usuario"})
    c = DbConnection(owner_id=p.id)
    _apply(db, c, body)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={"code": "name_taken", "message": "Ya tienes una conexión con ese nombre"})
    audit(db, "databases.create", actor=p.user, request=request, target_type="db_connection", target_id=str(c.id),
          details={"name": c.name, "host": c.host, "port": c.port, "database": c.database, "user": c.username})
    return rdb.public(c)


@router.put("/{cid}")
def update_database(cid: uuid.UUID, body: DbIn, request: Request, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _own(db, p, cid)
    _apply(db, c, body)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={"code": "name_taken", "message": "Ya tienes una conexión con ese nombre"})
    audit(db, "databases.update", actor=p.user, request=request, target_type="db_connection", target_id=str(c.id),
          details={"name": c.name, "host": c.host, "database": c.database, "password_changed": body.password is not None})
    return rdb.public(c)


@router.delete("/{cid}")
def delete_database(cid: uuid.UUID, request: Request, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _own(db, p, cid)
    name = c.name
    db.delete(c)
    db.commit()
    audit(db, "databases.delete", actor=p.user, request=request, target_type="db_connection", target_id=str(cid), details={"name": name})
    return {"ok": True}


@router.post("/{cid}/test")
def test_database(cid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _own(db, p, cid)
    try:
        return rdb.test(db, c)
    except rdb.RemoteDbError as e:
        return {"ok": False, "code": e.code, "message": e.message}


@router.get("/{cid}/schema")
def database_schema(cid: uuid.UUID, table: str | None = None, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _own(db, p, cid)
    try:
        res = rdb.schema(db, c, table)
    except rdb.RemoteDbError as e:
        raise _err(e, 502 if e.code in ("connect", "timeout") else 422)
    return {**res, "markdown": rdb.schema_markdown(res)}


@router.post("/{cid}/query")
def query_database(cid: uuid.UUID, body: QueryIn, request: Request, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _own(db, p, cid)
    try:
        res = rdb.query(db, c, body.sql)
    except rdb.RemoteDbError as e:
        audit(db, "databases.query", actor=p.user, request=request, target_type="db_connection", target_id=str(c.id),
              success=False, details={"name": c.name, "sql_chars": len(body.sql), "code": e.code})
        raise _err(e, 502 if e.code in ("connect", "timeout") else 422)
    audit(db, "databases.query", actor=p.user, request=request, target_type="db_connection", target_id=str(c.id),
          details={"name": c.name, "sql_chars": len(body.sql), "rows": res["row_count"], "truncated": res["truncated"], "ms": res["ms"]})
    return {**res, "markdown": rdb.as_markdown(res)}
