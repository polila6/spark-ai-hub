"""Scheduled tasks: the assistant runs a prompt on a schedule (with web, code, documents...) in its own chat, then the
user gets a notification and, optionally, the answer by e-mail. The worker checks for due tasks every 30 s."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..logging_setup import log
from ..models import ApiUsage, Conversation, Message, ModelStatus, RegisteredModel, ScheduledTask, User
from ..settings_store import get_setting
from . import turns

FREQS = ("hourly", "daily", "weekly", "monthly")
MAX_RUNNING = 2
_running: set[uuid.UUID] = set()
_lock = threading.Lock()


class TaskError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Madrid")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Madrid")


def validate(schedule: dict, tz: str) -> dict:
    freq = schedule.get("freq")
    if freq not in FREQS:
        raise TaskError("Frecuencia no válida")
    out: dict = {"freq": freq}
    if freq == "hourly":
        out["minute"] = int(schedule.get("minute", 0)) % 60
        out["every"] = max(1, min(24, int(schedule.get("every", 1))))
    else:
        hh, _, mm = str(schedule.get("time") or "08:00").partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise TaskError("Hora no válida (HH:MM)")
        out["time"] = f"{int(hh):02d}:{int(mm):02d}"
    if freq == "daily":
        days = sorted({int(d) for d in (schedule.get("weekdays") or range(7)) if 0 <= int(d) <= 6})
        out["weekdays"] = days or list(range(7))
    if freq == "weekly":
        out["weekday"] = int(schedule.get("weekday", 0)) % 7
    if freq == "monthly":
        out["day"] = max(1, min(28, int(schedule.get("day", 1))))
    _tz(tz)
    return out


def next_run(schedule: dict, tz: str, after: datetime | None = None) -> datetime:
    """Next occurrence strictly after `after` (UTC), computed in the task's time zone."""
    zone = _tz(tz)
    now = (after or datetime.now(timezone.utc)).astimezone(zone)
    freq = schedule["freq"]
    if freq == "hourly":
        every = schedule.get("every", 1)
        cand = now.replace(minute=schedule.get("minute", 0), second=0, microsecond=0)
        while cand <= now or cand.hour % every:
            cand += timedelta(hours=1)
        return cand.astimezone(timezone.utc)
    hh, mm = (int(x) for x in schedule["time"].split(":"))
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for _ in range(400):
        if cand > now:
            ok = (freq == "daily" and cand.weekday() in schedule.get("weekdays", range(7))) or \
                 (freq == "weekly" and cand.weekday() == schedule.get("weekday", 0)) or \
                 (freq == "monthly" and cand.day == schedule.get("day", 1))
            if ok:
                # re-localise (DST changes): keep the wall-clock time
                return datetime(cand.year, cand.month, cand.day, hh, mm, tzinfo=zone).astimezone(timezone.utc)
        cand += timedelta(days=1)
    raise TaskError("No se pudo calcular la próxima ejecución")


def describe(schedule: dict, lang: str = "es") -> str:
    es = lang != "en"
    names = (["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"] if es else
             ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    f = schedule.get("freq")
    if f == "hourly":
        n = schedule.get("every", 1)
        return (f"cada {n} h en el minuto {schedule.get('minute', 0)}" if es else f"every {n} h at minute {schedule.get('minute', 0)}") \
            if n > 1 else (f"cada hora en el minuto {schedule.get('minute', 0)}" if es else f"hourly at minute {schedule.get('minute', 0)}")
    t = schedule.get("time", "08:00")
    if f == "daily":
        days = schedule.get("weekdays") or list(range(7))
        if len(days) == 7:
            return f"todos los días a las {t}" if es else f"every day at {t}"
        if days == [0, 1, 2, 3, 4]:
            return f"de lunes a viernes a las {t}" if es else f"Monday to Friday at {t}"
        return (", ".join(names[d] for d in days) + (f" a las {t}" if es else f" at {t}"))
    if f == "weekly":
        return (f"cada {names[schedule.get('weekday', 0)]} a las {t}" if es else f"every {names[schedule.get('weekday', 0)]} at {t}")
    return (f"el día {schedule.get('day', 1)} de cada mes a las {t}" if es else f"on day {schedule.get('day', 1)} of every month at {t}")


def public(t: ScheduledTask, lang: str = "es") -> dict:
    return {"id": str(t.id), "name": t.name, "prompt": t.prompt, "schedule": t.schedule, "timezone": t.timezone,
            "schedule_text": describe(t.schedule, lang), "model_alias": t.model_alias, "options": t.options or {},
            "project_id": str(t.project_id) if t.project_id else None,
            "conversation_id": str(t.conversation_id) if t.conversation_id else None, "enabled": t.enabled,
            "next_run_at": t.next_run_at, "last_run_at": t.last_run_at, "last_status": t.last_status,
            "last_error": t.last_error, "run_count": t.run_count, "created_at": t.created_at}


def _model(db: Session, alias: str | None) -> RegisteredModel:
    for a in (alias, get_setting(db, "chat_default_model"), get_setting(db, "preferred_resident_model")):
        if a:
            m = db.scalar(select(RegisteredModel).where(RegisteredModel.alias == a))
            if m and m.status in (ModelStatus.installed, ModelStatus.ready, ModelStatus.loading, ModelStatus.busy):
                return m
    raise TaskError("No hay modelo de chat disponible para la tarea")


def dispatch_due() -> int:
    """Called by the worker: start every due task (at most MAX_RUNNING at once)."""
    now = datetime.now(timezone.utc)
    started = 0
    with SessionLocal() as db:
        due = db.scalars(select(ScheduledTask).where(ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at <= now)
                         .order_by(ScheduledTask.next_run_at).limit(10)).all()
        for t in due:
            with _lock:
                if t.id in _running or len(_running) >= MAX_RUNNING:
                    continue
                _running.add(t.id)
            t.next_run_at = next_run(t.schedule, t.timezone, now)  # advance first: never run twice for one slot
            db.commit()
            threading.Thread(target=run_task, args=(t.id,), daemon=True, name=f"task-{t.id}").start()
            started += 1
    return started


def run_task(task_id: uuid.UUID, manual: bool = False) -> dict:
    """Run one task now in its chat; returns {status, conversation_id, text}."""
    from . import agent
    from .notify import notify

    with _lock:
        _running.add(task_id)
    try:
        with SessionLocal() as db:
            t = db.get(ScheduledTask, task_id)
            if not t:
                return {"status": "gone"}
            user = db.get(User, t.owner_id)
            if not user or not user.is_active:
                t.enabled = False
                db.commit()
                return {"status": "disabled"}
            model = _model(db, t.model_alias)
            conv = db.get(Conversation, t.conversation_id) if t.conversation_id else None
            if conv is None:
                conv = Conversation(owner_id=user.id, title=f"⏰ {t.name}"[:255], model_alias=model.alias,
                                    project_id=t.project_id, settings={"web": bool((t.options or {}).get("web", True))})
                db.add(conv)
                db.flush()
                t.conversation_id = conv.id
            lang = (user.preferences or {}).get("language") or "es"
            stamp = datetime.now(_tz(t.timezone)).strftime("%d/%m/%Y %H:%M")
            content = (f"[Tarea programada «{t.name}» · {stamp}]\n{t.prompt}" if lang != "en"
                       else f"[Scheduled task «{t.name}» · {stamp}]\n{t.prompt}")
            db.add(Message(conversation_id=conv.id, role="user", content=content))
            t.last_status, t.last_error = "running", None
            db.commit()
            cid, uid, name, opts, mid_model = conv.id, user.id, t.name, dict(t.options or {}), model
            turn = agent.Turn(user_id=uid, conversation_id=cid, model_id=model.id, content=content,
                              params={"max_tokens": 4096, "temperature": 0.4}, use_tools=True, thinking=False,
                              lang=lang, web=bool(opts.get("web", True)), research=bool(opts.get("research")))
        try:
            turns.start(cid, uid, name)
        except RuntimeError:
            raise TaskError("Su chat ya está respondiendo; se reintentará en la próxima ejecución")
        status, text, err = "ok", "", None
        t0 = time.time()
        try:
            r = agent.run_turn(turn, lambda ev: turns.emit(cid, ev), turns.cancelled_checker(cid))
            text = r["text"]
            with SessionLocal() as db:
                msg = Message(conversation_id=cid, role="assistant", content=text, model_alias=mid_model.alias,
                              metrics={**r["metrics"], "scheduled_task": str(task_id)}, parts=r["parts"] or None)
                db.add(msg)
                m = r["metrics"]
                db.add(ApiUsage(user_id=uid, source="task", endpoint="tasks.run", model=mid_model.alias, status_code=200,
                                prompt_tokens=int(m.get("prompt_tokens") or 0), completion_tokens=int(m.get("completion_tokens") or 0),
                                latency_ms=(time.time() - t0) * 1000))
                c = db.get(Conversation, cid)
                if c:
                    c.updated_at = datetime.now(timezone.utc)
                db.commit()
                turns.emit(cid, {"type": "done", "metrics": r["metrics"], "message_id": msg.id})
        except Exception as e:  # noqa: BLE001
            status, err = "error", str(getattr(e, "message", e))[:1000]
            turns.emit(cid, {"type": "error", "message": err})
        finally:
            turns.finish(cid, uid, "done" if status == "ok" else "error")
        with SessionLocal() as db:
            t = db.get(ScheduledTask, task_id)
            if t:
                t.last_run_at, t.last_status, t.last_error = datetime.now(timezone.utc), status, err
                t.run_count += 1
                if manual and t.enabled and (t.next_run_at is None or t.next_run_at <= datetime.now(timezone.utc)):
                    t.next_run_at = next_run(t.schedule, t.timezone)
            title = (f"Tarea «{name}» lista" if status == "ok" else f"La tarea «{name}» falló") if lang != "en" else \
                    (f"Task «{name}» done" if status == "ok" else f"Task «{name}» failed")
            body = (text if status == "ok" else err or "")[:6000]
            notify(db, uid, "task", title, body, link=f"/playground?c={cid}", email=bool(opts.get("email")))
            db.commit()
        log.info("tasks.ran", task=str(task_id), status=status, seconds=round(time.time() - t0, 1))
        return {"status": status, "conversation_id": str(cid), "text": text[:4000], "error": err}
    except TaskError as e:
        with SessionLocal() as db:
            t = db.get(ScheduledTask, task_id)
            if t:
                t.last_status, t.last_error, t.last_run_at = "error", e.message, datetime.now(timezone.utc)
                db.commit()
        return {"status": "error", "error": e.message}
    finally:
        with _lock:
            _running.discard(task_id)
