"""AI Playground: private conversations with streaming, saved prompts, export."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import Principal, current_principal, not_found, require_researcher, visible_filter
from ..engines import LLM_ENGINES, EngineError
from ..models import Conversation, Message, RegisteredModel, SavedPrompt, SharedResource, Visibility
from ..services import llm, scheduler, turns
from .openai_gateway import _record

router = APIRouter(prefix="/api/v1/playground", tags=["playground"])


class ConvIn(BaseModel):
    title: str = Field(default="", max_length=255)
    model_alias: str | None = Field(default=None, max_length=128)
    settings: dict = {}
    project_id: uuid.UUID | None = None


class ConvPatch(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    model_alias: str | None = None
    settings: dict | None = None
    visibility: Visibility | None = None
    project_id: uuid.UUID | None = None


class SendIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    model_alias: str = Field(max_length=128)
    system_prompt: str | None = Field(default=None, max_length=50_000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    stop: list[str] | None = Field(default=None, max_length=4)
    seed: int | None = None
    attachments: list[uuid.UUID] = Field(default=[], max_length=20)
    mode: str = Field(default="chat", pattern="^(chat|image)$")
    tools: bool = True
    thinking: bool | None = None
    aspect: str = Field(default="square", pattern="^(square|landscape|portrait|wide|tall)$")
    context_limit: int | None = Field(default=None, ge=2048, le=1_048_576)
    auto_compact: bool = True
    web: bool = True
    research: bool = False


class PromptIn(BaseModel):
    title: str = Field(max_length=255)
    content: str = Field(max_length=200_000)
    system_prompt: str | None = Field(default=None, max_length=50_000)
    tags: list[str] = []


def _conv(db: Session, p: Principal, cid: uuid.UUID, write: bool = False) -> Conversation:
    c = db.get(Conversation, cid)
    if not c:
        raise not_found("conversation")
    if c.owner_id == p.id:
        return c
    if write:
        raise not_found("conversation")
    if c.visibility == Visibility.public:
        return c
    if c.visibility == Visibility.shared and db.scalar(select(SharedResource.id).where(
            SharedResource.resource_type == "conversation", SharedResource.resource_id == c.id,
            SharedResource.grantee_id == p.id)):
        return c
    raise not_found("conversation")


def _conv_pub(c: Conversation) -> dict:
    return {"id": str(c.id), "title": c.title, "model_alias": c.model_alias, "settings": c.settings,
            "visibility": c.visibility.value, "owner_id": str(c.owner_id), "created_at": c.created_at,
            "updated_at": c.updated_at, "project_id": str(c.project_id) if c.project_id else None,
            "summary": {"text": c.summary, "upto": c.summary_upto, **(c.summary_meta or {})} if c.summary else None}


@router.get("/conversations")
def list_conversations(p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                       project_id: uuid.UUID | None = None):
    q = select(Conversation).where(Conversation.owner_id == p.id)
    if project_id:
        q = q.where(Conversation.project_id == project_id)
    rows = db.scalars(q.order_by(Conversation.updated_at.desc()).limit(500))
    shared = db.scalars(select(Conversation).where(Conversation.owner_id != p.id,
                                                   visible_filter(Conversation, p, "conversation")).limit(100))
    return {"mine": [_conv_pub(c) for c in rows], "shared": [_conv_pub(c) for c in shared]}


@router.get("/search")
def search_conversations(q: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db), limit: int = 30):
    """Full-text search over the user's own chats (titles and messages), newest first, with a snippet."""
    from sqlalchemy import text as sql

    q = q.strip()[:200]
    if not q:
        return []
    rows = db.execute(sql("""
        SELECT c.id, c.title, c.updated_at, m.id AS mid, m.role, left(m.content, 4000) AS content
        FROM conversations c JOIN messages m ON m.conversation_id = c.id
        WHERE c.owner_id = :uid AND (to_tsvector('simple', m.content) @@ websearch_to_tsquery('simple', :q)
              OR m.content ILIKE :like OR to_tsvector('simple', c.title) @@ websearch_to_tsquery('simple', :q))
        ORDER BY c.updated_at DESC, m.id DESC LIMIT 300"""),
        {"uid": p.id, "q": q, "like": f"%{q.replace('%', '')}%"}).all()
    out: dict = {}
    words = [w.lower() for w in q.split() if w]
    for r in rows:
        if r.id in out or len(out) >= min(limit, 100):
            continue
        low = r.content.lower()
        pos = min([low.find(w) for w in words if low.find(w) >= 0] or [0])
        snippet = ("…" if pos > 60 else "") + r.content[max(0, pos - 60):pos + 180] + "…"
        out[r.id] = {"id": str(r.id), "title": r.title, "updated_at": r.updated_at, "message_id": r.mid,
                     "role": r.role, "snippet": snippet}
    return list(out.values())


@router.post("/conversations")
def create_conversation(body: ConvIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if body.project_id:
        from .projects import project_for_read

        project_for_read(db, p, body.project_id)
    c = Conversation(owner_id=p.id, title=body.title or "Nueva conversación", model_alias=body.model_alias,
                     settings=body.settings, project_id=body.project_id)
    db.add(c)
    db.commit()
    return _conv_pub(c)


@router.get("/conversations/{cid}")
def get_conversation(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = _conv(db, p, cid)
    msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
    t = turns.active(c.id) if c.owner_id == p.id else None
    return {**_conv_pub(c), "active_turn": t if t and t.get("status") == "running" else None, "messages": [{"id": m.id, "role": m.role, "content": m.content, "model_alias": m.model_alias,
                                          "metrics": m.metrics, "attachments": m.attachments, "parts": m.parts,
                                          "created_at": m.created_at} for m in msgs]}


@router.patch("/conversations/{cid}")
def patch_conversation(cid: uuid.UUID, body: ConvPatch, p: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    c = _conv(db, p, cid, write=True)
    data = body.model_dump(exclude_unset=True)
    if data.get("project_id"):
        from .projects import project_for_read

        project_for_read(db, p, data["project_id"])
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    return _conv_pub(c)


@router.delete("/conversations/{cid}")
def delete_conversation(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = _conv(db, p, cid, write=True)
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.get("/conversations/{cid}/export")
def export_conversation(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                        fmt: str = "json"):
    data = get_conversation(cid, p, db)
    if fmt == "md":
        lines = [f"# {data['title']}", ""]
        for m in data["messages"]:
            lines += [f"**{m['role']}**" + (f" ({m['model_alias']})" if m.get("model_alias") else ""), "", m["content"], ""]
        return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="conversation-{cid}.md"'})
    return PlainTextResponse(json.dumps(data, default=str, ensure_ascii=False, indent=2), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="conversation-{cid}.json"'})


@router.post("/conversations/{cid}/attachments")
async def upload_attachment(cid: uuid.UUID, file: UploadFile = File(...), p: Principal = Depends(require_researcher),
                            db: Session = Depends(get_db)):
    """Attach a file to the next message: stored in the drive (folder "Chat"), text extracted at once."""
    from ..services import files as files_svc
    from .files import store_upload

    c = _conv(db, p, cid, write=True)
    f = await store_upload(db, p, file, folder="Chat", project_id=c.project_id, source="chat", index=False)
    return files_svc.public(f)


@router.post("/conversations/{cid}/messages")
async def send_message(cid: uuid.UUID, body: SendIn, request: Request, p: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    from ..models import UserFile
    from ..services import agent

    c = _conv(db, p, cid, write=True)
    q = select(RegisteredModel).where(RegisteredModel.alias == body.model_alias)
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    model = db.scalar(q)
    if not model or model.backend not in LLM_ENGINES or "chat" not in (model.capabilities or []):
        raise HTTPException(404, detail={"code": "model_not_found", "message": "Chat model not found or not accessible"})
    attachments = []
    for fid in body.attachments:
        f = db.get(UserFile, fid)
        if not f or f.owner_id != p.id:
            raise HTTPException(404, detail={"code": "attachment_not_found", "message": "Attachment not found"})
        attachments.append({"file_id": str(f.id), "name": f.filename, "mime": f.mime, "kind": f.kind,
                            "size_bytes": f.size_bytes, "url": f"/api/v1/files/{f.id}/content"})
    db.add(Message(conversation_id=c.id, role="user", content=body.content, attachments=attachments or None))
    if not c.title or c.title == "Nueva conversación":
        c.title = body.content[:60]
    c.model_alias = model.alias
    c.settings = {k: v for k, v in body.model_dump(mode="json").items() if k not in ("content", "attachments")}
    db.commit()
    params = {k: v for k, v in {"temperature": body.temperature, "top_p": body.top_p, "max_tokens": body.max_tokens,
                                "stop": body.stop, "seed": body.seed}.items() if v is not None}
    if body.mode == "image":
        params["aspect"] = body.aspect
    lang = (p.user.preferences or {}).get("language") or "es"
    turn = agent.Turn(user_id=p.id, conversation_id=c.id, model_id=model.id, content=body.content,
                      attachments=list(body.attachments), system_prompt=body.system_prompt, params=params,
                      use_tools=body.tools, thinking=body.thinking, mode=body.mode, lang=lang,
                      context_limit=body.context_limit, auto_compact=body.auto_compact, web=body.web,
                      research=body.research)
    title = c.title
    try:
        turns.start(cid, p.id, title)
    except RuntimeError:
        raise HTTPException(409, detail={"code": "turn_running", "message": "This chat is still answering the previous message"})
    threading.Thread(target=_run_turn, args=(turn, p, body.model_alias, title), daemon=True).start()
    return StreamingResponse(_tail(cid, request, 0), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_turn(turn, p: Principal, model_alias: str, title: str) -> None:
    """Runs independently of any HTTP connection; the browser only follows the Redis event log."""
    from ..services import agent
    from ..services.notify import notify

    cid = turn.conversation_id
    t0 = time.perf_counter()
    status = "done"
    try:
        r = agent.run_turn(turn, lambda ev: turns.emit(cid, ev), turns.cancelled_checker(cid))
        with SessionLocal() as s2:
            msg = Message(conversation_id=cid, role="assistant", content=r["text"], model_alias=model_alias,
                          metrics={**r["metrics"], "reasoning": r.get("reasoning"),
                                   **({"cancelled": True} if turns.cancelled_checker(cid)() else {})},
                          parts=r["parts"] or None)
            s2.add(msg)
            conv = s2.get(Conversation, cid)
            if conv:
                conv.updated_at = time_now()
            s2.commit()
            mid = msg.id
        m = r["metrics"]
        _record(p, "playground.chat", model_alias, 200,
                {"prompt_tokens": m.get("prompt_tokens"), "completion_tokens": m.get("completion_tokens")}, t0,
                source="playground")
        turns.emit(cid, {"type": "done", "metrics": m, "reasoning": r.get("reasoning"), "message_id": mid})
    except agent.AgentError as e:
        status = "error"
        turns.emit(cid, {"type": "error", "message": e.message, "remediation": e.remediation})
    except (scheduler.SchedulingError, EngineError) as e:
        status = "error"
        turns.emit(cid, {"type": "error", "message": e.message, "remediation": getattr(e, "remediation", None)})
    except Exception as e:  # noqa: BLE001
        status = "error"
        turns.emit(cid, {"type": "error", "message": str(e)[:1000]})
    finally:
        turns.finish(cid, p.id, status)
        if time.perf_counter() - t0 > 30:  # long turns (images, model loads): leave a notice in the bell
            try:
                with SessionLocal() as s3:
                    notify(s3, p.id, "chat", ("Respuesta lista: " if status == "done" else "El chat terminó con error: ") + title[:80],
                           link=f"/playground?c={cid}", email=False)
                    s3.commit()
            except Exception:  # noqa: BLE001
                pass


def time_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def _tail(cid, request: Request, start_at: int):
    """SSE: replay the turn's events from `start_at`, then follow until done/error. Leaving does not cancel."""
    idx = start_at
    last_beat = time.time()
    while True:
        if await request.is_disconnected():
            return
        evs = await asyncio.to_thread(turns.events, cid, idx)
        for ev in evs:
            idx += 1
            yield f"data: {json.dumps({**ev, 'seq': idx}, ensure_ascii=False, default=str)}\n\n"
            if ev.get("type") in ("done", "error"):
                return
        if not evs:
            info = await asyncio.to_thread(turns.active, cid)
            if not info or info.get("status") != "running":
                if not info:
                    yield f"data: {json.dumps({'type': 'gone'})}\n\n"
                    return
                await asyncio.sleep(0.5)  # finished: drain the final events on the next pass
                if not await asyncio.to_thread(turns.events, cid, idx):
                    return
                continue
            if time.time() - last_beat > 15:
                last_beat = time.time()
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.3)


@router.get("/conversations/{cid}/stream")
async def stream_turn(cid: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db), start: int = 0):
    """Re-attach to a running (or just finished) turn, e.g. after coming back to the chat page."""
    _conv(db, p, cid, write=True)
    if not turns.active(cid):
        raise HTTPException(404, detail={"code": "no_turn", "message": "No running answer for this chat"})
    return StreamingResponse(_tail(cid, request, max(0, start)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/conversations/{cid}/cancel")
def cancel_turn(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _conv(db, p, cid, write=True)
    turns.cancel(cid)
    return {"ok": True}


class CompactIn(BaseModel):
    model_alias: str | None = Field(default=None, max_length=128)
    context_limit: int | None = Field(default=None, ge=2048, le=1_048_576)


@router.post("/conversations/{cid}/compact")
def compact_now(cid: uuid.UUID, body: CompactIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Summarise the older messages now (the model keeps the summary plus the latest messages; nothing is deleted)."""
    from ..services import agent

    c = _conv(db, p, cid, write=True)
    if turns.active(cid) and (turns.active(cid) or {}).get("status") == "running":
        raise HTTPException(409, detail={"code": "turn_running", "message": "Wait until the current answer finishes"})
    alias = body.model_alias or c.model_alias
    q = select(RegisteredModel).where(RegisteredModel.alias == alias)
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    model = db.scalar(q)
    if not model or model.backend not in LLM_ENGINES:
        raise HTTPException(404, detail={"code": "model_not_found", "message": "Chat model not found"})
    _ = model.runtime_config
    ctx = agent.effective_context(model, body.context_limit)
    try:
        info = agent.compact_conversation(c.id, model.id, ctx, force=True, exclude_last_user=False)
    except (scheduler.SchedulingError, EngineError) as e:
        raise HTTPException(503, detail={"code": "engine_unavailable", "message": e.message,
                                         "remediation": getattr(e, "remediation", None)})
    if not info:
        return {"ok": True, "compacted": False, "message": "Nothing to summarise yet"}
    return {"ok": True, "compacted": True, **info}


@router.delete("/conversations/{cid}/summary")
def clear_summary(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Forget the summary: the model sees the full history again (trimmed to the window if needed)."""
    c = _conv(db, p, cid, write=True)
    c.summary, c.summary_upto, c.summary_meta = None, None, None
    db.commit()
    return {"ok": True}


@router.get("/conversations/{cid}/context")
def context_info(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                 model_alias: str | None = None, context_limit: int | None = None):
    """Estimated tokens the next message will use (history after the summary + summary) vs the window."""
    from ..services import agent

    c = _conv(db, p, cid, write=True)
    model = db.scalar(select(RegisteredModel).where(RegisteredModel.alias == (model_alias or c.model_alias)))
    msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
    live = [m for m in msgs if c.summary_upto is None or m.id > c.summary_upto]
    hist = sum(agent.est_tokens(agent._message_text(m)) for m in live)
    out = {"messages": len(msgs), "messages_in_context": len(live), "history_tokens": hist,
           "summary_tokens": agent.est_tokens(c.summary) if c.summary else 0, "summary": bool(c.summary),
           "summary_meta": c.summary_meta}
    if model and model.backend in LLM_ENGINES:
        _ = model.runtime_config
        out["model_window"] = llm.context_tokens(model)
        out["limit"] = agent.effective_context(model, context_limit)
    last = next((m for m in reversed(msgs) if m.role == "assistant" and (m.metrics or {}).get("context_used")), None)
    if last:
        out["last_used"] = last.metrics["context_used"]
    return out


@router.get("/active")
def active_turns(p: Principal = Depends(current_principal)):
    """Chats of this user that are still working (header indicator)."""
    return turns.running_for_user(p.id)


@router.get("/prompts")
def list_prompts(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return [{"id": str(s.id), "title": s.title, "content": s.content, "system_prompt": s.system_prompt, "tags": s.tags,
             "created_at": s.created_at} for s in db.scalars(select(SavedPrompt).where(SavedPrompt.owner_id == p.id)
                                                            .order_by(SavedPrompt.created_at.desc()))]


@router.post("/prompts")
def create_prompt(body: PromptIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s = SavedPrompt(owner_id=p.id, **body.model_dump())
    db.add(s)
    db.commit()
    return {"id": str(s.id)}


@router.delete("/prompts/{pid}")
def delete_prompt(pid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s = db.get(SavedPrompt, pid)
    if not s or s.owner_id != p.id:
        raise not_found("prompt")
    db.delete(s)
    db.commit()
    return {"ok": True}
