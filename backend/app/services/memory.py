"""The assistant's memory of a person: it learns lasting facts from their chats and the person can take it all with them.

After an answer, a small model call reads the last exchange together with what is already remembered and returns a few
changes: add a new fact, refine an old one, forget an outdated one. Facts the person asked to remember by hand are never
rewritten or removed by the assistant. Passwords, keys and identity numbers are never stored. Everything is visible, editable
and deletable on the Memory page, and exportable (JSON or a Markdown "about me" to paste into another AI).

The decisions (what is worth learning, parsing, merging, ranking, export formats) are pure functions and unit tested;
`learn()` is the only part that touches the database and the model.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

CATEGORIES = ("profile", "preference", "work", "project", "context", "other")
CATEGORY_LABELS = {
    "profile": ("Sobre mí", "About me"), "preference": ("Cómo me gusta que respondas", "How I like answers"),
    "work": ("Mi trabajo", "My work"), "project": ("Proyectos", "Projects"),
    "context": ("Contexto", "Context"), "other": ("Otros", "Other"),
}
MAX_CONTENT = 300
MAX_AUTO = 200  # learned memories kept per person; the least useful are dropped beyond this
MAX_CHANGES = 3  # per exchange
PROMPT_ITEMS = 60  # how many memories go into the assistant's prompt
EDITABLE_SOURCES = ("assistant", "auto", "mcp")  # what the assistant may refine or forget; «user» and «import» are the person's own
MIN_GAP_S = 20.0  # at most one learning pass per chat in this time

# Never stored, however it is asked: credentials and identity or payment numbers.
SENSITIVE = re.compile(
    r"(?i)(contrase[nñ]a|password|passwd|passphrase|api[_ -]?key|secret|token\b|bearer\s|private key|clave (?:privada|de acceso|secreta)|"
    r"\biban\b|\bcvv\b|tarjeta de cr[eé]dito|credit card|\bsk-[A-Za-z0-9]{16,}|\bghp_[A-Za-z0-9]{16,}|\bhf_[A-Za-z0-9]{16,}|"
    r"\b\d{8}[A-Za-z]\b|\b[XYZ]\d{7}[A-Za-z]\b|\b(?:\d[ -]?){13,19}\b|[\w.+-]+:[^\s@]{4,}@[\w.-]+)")
TRIVIAL = re.compile(r"^\W*(hola|buenas|gracias|vale|ok|okay|perfecto|genial|s[ií]|no|adi[oó]s|hi|hello|thanks|thank you|bye)\b[\s\W]*$", re.I)

PROMPT = (
    "You maintain a short memory of lasting facts about a user, so an assistant can help them better in future chats. "
    "Read the last exchange and the current memory, then reply with ONLY a JSON object: "
    '{"add":[{"content":"...","category":"profile|preference|work|project|context","importance":1-5}],'
    '"update":[{"id":"...","content":"...","importance":1-5}],"forget":["id"]}. '
    "Rules: keep only durable facts the USER stated about themselves, their work, projects, tools, preferences or how they "
    "want to be answered. Never store the assistant's own content, one-off questions or tasks, or general knowledge. Write each "
    "fact as a short statement in the user's language (e.g. «Trabaja con ROS 2 en robots móviles»). If a fact is already in the "
    "memory do not add it; if it refines or contradicts an existing one, update it (or forget the outdated one). Never store "
    "passwords, keys, tokens, bank, identity or payment numbers, or exact home addresses. importance: 5 = essential to "
    "answer well, 3 = useful, 1 = a detail. At most 3 changes in total. When there is nothing lasting, reply "
    '{"add":[],"update":[],"forget":[]}.')


# ------------------------------------------------------------------ decisions (pure)


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def tokens(text: str) -> set[str]:
    return {w for w in norm(text).split() if len(w) > 2}


def similar(a: str, b: str) -> bool:
    """The same fact in other words: one contains the other, or most of their meaningful words coincide."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.7


def clean(content: str) -> str | None:
    """A storable memory, or None (empty, too vague or sensitive)."""
    c = re.sub(r"\s+", " ", str(content or "")).strip().strip("-•* ").strip()
    if len(c) < 8 or SENSITIVE.search(c):
        return None
    return c[:MAX_CONTENT]


def worth_learning(user_text: str, prefs: dict) -> bool:
    """Is this exchange worth a look? Memory off, small talk, very short messages and «recuerda que…» (already saved) are not."""
    if not prefs.get("memory_enabled", True) or not prefs.get("memory_auto", True):
        return False
    t = (user_text or "").strip()
    if len(t) < 25 or TRIVIAL.match(t):
        return False
    return not re.match(r"^\s*(?:por favor,?\s*)?(?:recuerda|recuérdalo|apunta|guarda en (?:tu )?memoria|remember)\b", t, re.I)


def rank_key(m) -> tuple:
    """Most important first; then confirmed more often; then most recently touched."""
    ts = m.updated_at or m.created_at
    return (-(m.importance or 3), -(m.mentions or 1), -(ts.timestamp() if ts else time.time()))  # (a new one has no date yet)


def ranked(mems: list, limit: int = PROMPT_ITEMS) -> list:
    return sorted(mems, key=rank_key)[:limit]


def render_prompt_block(mems: list, es: bool = True) -> str:
    """The memory as the assistant sees it: grouped, most important first."""
    out = []
    for cat in CATEGORIES:
        rows = [m for m in ranked(mems) if (m.category or "other") == cat]
        if rows:
            out.append(f"[{CATEGORY_LABELS[cat][0 if es else 1]}]\n" + "\n".join(f"- {m.content}" for m in rows))
    return "\n".join(out)


@dataclass
class Ops:
    add: list[dict] = field(default_factory=list)
    update: list[dict] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.add or self.update or self.forget)


def _importance(v, default: int = 3) -> int:
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return default


def parse_ops(text: str, known_ids: set[str]) -> Ops:
    """The model's reply as safe operations: valid JSON only, known ids only, at most MAX_CHANGES in total."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return Ops()
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return Ops()
    if not isinstance(data, dict):
        return Ops()
    ops, budget = Ops(), MAX_CHANGES
    for item in (data.get("forget") or []) if isinstance(data.get("forget"), list) else []:
        if budget and str(item) in known_ids:
            ops.forget.append(str(item))
            budget -= 1
    for item in data.get("update") or [] if isinstance(data.get("update"), list) else []:
        if budget and isinstance(item, dict) and str(item.get("id")) in known_ids:
            c = clean(item.get("content"))
            if c:
                ops.update.append({"id": str(item["id"]), "content": c, "importance": _importance(item.get("importance"))})
                budget -= 1
    for item in data.get("add") or [] if isinstance(data.get("add"), list) else []:
        if budget and isinstance(item, dict):
            c = clean(item.get("content"))
            if c:
                cat = item.get("category") if item.get("category") in CATEGORIES else "other"
                ops.add.append({"content": c, "category": cat, "importance": _importance(item.get("importance"))})
                budget -= 1
    return ops


def build_prompt(existing: list, user_text: str, assistant_text: str) -> list[dict]:
    known = "\n".join(f"{m.id} | {m.category} | {m.importance} | {m.content}" for m in ranked(existing, 80)) or "(empty)"
    body = (f"CURRENT MEMORY (id | category | importance | fact):\n{known}\n\n"
            f"LAST EXCHANGE\nUSER: {user_text[:1500]}\nASSISTANT (for context only): {assistant_text[:700]}")
    return [{"role": "system", "content": PROMPT}, {"role": "user", "content": body}]


def evict(mems: list, keep: int = MAX_AUTO) -> list:
    """Learned memories beyond the cap: the least important, least confirmed, oldest go first. The person's own never do."""
    auto = [m for m in mems if m.source in EDITABLE_SOURCES]
    return sorted(auto, key=rank_key, reverse=True)[: max(0, len(auto) - keep)]


# ------------------------------------------------------------------ export / import (pure)


def export_json(user, mems: list, now: datetime | None = None) -> dict:
    prefs = user.preferences or {}
    return {
        "format": "spark-ai-hub-memory", "version": 1, "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "profile": {"name": user.display_name or user.username, "about_me": prefs.get("about_me") or "",
                    "custom_instructions": prefs.get("custom_instructions") or ""},
        "memories": [{"content": m.content, "category": m.category or "other", "importance": m.importance or 3,
                      "mentions": m.mentions or 1, "source": m.source,
                      "created_at": m.created_at.isoformat() if m.created_at else None,
                      "updated_at": m.updated_at.isoformat() if m.updated_at else None} for m in sorted(mems, key=rank_key)],
    }


def export_markdown(user, mems: list, es: bool = True, now: datetime | None = None) -> str:
    """A document to paste into another AI as personal context or custom instructions."""
    prefs = user.preferences or {}
    name = user.display_name or user.username
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    out = [f"# {'Lo que sabe mi asistente sobre' if es else 'What my assistant knows about'} {name}",
           ("*Exportado de Spark AI Hub el %s. Pégalo como contexto o instrucciones personalizadas en otra IA.*" if es else
            "*Exported from Spark AI Hub on %s. Paste it as context or custom instructions into another AI.*") % day, ""]
    if prefs.get("about_me"):
        out += [f"## {'Sobre mí (lo escribí yo)' if es else 'About me (written by me)'}", str(prefs["about_me"]).strip(), ""]
    if prefs.get("custom_instructions"):
        out += [f"## {'Cómo quiero que respondas' if es else 'How I want answers'}", str(prefs["custom_instructions"]).strip(), ""]
    for cat in CATEGORIES:
        rows = sorted((m for m in mems if (m.category or "other") == cat), key=rank_key)
        if rows:
            out += [f"## {CATEGORY_LABELS[cat][0 if es else 1]}", *[f"- {m.content}" for m in rows], ""]
    return "\n".join(out).rstrip() + "\n"


def parse_import(text: str | None, data: dict | None) -> list[dict]:
    """Memories from our own JSON export, or from any text where each fact is a line («- fact» or «* fact»)."""
    items: list[dict] = []
    if isinstance(data, dict) and isinstance(data.get("memories"), list):
        for it in data["memories"][:500]:
            if isinstance(it, dict):
                items.append({"content": it.get("content"), "category": it.get("category"), "importance": it.get("importance")})
            elif isinstance(it, str):
                items.append({"content": it})
    elif text:
        for line in text.splitlines()[:2000]:
            m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
            if m:
                items.append({"content": m.group(1)})
    out = []
    for it in items:
        c = clean(it.get("content"))
        if c:
            out.append({"content": c, "category": it.get("category") if it.get("category") in CATEGORIES else "other",
                        "importance": _importance(it.get("importance"))})
    return out[:500]


# ------------------------------------------------------------------ database side


def apply(db, user_id, ops: Ops, existing: list, conversation_id=None) -> dict:
    """Apply the operations. Returns what changed. The caller commits."""
    from ..models import Memory

    by_id = {str(m.id): m for m in existing}
    done = {"added": 0, "updated": 0, "forgotten": 0, "confirmed": 0}
    for mid in ops.forget:
        m = by_id.get(mid)
        if m is not None and m.source in EDITABLE_SOURCES:
            db.delete(m)
            existing.remove(m)
            done["forgotten"] += 1
    for u in ops.update:
        m = by_id.get(u["id"])
        if m is not None and m.source in EDITABLE_SOURCES and m.content != u["content"]:
            m.content, m.importance = u["content"], u["importance"]
            m.mentions = (m.mentions or 1) + 1
            done["updated"] += 1
    for a in ops.add:
        dup = next((m for m in existing if similar(m.content, a["content"])), None)
        if dup is not None:  # already known: it was confirmed again
            dup.mentions = (dup.mentions or 1) + 1
            if dup.mentions >= 3 and (dup.importance or 3) < 4 and dup.source in EDITABLE_SOURCES:
                dup.importance = (dup.importance or 3) + 1
            done["confirmed"] += 1
            continue
        m = Memory(owner_id=user_id, content=a["content"], source="auto", category=a["category"], importance=a["importance"],
                   mentions=1, conversation_id=conversation_id)
        db.add(m)
        existing.append(m)
        done["added"] += 1
    for m in evict(existing):
        db.delete(m)
        existing.remove(m)
    return done


def list_for(db, user_id) -> list:
    from ..models import Memory

    return list(db.scalars(select(Memory).where(Memory.owner_id == user_id)))


_lock = threading.Lock()
_running: set = set()
_last: dict = {}


def schedule(user_id, conversation_id, model_id) -> None:
    """Learn from the exchange that just finished, in the background (never delays or breaks the chat)."""
    now = time.monotonic()
    with _lock:
        if user_id in _running or now - _last.get(conversation_id, -1e9) < MIN_GAP_S:
            return
        _running.add(user_id)
        _last[conversation_id] = now
        if len(_last) > 2000:
            for k in sorted(_last, key=_last.get)[:1000]:
                _last.pop(k, None)

    def run():
        try:
            learn(user_id, conversation_id, model_id)
        except Exception as e:  # noqa: BLE001 — learning is a nicety
            from ..logging_setup import log

            log.warning("memory.learn_failed: %s", e)
        finally:
            with _lock:
                _running.discard(user_id)

    threading.Thread(target=run, daemon=True).start()


def _complete(model, messages: list[dict]) -> str:
    from . import llm, scheduler

    body = {"messages": messages, "max_tokens": 400, "temperature": 0}
    if model.backend == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    inst = scheduler.ensure_loaded(model.id, wait_s=10)
    scheduler.begin_request(inst.id)
    try:
        r = llm.chat_once(model, body)
    finally:
        scheduler.end_request(inst.id)
    return (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def learn(user_id, conversation_id, model_id, complete=None) -> dict | None:
    """One learning pass over the latest exchange of a chat. Returns what changed, or None when there was nothing to do."""
    from ..db import SessionLocal
    from ..models import Message, RegisteredModel, RuntimeInstance, User

    with SessionLocal() as db:
        user = db.get(User, user_id)
        model = db.get(RegisteredModel, model_id)
        if user is None or model is None:
            return None
        recent = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(6)))
        u = next((m for m in recent if m.role == "user"), None)
        a = next((m for m in recent if m.role == "assistant" and (u is None or m.id > u.id)), None)
        if u is None or not worth_learning(u.content, user.preferences or {}):
            return None
        if complete is None and not db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == model_id)):
            return None  # the model is not resident: do not load it just to take notes
        existing = list_for(db, user_id)
        _ = model.runtime_config
        db.expunge(model)
        msgs = build_prompt(existing, u.content, a.content if a else "")
        known = {str(m.id) for m in existing}
    text = (complete or (lambda m: _complete(model, m)))(msgs)
    ops = parse_ops(text, known)
    if ops.empty():
        return {"added": 0, "updated": 0, "forgotten": 0, "confirmed": 0}
    with SessionLocal() as db:
        existing = list_for(db, user_id)
        done = apply(db, user_id, ops, existing, conversation_id)
        db.commit()
    return done
