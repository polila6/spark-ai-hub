"""Progress of a long turn, saved every few steps so a server restart does not lose the task.

A turn runs inside the backend process; a restart (a deploy) kills it, and the assistant's message is only stored when the turn
ends. With a checkpoint, what the turn had done (its comments and steps, its plan) is kept in the database; after the restart
the chat offers «Reanudar»: the partial answer becomes a real message (so the model reads it in the history) and a new turn
goes on from there, with the files of the workspace exactly as they were left. One row per conversation, deleted when the turn
ends by itself."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Message, TurnCheckpoint

EVERY_STEPS = 6  # save after this many steps...
EVERY_SECONDS = 240  # ...or this many seconds, whichever comes first


def save(cid, user_id, user_message_id: int | None, model_alias: str | None, step: int, text: str, parts: list, metrics: dict) -> None:
    """Upsert the checkpoint of a conversation (own session: never disturbs the turn; failures are silent)."""
    try:
        payload = json.loads(json.dumps({"text": text, "parts": parts, "metrics": metrics}, default=str))
        with SessionLocal() as db:
            row = db.get(TurnCheckpoint, cid)
            if row is None:
                row = TurnCheckpoint(conversation_id=cid, user_id=user_id)
                db.add(row)
            row.user_message_id, row.model_alias, row.step = user_message_id, model_alias, step
            row.payload, row.updated_at = payload, datetime.now(timezone.utc)
            db.commit()
    except Exception:  # noqa: BLE001
        pass


def discard(cid) -> None:
    try:
        with SessionLocal() as db:
            db.execute(delete(TurnCheckpoint).where(TurnCheckpoint.conversation_id == cid))
            db.commit()
    except Exception:  # noqa: BLE001
        pass


def info(db: Session, cid) -> dict | None:
    """What the chat shows about an interrupted task (None when there is no checkpoint)."""
    row = db.get(TurnCheckpoint, cid)
    if row is None:
        return None
    parts = (row.payload or {}).get("parts") or []
    return {"step": row.step, "updated_at": row.updated_at, "tools": sum(1 for p in parts if p.get("type") == "tool"),
            "comments": sum(1 for p in parts if p.get("type") == "say")}


def materialize(db: Session, cid) -> Message | None:
    """Turn the checkpoint into the assistant message it was going to be (flagged `interrupted`), and delete it. No commit."""
    row = db.get(TurnCheckpoint, cid)
    if row is None:
        return None
    pl = row.payload or {}
    text = (pl.get("text") or "").strip()
    msg = Message(conversation_id=cid, role="assistant", content=text or "(Respuesta interrumpida antes de escribir texto.)",
                  model_alias=row.model_alias, parts=pl.get("parts") or None,
                  metrics={**(pl.get("metrics") or {}), "interrupted": True, "steps": row.step})
    db.add(msg)
    db.delete(row)
    return msg


RESUME_TEXT_ES = ("Continúa la tarea desde donde se interrumpió (el servidor se reinició). Revisa tu plan y el estado real del "
                  "workspace, no repitas lo ya hecho y sigue hasta cumplir todos los objetivos.")
RESUME_TEXT_EN = ("Carry on with the task from where it was interrupted (the server restarted). Check your plan and the real state "
                  "of the workspace, do not repeat what is done, and go on until every goal is met.")
