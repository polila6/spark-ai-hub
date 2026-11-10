"""Web chat agent: personal context (instructions, memory, project), attachments (images, documents, data) and tools
(image generation/editing, knowledge search, document reading, data queries, memory, web search, skills and
sub-agents) executed server-side. Long conversations are compacted automatically to fit the context window.

`run_turn` is synchronous (runs in a thread) and reports progress through `emit(event_dict)`; the router streams the
events to the browser as SSE. Tool results are stored in `Message.parts` so the UI can replay them.

The main loop runs until the task is done, not for a fixed number of steps: the model keeps a plan (update_plan) and is
nudged to continue while plan items are open, while workspace changes are unverified, or when it stops after only
announcing work. Admin settings give a generous safety net (steps and wall-clock time) and the user can always stop.
When the turn's own messages approach the context window they are compacted (summary of the older steps), and
messages the user sends meanwhile are injected from the turn's inbox (services/turns.py) before the next step.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..engines import EngineError
from ..models import (AgentProfile, Artifact, CanvasDoc, Conversation, Job, JobStatus, Memory, Message, Project, RegisteredModel,
                      SchedClass, User, UserFile, Visibility)
from ..settings_store import branding, get_setting
from . import canvas as canvas_svc
from . import director
from . import memory as memory_svc
from . import dataq, kb, llm, loopguard, media_fetch, remote_db, research_tools, sandbox, scheduler, vision, web, web_tools, workflows
from . import skills as skills_svc
from . import files as files_svc
from . import turns as turns_svc
from .jobs import JobError, create_job, request_cancel

MAX_STEPS = 40  # fallback of the admin setting agent_max_steps_chat (safety net, not a goal)
SMALL_MODEL_B = 14  # below this size models get only the core tools: 20+ tool definitions confuse them
CORE_TOOLS = {"generate_image", "edit_image", "search_knowledge", "read_document", "describe_data", "query_data",
              "save_memory", "web_search", "fetch_url", "fetch_urls", "use_skill", "db_schema", "db_query"}
REMEMBER_RE = re.compile(r"^\s*(?:por favor,?\s*)?(?:recuerda|recuérdalo|apunta|guarda en (?:tu )?memoria|remember)\b[\s,:]*(?:que|that)?\s*(.+)",
                         re.I | re.S)
WORKSPACE_MAX_STEPS = 400  # fallback of agent_max_steps_workspace: long tasks run until done (or stopped)
MAX_MINUTES_CHAT, MAX_MINUTES_WORKSPACE = 30, 240  # fallbacks of agent_max_minutes_chat / _workspace
THINKING_MIN_TOKENS = 16384  # with thinking on, a step needs room for the reasoning AND the answer
COMPACT_AT = 0.7  # compact the turn's working messages when the prompt passes this share of the window
COMPACT_KEEP = 0.2  # share of the window kept verbatim (most recent steps) when compacting
MAX_NUDGES_CHAT, MAX_NUDGES_WORKSPACE = 6, 60  # "continue" reminders per turn (2 in a row in chat, NUDGES_ROW_WORKSPACE in a workspace)
NUDGES_ROW_WORKSPACE = 5
MAX_ROLLOVERS_WORKSPACE = 3  # a workspace turn that hits its time/step limit with the goal unmet goes on in a new segment (this many times)
AUDIT_MIN_STEPS = 6  # a workspace turn with at least this many steps is audited against the request before it may finish
MAX_AUDITS = 3
GOAL_DONE = "objetivos cumplidos"  # the sentinel of a passed audit (also matched in English: «goals met»)
SUB_MAX_STEPS = 8
IMAGE_QUEUE_WAIT_S = 300  # a generated image that has not even started after this long stops holding the turn
IMAGE_FAILS_MAX = 2  # after this many failed generations in a turn the generator is switched off for it
SUB_WS_MAX_STEPS = 60  # a sub-agent that writes and runs code in the workspace needs more steps
WS_WRITE_TOOLS = {"ws_write", "ws_append", "ws_edit", "download_file"}  # download_file writes into the workspace too
WS_TOOLS = WS_WRITE_TOOLS | {"ws_list", "ws_read", "ws_run"}
IMAGE_MAX_SIDE = 1280
TOOL_RESULT_CHARS = 12000
CHARS_PER_TOKEN = 3.2  # first estimate; recalibrated with the engine's real prompt_tokens during the turn
IMAGE_TOKENS = 1100
UNC_PATH_RE = re.compile(r"\\\\[^\n`|)]+")
DOCUMENT_QUERY_STOP = {
    "acuerdo", "acuerdos", "archivo", "archivos", "busca", "buscar", "cita", "citar", "dame", "documento",
    "documentos", "encuentra", "enumera", "exacta", "exactas", "fuente", "fuentes", "información", "necesito",
    "resumen", "resume", "resúmelo", "resúmelos", "ruta", "rutas", "tenemos", "verificada", "verificadas",
    "what", "which", "search", "find", "summarize", "summary", "source", "sources", "path", "paths", "verified",
    "algún", "alguna", "cual", "cuál", "como", "cómo", "donde", "dónde", "para", "sobre", "todos", "todas",
    "esta", "este", "estos", "estas", "entre", "desde", "hasta", "with", "from", "about", "have", "there",
}


def _document_search_query(value: str) -> str:
    """Extract a compact retrieval query from an instruction-like user question."""
    words = re.findall(r"[\w.-]+", value, re.UNICODE)
    distinctive = [w for i, w in enumerate(words) if i > 0 and len(w) > 2 and w[0].isupper()
                   and w.lower() not in DOCUMENT_QUERY_STOP]
    if distinctive:
        return " ".join(distinctive[:4])
    useful = [w for w in words if len(w) > 2 and w.lower() not in DOCUMENT_QUERY_STOP]
    return " ".join(useful[:6]) or value[:1000]


def _remove_unverified_unc_paths(text: str, allowed_paths: set[str]) -> str:
    """Keep only UNC paths that came verbatim from document metadata."""
    if not allowed_paths:
        return text

    def replace(match: re.Match) -> str:
        raw = match.group(0)
        candidate = raw.rstrip(" .,:;")
        suffix = raw[len(candidate):]
        return raw if candidate in allowed_paths else "[ruta no verificada omitida]" + suffix

    return UNC_PATH_RE.sub(replace, text)


class AgentError(Exception):
    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.message, self.remediation = message, remediation


@dataclass
class Turn:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    model_id: uuid.UUID
    content: str
    attachments: list[uuid.UUID] = field(default_factory=list)
    system_prompt: str | None = None
    params: dict = field(default_factory=dict)
    use_tools: bool = True
    thinking: bool | None = None
    mode: str = "chat"  # chat | image
    lang: str = "es"
    context_limit: int | None = None  # tokens; None = the model's full window
    auto_compact: bool = True
    web: bool = True
    research: bool = False  # "Deep research" switch: the assistant calls deep_research
    effort: int = 2  # 0 instant … 4 maximum: how far the director lets thinking go (see services/director.py)
    director: bool = False  # a decision before answering: which of the allowed capabilities this message needs
    allow: dict = field(default_factory=dict)  # capability → auto | on | off (think, web, research, skills, agents, code)
    workspace_id: uuid.UUID | None = None  # workspace mode: files and commands in the user's persistent workspace
    preferred_agent: str | None = None  # agent selected for the main task; other agents may still assist
    user_message_id: int | None = None  # the message being answered: history = messages before it (queued ones after)
    history_upto: int | None = None  # follow-up turns: history = messages with id < this (default user_message_id)...
    history_exclude: list[int] = field(default_factory=list)  # ...except these (the queued messages being answered)

    def history_bounds(self) -> tuple[int | None, set[int]]:
        return (self.history_upto or self.user_message_id), set(self.history_exclude)


# ------------------------------------------------------------------ helpers


def _now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _data_uri(path, max_side: int = IMAGE_MAX_SIDE) -> str:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _png_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def prior_messages(db, conv_id: uuid.UUID, before_id: int | None = None, exclude: set[int] | None = None) -> list[Message]:
    """Conversation messages before the turn being answered (the router stores the new user message first).

    With `before_id` (the id of the message being answered) messages queued after it while the turn runs are left
    out: they reach the model through the inbox, not as history. `exclude`: ids that are not history either (the
    queued messages a follow-up turn answers)."""
    q = select(Message).where(Message.conversation_id == conv_id)
    if before_id is not None:
        return [m for m in db.scalars(q.where(Message.id < before_id).order_by(Message.id)) if m.id not in (exclude or ())]
    msgs = list(db.scalars(q.order_by(Message.id)))
    if msgs and msgs[-1].role == "user":
        msgs.pop()
    return msgs


def image_refs(db, conv_id: uuid.UUID, extra: list[UserFile] | None = None, before_id: int | None = None,
               exclude: set[int] | None = None) -> list[dict]:
    """Stable labels img1..imgN for every image in the conversation (uploads and generated), oldest first."""
    refs: list[dict] = []
    for m in prior_messages(db, conv_id, before_id, exclude):
        for a in m.attachments or []:
            if a.get("kind") == "image":
                refs.append({"source": "file", "id": a["file_id"], "desc": f"subida por el usuario: {a.get('name')}"})
        for part in m.parts or []:
            if part.get("image"):
                refs.append({"source": "artifact", "id": part["image"]["id"],
                             "desc": f"generada: {part['image'].get('prompt', '')[:120]}"})
    for f in extra or []:
        if f.kind == "image":
            refs.append({"source": "file", "id": str(f.id), "desc": f"subida por el usuario ahora: {f.filename}"})
    for i, r in enumerate(refs, 1):
        r["ref"] = f"img{i}"
    return refs


def _image_path(db, ref: dict, owner_id):
    from ..config import get_settings

    if ref["source"] == "file":
        f = db.get(UserFile, uuid.UUID(ref["id"]))
        return (get_settings().data_root / f.path) if f and f.owner_id == owner_id else None
    a = db.get(Artifact, uuid.UUID(ref["id"]))
    return (get_settings().data_root / a.path) if a and a.owner_id == owner_id else None


def _available_workflow(wf_ids: list[str]) -> str | None:
    files = workflows.installed_files()
    for wid in wf_ids:
        wf = workflows.WORKFLOWS.get(wid)
        if wf and not workflows.missing_models(wf, files):
            return wid
    return None


def pick_workflow(db, kind: str) -> str | None:
    pref = get_setting(db, "chat_image_model") or "auto"
    if kind == "edit":
        order = ["qwen-image-edit", "sd15-img2img"] if pref != "sd15" else ["sd15-img2img", "qwen-image-edit"]
    else:
        order = ["qwen-image-txt2img", "sd15-txt2img"] if pref != "sd15" else ["sd15-txt2img", "qwen-image-txt2img"]
    return _available_workflow(order)


ASPECTS = {"qwen_image": {"square": (1328, 1328), "landscape": (1472, 1104), "portrait": (1104, 1472),
                          "wide": (1664, 928), "tall": (928, 1664)},
           "sd15": {"square": (512, 512), "landscape": (768, 576), "portrait": (576, 768), "wide": (896, 512),
                    "tall": (512, 896)}}


# ------------------------------------------------------------------ context window

def est_tokens(content, ratio: float = CHARS_PER_TOKEN) -> int:
    """Rough token count of a message content / message list (text chars / ratio + a flat cost per image)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return int(len(content) / ratio) + 1
    if isinstance(content, dict):
        n = est_tokens(content.get("content"), ratio) + 4
        for tc in content.get("tool_calls") or []:
            n += est_tokens(json.dumps(tc.get("function", {}), ensure_ascii=False), ratio)
        return n
    n = 0
    for p in content:
        if isinstance(p, dict) and p.get("type") == "image_url":
            n += IMAGE_TOKENS
        elif isinstance(p, dict) and "role" in p:
            n += est_tokens(p, ratio)
        elif isinstance(p, dict):
            n += est_tokens(p.get("text", ""), ratio)
    return n


def effective_context(model: RegisteredModel, limit: int | None) -> int:
    ctx = llm.context_tokens(model)
    return max(2048, min(ctx, int(limit))) if limit else ctx


def _message_text(m: Message) -> str:
    """Plain-text rendering of a stored message for summaries (attachments and tool results included)."""
    lines = [m.content or ""]
    for a in m.attachments or []:
        lines.append(f"[adjunto: {a.get('name')} ({a.get('kind')}) file_id={a.get('file_id')}]")
    for part in m.parts or []:
        if part.get("image"):
            lines.append(f"[imagen {part['image'].get('ref', '')}: {part['image'].get('prompt', '')[:200]}]")
        elif part.get("summary"):
            lines.append(f"[{part.get('name')}: {str(part['summary'])[:400]}]")
    return "\n".join(x for x in lines if x)


SUMMARY_PROMPT = (
    "Resume la conversación siguiente entre un usuario y un asistente para que el asistente pueda continuarla sin "
    "verla. Conserva: objetivos y peticiones del usuario, datos, cifras, nombres, decisiones tomadas, conclusiones, "
    "file_id de archivos y referencias de imágenes (img1, img2...), URLs consultadas, código o fórmulas importantes "
    "(resumidos) y tareas pendientes. Escribe en el idioma de la conversación, en viñetas compactas, sin inventar "
    "nada. Si recibes un resumen previo, intégralo. Máximo {words} palabras.")


TASK_SUMMARY_PROMPT = (
    "Resume el trabajo que un asistente lleva hecho en la tarea actual (sus mensajes, las llamadas a herramientas y "
    "sus resultados) para que pueda continuarla sin verlo. Conserva: lo que pidió el usuario, lo hecho paso a paso "
    "(breve), archivos creados o modificados con su ruta exacta y su estado, comandos ejecutados y resultados clave "
    "(cifras, salidas, datos encontrados, URLs consultadas, file_id), decisiones tomadas, errores que siguen abiertos "
    "y lo que falta por hacer. Escribe en el idioma de la tarea, en viñetas compactas, sin inventar nada. Si recibes "
    "un resumen previo, intégralo. Máximo {words} palabras.")


def _summarize(model: RegisteredModel, previous: str | None, transcript: str, words: int,
               prompt: str = SUMMARY_PROMPT, label: str = "Conversación") -> str:
    body = {"messages": [{"role": "system", "content": prompt.format(words=words)},
                         {"role": "user", "content": (f"Resumen previo:\n{previous}\n\n" if previous else "")
                          + f"{label}:\n{transcript}"}],
            "temperature": 0.2, "max_tokens": min(4000, int(words * 2.2))}
    if model.backend == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    out = llm.chat_once(model, body)
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def compact_conversation(conv_id: uuid.UUID, model_id: uuid.UUID, ctx: int, budget: int | None = None,
                         force: bool = False, exclude_last_user: bool = True, before_id: int | None = None,
                         exclude: set[int] | None = None) -> dict | None:
    """Summarise the oldest messages so the rest fits. `budget`: tokens available for the history.

    Without `force`, nothing happens while the history uses less than half of the budget. The most recent messages
    (about a quarter of the budget, at least the last exchange) stay verbatim. Returns stats or None.
    """
    budget = budget or int(ctx * 0.6)
    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        msgs = prior_messages(db, conv_id, before_id, exclude) if exclude_last_user else list(db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)))
        msgs = [m for m in msgs if conv.summary_upto is None or m.id > conv.summary_upto]
        texts = [(m.id, m.role, _message_text(m)) for m in msgs]
        previous = conv.summary
        model = db.get(RegisteredModel, model_id)
        _ = model.runtime_config
        db.expunge(model)
    sizes = [est_tokens(t) for _, _, t in texts]
    total = sum(sizes) + est_tokens(previous or "")
    if not texts or (not force and total <= budget * 0.5):
        return None
    keep_budget = int(budget * 0.25)
    keep, acc = 0, 0
    for size in reversed(sizes):
        if keep >= 2 and acc + size > keep_budget:
            break
        acc += size
        keep += 1
    keep = min(keep, len(texts) - 1) if force else keep
    old = texts[: len(texts) - keep]
    if not old:
        return None
    # Summarise in chunks that fit comfortably in the window (each chunk folds into the running summary).
    chunk_tokens = max(1500, int(ctx * 0.45))
    words = max(250, min(1200, int(budget * 0.08)))
    inst = scheduler.ensure_loaded(model_id, wait_s=1800)
    scheduler.begin_request(inst.id)
    try:
        summary = previous
        buf: list[str] = []
        used = 0
        for _, role, text in old:
            piece = f"{'USUARIO' if role == 'user' else 'ASISTENTE'}: {text[:20000]}"
            t = est_tokens(piece)
            if buf and used + t > chunk_tokens:
                summary = _summarize(model, summary, "\n\n".join(buf), words)
                buf, used = [], 0
            buf.append(piece[: int(chunk_tokens * CHARS_PER_TOKEN)])
            used += min(t, chunk_tokens)
        if buf:
            summary = _summarize(model, summary, "\n\n".join(buf), words)
    finally:
        scheduler.end_request(inst.id)
    if not summary:
        return None
    after = est_tokens(summary) + sum(sizes[len(old):])
    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        meta = conv.summary_meta or {}
        conv.summary = summary
        conv.summary_upto = old[-1][0]
        conv.summary_meta = {"messages": int(meta.get("messages") or 0) + len(old), "tokens_before": total,
                             "tokens_after": after, "at": datetime.now(timezone.utc).isoformat(),
                             "model": model.alias, "compactions": int(meta.get("compactions") or 0) + 1}
        db.commit()
        return {**conv.summary_meta, "summarized_now": len(old), "kept": keep}


# ------------------------------------------------------------------ run-until-done loop (pure helpers)

PLAN_STATUSES = ("pending", "in_progress", "completed")
_PLAN_ALIASES = {"todo": "pending", "to_do": "pending", "not_started": "pending", "open": "pending", "waiting": "pending",
                 "in-progress": "in_progress", "inprogress": "in_progress", "doing": "in_progress", "active": "in_progress",
                 "working": "in_progress", "started": "in_progress", "en_curso": "in_progress",
                 "done": "completed", "complete": "completed", "finished": "completed", "ok": "completed",
                 "cancelled": "completed", "canceled": "completed", "skipped": "completed", "hecho": "completed"}
PLAN_MAX_ITEMS = 40


def normalize_plan(items) -> list[dict]:
    """The model's update_plan items as [{content, status}] (status pending|in_progress|completed); tolerant of a
    JSON string, plain strings and status synonyms. Empty items are dropped; at most PLAN_MAX_ITEMS."""
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            items = [x for x in items.splitlines() if x.strip()]
    if isinstance(items, dict):
        items = items.get("items") or []
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            content, status = it, "pending"
        elif isinstance(it, dict):
            content = it.get("content") or it.get("step") or it.get("title") or it.get("task") or ""
            status = str(it.get("status") or "pending").strip().lower().replace(" ", "_")
        else:
            continue
        content = re.sub(r"\s+", " ", str(content)).strip()[:300]
        if not content:
            continue
        out.append({"content": content, "status": status if status in PLAN_STATUSES else _PLAN_ALIASES.get(status, "pending")})
        if len(out) >= PLAN_MAX_ITEMS:
            break
    return out


def plan_open(plan: list[dict]) -> list[dict]:
    return [i for i in plan or [] if i.get("status") != "completed"]


ANNOUNCE_RE = re.compile(
    r"(?:^|[\s.;:!¡,(])(?:voy a|vamos a|procedo a|paso a|empiezo (?:a|por|con)|lanzo|ahora (?:voy|paso|lanzo|compilo|"
    r"ejecuto|reviso|busco|creo|escribo|corrijo|pruebo|abro|leo)|a continuaci[oó]n (?:voy|paso|lanzo|compilo|ejecuto|"
    r"reviso|busco|creo|escribo|corrijo|pruebo|abro|leo)|let me|i(?:'ll| will) (?:now )?(?:run|compile|check|fix|search|"
    r"write|create|open|test|start|continue|do|read|look)|next,? i(?:'ll| will))\b", re.I)


def announces_work(text: str) -> bool:
    """The step ended with the model saying what it is about to do ("Voy a compilar.") instead of doing it."""
    tail = (text or "").strip()[-600:]
    if not tail or tail.rstrip("*_ )").endswith("?"):
        return False  # a question to the user is a legitimate end of turn
    last = re.split(r"\n\s*\n", tail)[-1]
    return bool(ANNOUNCE_RE.search(last))


HANDOFF_RE = re.compile(
    r"(?:escribe|escr[ií]beme|di|dime|d[ií]me|env[ií]a|responde|resp[oó]ndeme|manda|pon|teclea|write|type|say|reply)\s*(?:con\s*)?[«\"'“*_`]*\s*"
    r"(?:contin[uú]a(?:r)?|sigue|seguir|adelante|continue|go on|go ahead|proceed)\b"
    r"|(?:cuando\s+(?:quieras|me\s+(?:lo\s+)?(?:pidas|digas))|si\s+(?:quieres|lo\s+deseas),?)\s+(?:sigo|contin[uú]o|retomo|lo\s+(?:sigo|retomo))"
    r"|retomo\s+(?:exactamente\s+)?(?:por|desde|en)\s+el\s+(?:paso|punto)"
    r"|(?:i(?:'ll| will)|shall i|want me to)\s+(?:continue|go on|carry on)\b", re.I)
PERMISSION_Q_RE = re.compile(
    r"(?:¿\s*(?:quieres|deseas|prefieres)\s+que\s+(?:siga|contin[uú]e|continúe|prosiga|lo\s+haga|empiece|proceda|retome)|"
    r"¿\s*(?:sigo|contin[uú]o|procedo|empiezo|retomo|paso\s+a)\b|¿\s*te\s+parece\s+bien\s+que|"
    r"¿\s*(?:contin[uú]amos|seguimos)\b|(?:shall|should)\s+i\s+(?:continue|proceed|go on)|do\s+you\s+want\s+me\s+to\s+(?:continue|proceed|go on))",
    re.I)


def hands_off(text: str) -> bool:
    """The step ends by asking the user to say «continúa» / by asking permission to go on (the user does not want that:
    in a workspace the model goes on by itself and only stops for a decision only the user can take)."""
    tail = (text or "").strip()[-700:]
    return bool(tail) and bool(HANDOFF_RE.search(tail) or PERMISSION_Q_RE.search(tail))


def continue_reason(text: str, plan: list[dict], unverified: bool, nudges_in_row: int, nudges_total: int,
                    max_total: int, row_limit: int = 2) -> str | None:
    """Why the loop goes on although the model ended its step without tool calls (None = the turn is done).

    plan: items still pending/in progress; verify: workspace files written but never run/checked since; empty: no
    answer text at all (e.g. only reasoning); announced: the text promises work that was not done."""
    if nudges_in_row >= row_limit or nudges_total >= max_total:
        return None
    if hands_off(text):
        return "handoff"
    if plan_open(plan):
        return "plan"
    if unverified:
        return "verify"
    if not (text or "").strip():
        return "empty"
    if announces_work(text):
        return "announced"
    return None


def nudge_message(reason: str, plan: list[dict], lang: str = "es") -> str:
    es = lang != "en"
    if reason == "plan":
        pend = "\n".join(f"- [{i['status']}] {i['content']}" for i in plan_open(plan)[:20])
        return (("Continúa con el plan. Pasos sin completar:\n" + pend + "\nHazlos ahora con las herramientas, sin "
                 "anunciarlo antes. Si de verdad has terminado (o un paso no se puede hacer), márcalo todo como completado "
                 "con update_plan y da el resumen final.") if es else
                ("Continue with the plan. Open steps:\n" + pend + "\nDo them now with the tools, without announcing it "
                 "first. If you really are done (or a step is impossible), mark everything completed with update_plan "
                 "and give the final summary."))
    if reason == "handoff":
        pend = "\n".join(f"- [{i['status']}] {i['content']}" for i in plan_open(plan)[:20])
        return (("NO pidas al usuario que escriba «continúa» ni le preguntes si sigues: la tarea no ha terminado y tú la "
                 "continúas solo, ahora." + (("\nPendiente:\n" + pend) if pend else "") + "\nSolo puedes detenerte si te falta una "
                 "decisión o un dato que únicamente el usuario puede dar: entonces haz UNA pregunta concreta y nada más. En "
                 "otro caso llama ya a la herramienta del siguiente paso.") if es else
                ("Do NOT ask the user to write «continue» or whether to go on: the task is not finished and you carry on "
                 "by yourself, now." + (("\nPending:\n" + pend) if pend else "") + "\nStop only if you lack a decision or "
                 "data that only the user can give: then ask ONE concrete question and nothing else. Otherwise call the "
                 "tool for the next step now."))
    if reason == "audit":
        pend = "\n".join(f"- [{i['status']}] {i['content']}" for i in plan_open(plan)[:20])
        return (("AUDITORÍA DE OBJETIVOS antes de terminar. Relee la petición ORIGINAL del usuario y tu plan. Para CADA requisito "
                 "explícito (entregables y formatos, cantidad de capítulos/figuras/tablas, calidad visual sin solapes, imágenes "
                 "reales y no dibujadas, referencias reales, documento compilado y revisado, etc.) indica «cumplido» con su "
                 "prueba (ruta del archivo, salida del compilador o del comprobador, captura mirada) o «PENDIENTE»."
                 + (("\nPasos aún abiertos en tu plan:\n" + pend) if pend else "") +
                 "\nSi hay algo pendiente o sin verificar NO escribas el resumen final: trabájalo ahora con las herramientas. "
                 "Si TODO está cumplido y verificado, escribe el resumen final con el título «✅ Objetivos cumplidos», una "
                 "tabla requisito → evidencia y dónde están los archivos. No pidas permiso ni mandes al usuario escribir "
                 "«continúa»; solo puedes parar para hacer UNA pregunta que únicamente el usuario pueda responder.") if es else
                ("GOAL AUDIT before finishing. Re-read the user's ORIGINAL request and your plan. For EACH explicit requirement "
                 "(deliverables and formats, number of chapters/figures/tables, visual quality without overlaps, real pictures "
                 "not hand-drawn ones, real references, document compiled and reviewed, etc.) say «met» with its proof (file "
                 "path, compiler or checker output, screenshot looked at) or «PENDING»."
                 + (("\nOpen plan steps:\n" + pend) if pend else "") +
                 "\nIf anything is pending or unverified do NOT write the final summary: work on it now with the tools. If "
                 "EVERYTHING is met and verified, write the final summary titled «✅ Goals met», a requirement → evidence table "
                 "and where the files are. Do not ask permission or tell the user to write «continue»; stop only to ask ONE "
                 "question that only the user can answer."))
    if reason == "audit_fail":
        return ("Tu respuesta no confirma que todo esté cumplido (falta el título «✅ Objetivos cumplidos»). Si queda algo "
                "pendiente o sin verificar, hazlo ahora con las herramientas; si de verdad está todo cumplido y verificado, "
                "repite el resumen final con ese título." if es else
                "Your answer does not confirm everything is met (the «✅ Goals met» title is missing). If anything is pending or "
                "unverified do it now with the tools; if everything really is met and verified, repeat the final summary with "
                "that title.")
    if reason == "verify":
        return ("Antes de terminar, comprueba que lo que has escrito funciona: ejecútalo, compílalo o pruébalo con ws_run, "
                "revisa la salida y corrige los errores. Después da el resumen final." if es else
                "Before finishing, check that what you wrote works: run, compile or test it with ws_run, read the output "
                "and fix any error. Then give the final summary.")
    if reason == "empty":
        return ("No has escrito la respuesta para el usuario. Si queda trabajo, continúa con las herramientas; si no, "
                "escribe ya la respuesta final completa." if es else
                "You have not written the answer for the user. If work remains, continue with the tools; otherwise "
                "write the complete final answer now.")
    return ("Has dicho lo que ibas a hacer, pero no lo has hecho. Hazlo ahora llamando a las herramientas. Si ya está "
            "todo hecho, da el resumen final." if es else
            "You said what you were going to do but did not do it. Do it now by calling the tools. If everything is "
            "already done, give the final summary.")


def is_context_error(message: str) -> bool:
    return bool(re.search(r"context length|maximum context|too long|max_tokens|exceed|prompt is too", message or "", re.I))


def step_groups(messages: list[dict], start: int) -> list[list[int]]:
    """Indices of messages[start:] grouped into steps: an assistant message with the tool results that follow it
    stays together (splitting them breaks the tool-call protocol); every user message is a group of its own."""
    groups: list[list[int]] = []
    for i in range(start, len(messages)):
        role = messages[i].get("role")
        if role == "tool" and groups and messages[groups[-1][0]].get("role") == "assistant":
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def compaction_split(messages: list[dict], start: int, keep_tokens: int, ratio: float = CHARS_PER_TOKEN,
                     protected: set[int] | None = None, min_keep: int = 2) -> dict | None:
    """Which of the turn's working messages (messages[start:]) to summarise when the context fills up.

    The most recent steps stay verbatim (whole groups, at least `min_keep`, about `keep_tokens`); everything older is
    summarised, except `protected` indices (the user's injected messages), which stay as they are. Returns
    {"old": [...], "protected": [...], "tail": first kept index} or None when there is nothing worth summarising."""
    protected = protected or set()
    groups = step_groups(messages, start)
    if len(groups) <= min_keep:
        return None
    kept, acc = 0, 0
    for g in reversed(groups):
        size = sum(est_tokens(messages[i], ratio) for i in g)
        if kept >= min_keep and acc + size > keep_tokens:
            break
        acc += size
        kept += 1
    old_groups = groups[: len(groups) - kept]
    old = [i for g in old_groups for i in g if i not in protected]
    if not any(messages[i].get("role") in ("assistant", "tool") for i in old):
        return None
    return {"old": old, "protected": [i for g in old_groups for i in g if i in protected],
            "tail": groups[len(groups) - kept][0] if kept else len(messages)}


def _content_text(c) -> str:
    if isinstance(c, list):
        return "\n".join(p.get("text", "") if p.get("type") == "text" else "[imagen]" for p in c if isinstance(p, dict))
    return str(c or "")


def turn_transcript(msgs: list[dict], tool_chars: int = 2500) -> list[str]:
    """Plain-text pieces of the turn's messages for the summariser (tool results truncated)."""
    names: dict[str, str] = {}
    out: list[str] = []
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            lines = [f"ASISTENTE: {_content_text(m.get('content'))}".rstrip()]
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                names[tc.get("id") or ""] = fn.get("name") or "?"
                lines.append(f"→ {fn.get('name')}({str(fn.get('arguments') or '')[:800]})")
            out.append("\n".join(lines))
        elif role == "tool":
            text = _content_text(m.get("content"))
            out.append(f"RESULTADO de {names.get(m.get('tool_call_id') or '', 'herramienta')}: "
                       + (text[:tool_chars] + (" […]" if len(text) > tool_chars else "")))
        else:
            out.append(f"USUARIO/SISTEMA: {_content_text(m.get('content'))[:3000]}")
    return out


def mechanical_summary(msgs: list[dict]) -> str:
    """Fallback when the model cannot summarise: one line per tool call with the start of its result."""
    lines = []
    for piece in turn_transcript(msgs, tool_chars=200):
        lines.append("- " + re.sub(r"\s+", " ", piece)[:400])
    return "\n".join(lines[-120:])


# ------------------------------------------------------------------ tools

TOOLS = [
    {"type": "function", "function": {
        "name": "generate_image",
        "description": "Create a new image from a text description and show it to the user. Use it whenever the user "
                       "asks to draw, create, generate, design or illustrate something visual.",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Detailed visual description in English (subject, style, "
                                                        "composition, lighting). Include any text that must appear."},
            "aspect": {"type": "string", "enum": ["square", "landscape", "portrait", "wide", "tall"]},
            "negative_prompt": {"type": "string", "description": "Things to avoid (optional)"},
            "seed": {"type": "integer"}},
            "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "edit_image",
        "description": "Edit an existing image of this conversation following an instruction (change style, colours, "
                       "background, add/remove objects, change text). Use the image reference (img1, img2, ...).",
        "parameters": {"type": "object", "properties": {
            "image_ref": {"type": "string", "description": "Reference of the image to edit, e.g. img2"},
            "instruction": {"type": "string", "description": "What to change, in English, precise and complete"},
            "seed": {"type": "integer"}},
            "required": ["image_ref", "instruction"]}}},
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "Search the user's documents (personal knowledge base and the current project). Use it for "
                       "questions about their papers, notes, reports or any uploaded document. Cite every source with "
                       "its filename, page when available, and the COMPLETE source_path returned by the tool; never "
                       "shorten a source_path to its root folder.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query (keywords or a question)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "Read passages of a long attached document: the most relevant parts for a query, or given pages.",
        "parameters": {"type": "object", "properties": {
            "file_id": {"type": "string"},
            "query": {"type": "string", "description": "What to look for (optional)"},
            "pages": {"type": "array", "items": {"type": "integer"}, "description": "Page numbers to read (optional)"}},
            "required": ["file_id"]}}},
    {"type": "function", "function": {
        "name": "describe_data",
        "description": "Show tables, columns, types and sample rows of a data file (CSV, Excel, Parquet, JSON, SQLite).",
        "parameters": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}}},
    {"type": "function", "function": {
        "name": "query_data",
        "description": "Run one SQL query (DuckDB dialect; SQLite dialect for .sqlite/.db files) on a data file and get "
                       "up to 200 rows. Call describe_data first to know table and column names.",
        "parameters": {"type": "object", "properties": {
            "file_id": {"type": "string"},
            "sql": {"type": "string", "description": "A single SELECT/WITH query"}},
            "required": ["file_id", "sql"]}}},
    {"type": "function", "function": {
        "name": "save_memory",
        "description": "Remember a durable fact or preference about the user for future chats (only when the user asks "
                       "you to remember something, or states a lasting preference). Keep it short.",
        "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}},
]

WEB_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the internet. Use it for current events, prices, recent releases, facts you are not sure "
                       "about, documentation, or when the user asks to look something up. Then open the best results "
                       "with fetch_url before answering, and cite the URLs.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query (keywords; no personal or confidential data)"},
            "category": {"type": "string", "enum": ["web", "science", "news", "code"],
                         "description": "science = papers (arXiv, Scholar, PubMed...), code = GitHub/StackOverflow"},
            "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]},
            "site": {"type": "string", "description": "Only this site or domain, e.g. ine.es (optional)"},
            "filetype": {"type": "string", "description": "Only this file type: pdf, xlsx, csv, docx… (optional)"},
            "page": {"type": "integer", "description": "Result page, 2 for the next results (default 1)"},
            "limit": {"type": "integer", "description": "How many results, up to 20 (default 8)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Open a public web page or PDF and read its text (the most relevant passages when `query` is given).",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "query": {"type": "string", "description": "What to look for in the page (optional)"},
            "as_of": {"type": "string", "description": "Read the Internet Archive copy closest to this date (YYYY-MM-DD or a year), "
                                                       "e.g. to see a page as it was months ago (optional)"},
            "save_to": {"type": "string", "description": "Also save the FULL text as a file (workspace path, or the user's files in a "
                                                         "chat) so scripts can process it (optional)"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "fetch_urls",
        "description": "Read up to 10 web pages or PDFs AT ONCE (in parallel) and get an excerpt of each: much faster than calling "
                       "fetch_url one by one. Use it after web_search to read the best results together.",
        "parameters": {"type": "object", "properties": {
            "urls": {"type": "array", "items": {"type": "string"}, "description": "Up to 10 URLs"},
            "query": {"type": "string", "description": "What to look for in each page (picks the most relevant passages)"},
            "save_dir": {"type": "string", "description": "Workspace folder where to save each page's FULL text as .md (optional)"}},
            "required": ["urls"]}}},
    {"type": "function", "function": {
        "name": "http_request",
        "description": "Call a public web API or download a data endpoint (GET or POST): JSON, CSV, XML… Public addresses only. "
                       "The answer is shown (JSON pretty-printed); with save_to the FULL raw response is saved as a file for scripts "
                       "(the sandbox has no network, so this is how API data gets there). Do not send the user's private data to "
                       "third parties.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST"]},
            "params": {"type": "object", "description": "Query-string parameters"},
            "headers": {"type": "object", "description": "Extra headers (e.g. Accept, Authorization with an API key the user gave you)"},
            "json": {"type": "object", "description": "JSON body for POST"},
            "form": {"type": "object", "description": "Form body for POST"},
            "save_to": {"type": "string", "description": "Save the full raw response as this file (workspace path or the user's files)"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "crawl_site",
        "description": "Read SEVERAL pages of one website by following its links (or its sitemap), politely (robots.txt, pauses) and "
                       "return what each says. For documentation, reports, news archives, catalogues. Limited to 40 pages and 2 "
                       "minutes per call; use `pattern` to stay in a section.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Where to start"},
            "max_pages": {"type": "integer", "description": "Default 15, up to 40"},
            "depth": {"type": "integer", "description": "Link levels from the start page (default 2, up to 3)"},
            "pattern": {"type": "string", "description": "Regular expression the page URLs must match, e.g. /docs/ (optional)"},
            "sitemap": {"type": "boolean", "description": "Also take the URLs of the site's sitemap"},
            "same_host": {"type": "boolean", "description": "Stay on the same domain (default true)"},
            "save_dir": {"type": "string", "description": "Workspace folder where to save each page's FULL text as .md (optional)"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "search_images",
        "description": "Find real pictures on the internet (Wikipedia lead photos, Wikimedia Commons, Openverse) with their "
                       "author and licence. Use it when a document needs a photo of a person, place, object or event. "
                       "Then call download_file with the chosen `url` to bring it into the workspace / the user's files.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What the picture shows (a person's full name works well)"},
            "queries": {"type": "array", "items": {"type": "string"},
                        "description": "Several searches at once (up to 6), e.g. scientific name, common name, English name"},
            "source": {"type": "string", "enum": ["auto", "wikipedia", "commons", "openverse"],
                       "description": "auto = Wikipedia's main photo first, then Commons, then Openverse"},
            "limit": {"type": "integer", "description": "How many candidates per search (default 6)"}}}}},
    {"type": "function", "function": {
        "name": "extract_images",
        "description": "List the pictures of a web page (article, museum or institution page) with their alt text and captions, "
                       "biggest and captioned first. Use it when a page you found has the right photo or diagram; then "
                       "download_file the chosen `url`. Check the page's licence before using it and credit the source page.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "The web page"},
            "limit": {"type": "integer", "description": "How many pictures (default 20)"},
            "min_px": {"type": "integer", "description": "Skip pictures narrower than this (default 200)"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "search_papers",
        "description": "Find real scholarly papers (OpenAlex, Crossref): title, authors, year, journal, DOI, citations and abstract. "
                       "Use it to build a bibliography of verifiable references; then doi_to_bibtex for the BibTeX entries.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Topic, title words or authors"},
            "limit": {"type": "integer", "description": "How many papers (default 8, max 20)"},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
            "open_access": {"type": "boolean", "description": "Only papers with a free full text"},
            "source": {"type": "string", "enum": ["auto", "openalex", "crossref"]}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "doi_to_bibtex",
        "description": "Get the BibTeX entry of one or several DOIs from Crossref (up to 12). In a workspace, save_to appends the "
                       "new entries to that .bib file (existing keys are not duplicated) so you do not type them by hand.",
        "parameters": {"type": "object", "properties": {
            "dois": {"type": "array", "items": {"type": "string"}, "description": "DOIs like 10.1038/nature12345"},
            "save_to": {"type": "string", "description": "Workspace .bib file to append to, e.g. informe/references.bib"}},
            "required": ["dois"]}}},
    {"type": "function", "function": {
        "name": "download_file",
        "description": "Download a picture or file from a public URL into the workspace (path) or, in a chat, into the user's "
                       "files. The sandbox has NO internet, so this is the only way to get web images into a document. "
                       "Pictures are converted to JPEG/PNG and reduced to max_px. Also PDF, CSV, JSON, text, xlsx, docx, zip. "
                       "For web pages use fetch_url.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Direct URL of the file (use the `url` from search_images)"},
            "path": {"type": "string", "description": "Workspace path, e.g. imagenes/feijoo.jpg (optional; default imagenes/<name>)"},
            "max_px": {"type": "integer", "description": "Longest side in pixels for pictures (default 2000)"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "view_image",
        "description": "LOOK at a picture or at pages of a PDF with a small vision model and get what is wrong or what is shown: "
                       "text cut off or overprinted, tables or figures outside the margin, legends over data, blurry or placeholder "
                       "pictures. Use it after compiling a document or making a chart, then fix what is real. For PDFs the page "
                       "screenshots are saved in capturas/. Its remarks can be wrong: confirm them (sah_check, ws_read) before "
                       "changing anything. For a Word/PowerPoint/Excel file convert it to PDF first (soffice --headless "
                       "--convert-to pdf).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Workspace path of a PNG/JPG image or a PDF"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "Several workspace images checked together (up to 6), e.g. every picture you downloaded"},
            "file": {"type": "string", "description": "Chat only: name or id of an attached or generated file"},
            "pages": {"type": "string", "description": "PDF pages, e.g. \"1-3,8\" (at most 6 per call; default the first ones)"},
            "question": {"type": "string", "description": "What to check or describe; omit for a layout-defect review"}}}}},
]

DB_TOOLS = [
    {"type": "function", "function": {
        "name": "db_schema",
        "description": "List the tables and columns of one of the user's REMOTE databases (it runs on the remote server; "
                       "nothing is downloaded). Call it before writing SQL; pass `table` to see only one table.",
        "parameters": {"type": "object", "properties": {
            "connection": {"type": "string", "description": "Name of the database connection"},
            "table": {"type": "string", "description": "Only this table, e.g. 'public.orders' (optional)"}},
            "required": ["connection"]}}},
    {"type": "function", "function": {
        "name": "db_query",
        "description": "Run ONE read-only SQL query (SELECT / WITH / EXPLAIN / SHOW) on a REMOTE database. The query runs on the "
                       "remote server and only a limited number of rows come back, so aggregate (COUNT, SUM, GROUP BY), filter "
                       "and select just the columns you need; never try to bring a whole table. Writes are refused.",
        "parameters": {"type": "object", "properties": {
            "connection": {"type": "string", "description": "Name of the database connection"},
            "sql": {"type": "string", "description": "One SQL statement (PostgreSQL dialect)"}},
            "required": ["connection", "sql"]}}},
]

SKILL_TOOLS = [
    {"type": "function", "function": {
        "name": "use_skill",
        "description": "Load the full instructions of an installed skill before doing a task it covers.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "read_skill_file",
        "description": "Read a reference file bundled with a skill (paths are listed by use_skill).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
                       "required": ["name", "path"]}}},
    {"type": "function", "function": {
        "name": "find_skills",
        "description": "Look for skills (expert instruction packages) for a kind of task: installed ones, the hub "
                       "catalog and GitHub. Use it when a specialised task would benefit from expert guidance.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "install_skill",
        "description": "Install a skill for this user from the catalog or the internet (owner/repo/path, a GitHub URL "
                       "or a link to a SKILL.md). Afterwards load it with use_skill.",
        "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]}}},
]

CODE_TOOLS = [
    {"type": "function", "function": {
        "name": "run_code",
        "description": "Run a program in an isolated sandbox (no internet, ~2 min, Python 3.12 with numpy, pandas, "
                       "scipy, scikit-learn, sympy, matplotlib/seaborn, openpyxl, python-docx, python-pptx, reportlab, "
                       "pypdf; Node with docx and pptxgenjs; pandoc and LibreOffice headless). Use it for calculations, "
                       "data analysis, charts, and to create or convert files (Word, Excel, PowerPoint, PDF, CSV, PNG). "
                       "The working folder is /work: input files are in /work/in/, skill files in /work/skills/<name>/. "
                       "Save every result file in /work (e.g. /work/grafico.png): new files are given to the user "
                       "automatically. Files in /work stay there for your next run_code in this conversation, so fix or "
                       "extend them instead of writing them again. Print the key results to stdout.",
        "parameters": {"type": "object", "properties": {
            "language": {"type": "string", "enum": ["python", "bash", "node"]},
            "code": {"type": "string", "description": "Complete program"},
            "files": {"type": "array", "items": {"type": "string"},
                      "description": "Attached or drive files to copy into /work/in/ (file id or file name)"},
            "skills": {"type": "array", "items": {"type": "string"},
                       "description": "Installed skills whose scripts you need, copied to /work/skills/<name>/"}},
            "required": ["code"]}}},
]

CANVAS_TOOLS = [
    {"type": "function", "function": {
        "name": "canvas_write",
        "description": "Open a canvas next to the chat with a document (Markdown) or a code file the user will read, edit "
                       "and export (Word, PDF, Markdown). Use it for reports, articles, letters, plans, long answers the "
                       "user wants to iterate on, and complete programs. Writes a NEW canvas; to change the current one "
                       "use canvas_edit.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Full content (Markdown for documents)"},
            "kind": {"type": "string", "enum": ["document", "code"]},
            "language": {"type": "string", "description": "For code: python, javascript, sql, latex..."}},
            "required": ["title", "content"]}}},
    {"type": "function", "function": {
        "name": "canvas_edit",
        "description": "Change the current canvas with exact find/replace edits (copy `find` literally from the canvas). "
                       "To rewrite everything pass `content` instead of edits.",
        "parameters": {"type": "object", "properties": {
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "find": {"type": "string"}, "replace": {"type": "string"}, "all": {"type": "boolean"}},
                "required": ["find", "replace"]}},
            "content": {"type": "string", "description": "New full content (replaces everything)"},
            "title": {"type": "string"}}}}},
]

RESEARCH_TOOLS = [
    {"type": "function", "function": {
        "name": "deep_research",
        "description": "In-depth research on a question: plans sub-questions, runs several researcher agents in "
                       "parallel on the web (and the user's documents), and writes a long cited report into a canvas "
                       "the user can export to Word/PDF. Takes a few minutes. Use it when the user asks for a thorough "
                       "investigation, state of the art, market/literature review or a report with many sources.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "The research question with all relevant context"},
            "depth": {"type": "string", "enum": ["quick", "standard", "deep"],
                      "description": "quick = 3 sub-questions, standard = 5, deep = 7"},
            "use_documents": {"type": "boolean", "description": "Also search the user's documents"}},
            "required": ["question"]}}},
]

WORKSPACE_TOOLS = [
    {"type": "function", "function": {
        "name": "ws_list",
        "description": "List the files of the workspace (or of one folder).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Folder (empty = root)"}}}}},
    {"type": "function", "function": {
        "name": "ws_read",
        "description": "Read a text file of the workspace (code, CSV, Markdown...). For big files read a range of lines.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "ws_write",
        "description": "Create or overwrite a text file in the workspace (folders are created as needed).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "ws_append",
        "description": "Append text to the end of a file of the workspace (creates it if needed). Use it to write long "
                       "files (LaTeX, reports, big scripts) in several parts of ~150 lines each.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "ws_edit",
        "description": "Change part of a text file with exact find/replace edits (copy `find` literally from the file).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "find": {"type": "string"}, "replace": {"type": "string"}, "all": {"type": "boolean"}},
                "required": ["find", "replace"]}}},
            "required": ["path", "edits"]}}},
    {"type": "function", "function": {
        "name": "ws_run",
        "description": "Run a shell command in the workspace (current folder = workspace root, /work). No internet "
                       "except the Python and Node package registries. Installed: Python 3.12 (numpy, pandas, scipy, "
                       "scikit-learn, matplotlib, openpyxl, xlsxwriter, python-docx, python-pptx, reportlab, pypdf), "
                       "Node (docx, pptxgenjs), TeX Live (pdflatex, xelatex, lualatex, latexmk, biber; most LaTeX "
                       "packages), pandoc, LibreOffice (soffice --headless), imagemagick, qpdf. If a Python or Node "
                       "package is missing, install it: `pip install <pkg>` (kept in the workspace) or `npm install "
                       "<pkg>`. System packages (apt) cannot be installed. Returns stdout/stderr and the changed files.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "e.g. python informe.py && ls -la salida/"},
            "timeout_s": {"type": "integer"}}, "required": ["command"]}}},
]

TASK_TOOLS = [
    {"type": "function", "function": {
        "name": "schedule_task",
        "description": "ALWAYS call this when the user asks for something recurring or at a later time (\"cada lunes…\", \"todos los días a las…\", \"recuérdame…\"). Creates a scheduled task: the assistant will run `prompt` automatically on a schedule (e.g. "
                       "'every Monday at 9 summarise new arXiv papers on X') in its own chat, notify the user and "
                       "optionally e-mail the answer. Confirm the details with the user in your reply.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "prompt": {"type": "string", "description": "Self-contained instruction to run each time"},
            "freq": {"type": "string", "enum": ["hourly", "daily", "weekly", "monthly"]},
            "time": {"type": "string", "description": "HH:MM (24 h, user's local time)"},
            "weekdays": {"type": "array", "items": {"type": "integer"}, "description": "daily: 0=Monday … 6=Sunday"},
            "weekday": {"type": "integer", "description": "weekly: 0=Monday … 6=Sunday"},
            "day": {"type": "integer", "description": "monthly: day 1–28"},
            "email": {"type": "boolean", "description": "Also send the answer by e-mail"}},
            "required": ["name", "prompt", "freq"]}}},
]

PLAN_TOOLS = [
    {"type": "function", "function": {
        "name": "update_plan",
        "description": "Create or update your plan (checklist) for a task with several steps. Send the WHOLE list "
                       "every time. Keep exactly one item in_progress while you work, mark items completed as soon as "
                       "they are done, and add items when the task grows or the user asks for more. The user sees the "
                       "plan live. The turn does not end while items are pending or in progress.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Concrete step, e.g. 'Compilar informe.tex sin errores'"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                "required": ["content", "status"]}}},
            "required": ["items"]}}},
]

RESEARCH_PLAN_PROMPT = (
    "Plan an in-depth research. Split the question into {n} complementary sub-questions that together cover it "
    "(background, current state, data/figures, comparisons, risks or open problems, as relevant). Answer ONLY with "
    'JSON: {{"title": "report title", "subquestions": [{{"q": "...", "why": "..."}}]}}. Language of the question.')
RESEARCH_REPORT_PROMPT = (
    "Write a rigorous research report in Markdown, in the language of the question, from the researchers' findings "
    "below. Structure: # Title, a short executive summary, sections with ## headings (use tables where useful), "
    "conclusions and open questions, and a final '## Fuentes' / '## Sources' list. Cite every fact with the number "
    "of its source like [3]; use ONLY the numbered sources given; never invent facts, figures or URLs; say what "
    "could not be verified. Length: {words} words approximately.")

BUILTIN_AGENTS = {
    "investigador": {"description": "Investiga en internet: busca en varias fuentes, lee las páginas clave y devuelve "
                                    "un informe con hechos verificados y URLs.",
                     "instructions": "Eres un investigador meticuloso. Busca con web_search (varias consultas si hace "
                                     "falta, también category=science para literatura), abre con fetch_url las 2-5 "
                                     "fuentes más fiables y contrasta los datos. Devuelve un informe estructurado con "
                                     "los hallazgos, cifras y fechas, citando cada dato con su URL. Indica lo que no "
                                     "hayas podido verificar.",
                     "tools": ["web_search", "fetch_url", "fetch_urls", "search_images", "extract_images", "search_papers"]},
    "documentalista": {"description": "Busca y lee en los documentos del usuario (base de conocimiento y adjuntos).",
                       "instructions": "Eres un documentalista. Empieza con una consulta precisa a search_knowledge y "
                                       "haz una segunda solo si los primeros resultados son insuficientes. Lee con "
                                       "read_document lo necesario. Devuelve los fragmentos relevantes y una síntesis. "
                                       "En cada cita incluye [archivo, p. N] y la ruta source_path COMPLETA exacta que "
                                       "devuelva la herramienta; nunca la reduzcas a la carpeta raíz.",
                       "tools": ["search_knowledge", "read_document"]},
    "analista": {"description": "Analiza archivos de datos (CSV, Excel, Parquet, SQLite) con consultas SQL.",
                 "instructions": "Eres un analista de datos. Usa describe_data y query_data para responder con cifras "
                                 "exactas; muestra tablas en Markdown y explica el método. No inventes datos.",
                 "tools": ["describe_data", "query_data", "read_document", "run_code"]},
    "revisor": {"description": "Revisa críticamente un informe, texto, plan, código o respuesta: errores, huecos y mejoras. "
                               "LEE él mismo los archivos del workspace y los documentos; cita cada defecto con texto literal.",
                "instructions": "Eres un revisor exigente. REGLA DE PRUEBAS: lee tú mismo el material (ws_list/ws_read en el "
                                "workspace, read_document/search_knowledge para documentos) antes de opinar. Cada defecto que "
                                "señales debe llevar el archivo, la línea y una copia LITERAL del texto afectado; si no puedes "
                                "citarlo literalmente, no lo afirmes. Si no logras abrir el material, di exactamente qué no has "
                                "podido leer y NO inventes comentarios genéricos. Comprueba las cifras contra los datos "
                                "(describe_data/query_data o los CSV) y los hechos dudosos con web_search/fetch_url. Señala "
                                "errores, supuestos dudosos, riesgos y omisiones y propón mejoras concretas priorizadas "
                                "(críticas, importantes, menores). Termina diciendo qué archivos leíste.",
                "tools": ["ws_list", "ws_read", "read_document", "search_knowledge", "describe_data", "query_data",
                          "web_search", "fetch_url", "view_image"]},
    "general": {"description": "Agente de propósito general con las instrucciones que le des (campo instructions).",
                "instructions": "Eres un agente auxiliar. Cumple la tarea con las herramientas disponibles y devuelve "
                                "un resultado completo y conciso.",
                "tools": ["web_search", "fetch_url", "fetch_urls", "http_request", "crawl_site", "search_images", "extract_images",
                          "search_papers", "doi_to_bibtex", "download_file", "search_knowledge", "read_document",
                          "describe_data", "query_data", "use_skill", "read_skill_file", "ws_list", "ws_read", "ws_write",
                          "ws_append", "ws_edit", "ws_run", "view_image"]},
}
WEB_SAVE_TOOLS = {"fetch_url", "fetch_urls", "http_request", "crawl_site", "doi_to_bibtex"}  # write into the workspace only when given save_to/save_dir
SUB_AGENT_TOOLS = {"web_search", "fetch_url", "fetch_urls", "http_request", "crawl_site", "search_images", "extract_images", "search_papers",
                   "doi_to_bibtex", "download_file", "view_image", "search_knowledge", "read_document", "describe_data", "query_data",
                   "use_skill", "read_skill_file", "run_code"} | WS_TOOLS  # ws_* only inside a workspace
from .agent_catalog import EXTRA_AGENTS  # noqa: E402 - specialised agents (programador, tester, estadistico…)

BUILTIN_AGENTS.update({k: v for k, v in EXTRA_AGENTS.items() if k not in BUILTIN_AGENTS})

TOOL_LABELS = {"generate_image": "Generando imagen", "edit_image": "Editando imagen", "search_knowledge": "Buscando en tus documentos",
               "read_document": "Leyendo documento", "describe_data": "Analizando datos", "query_data": "Consultando datos",
               "save_memory": "Guardando en memoria", "web_search": "Buscando en internet", "fetch_url": "Leyendo página web", "fetch_urls": "Leyendo varias páginas", "http_request": "Consultando una API", "crawl_site": "Rastreando un sitio", "search_images": "Buscando imágenes", "extract_images": "Buscando imágenes en la página", "search_papers": "Buscando artículos", "doi_to_bibtex": "Obteniendo referencias", "download_file": "Descargando archivo", "view_image": "Mirando la imagen",
               "use_skill": "Cargando skill", "read_skill_file": "Leyendo archivo de skill", "find_skills": "Buscando skills",
               "install_skill": "Instalando skill", "delegate_task": "Agente trabajando", "compact_context": "Contexto compactado",
               "run_code": "Ejecutando código", "canvas_write": "Escribiendo en el lienzo",
               "canvas_edit": "Editando el lienzo", "deep_research": "Investigación a fondo",
               "schedule_task": "Programando tarea", "ws_list": "Mirando archivos", "ws_read": "Leyendo archivo",
               "ws_write": "Escribiendo archivo", "ws_append": "Añadiendo al archivo", "ws_edit": "Editando archivo", "ws_run": "Ejecutando en el workspace",
               "update_plan": "Actualizando el plan", "db_schema": "Mirando la base de datos", "db_query": "Consultando la base de datos"}


def agent_profiles(db, user: User) -> dict[str, dict]:
    """Built-in agents plus the user's own and public custom agents (name -> {description, instructions, tools})."""
    out = {k: {**v, "builtin": True} for k, v in BUILTIN_AGENTS.items()}
    from sqlalchemy import or_

    for a in db.scalars(select(AgentProfile).where(AgentProfile.enabled.is_(True), or_(
            AgentProfile.owner_id == user.id, AgentProfile.visibility == Visibility.public)).order_by(AgentProfile.name, AgentProfile.created_at, AgentProfile.id)):
        if a.name in out and a.owner_id != user.id:
            continue
        out[a.name] = {"description": a.description, "instructions": a.instructions, "builtin": False,
                       "tools": [t for t in (a.tools or []) if t in SUB_AGENT_TOOLS], "skills": a.skills or []}
    return out


def _delegate_spec(agents: dict[str, dict]) -> dict:
    return {"type": "function", "function": {
        "name": "delegate_task",
        "description": "Hand a self-contained task to a sub-agent that works with its own context and returns a "
                       "report. Call it several times in the same turn to run agents in parallel (e.g. research "
                       "different questions at once). The agent does not see this chat: give it all the context.",
        "parameters": {"type": "object", "properties": {
            "agent": {"type": "string", "enum": sorted(agents)},
            "task": {"type": "string", "description": "What the agent must do and deliver"},
            "context": {"type": "string", "description": "Facts, data or text the agent needs (optional)"},
            "instructions": {"type": "string", "description": "Extra role/instructions (for agent=general)"},
            "write_paths": {"type": "array", "items": {"type": "string"},
                            "description": "Workspace only: files or folders this agent may create/modify. Give agents "
                                           "running in parallel different paths; omit = whole workspace (one agent)."}},
            "required": ["agent", "task"]}}}


def scope_overlaps(a: list[str], b: list[str]) -> bool:
    """Two write scopes overlap when a path of one is equal to, inside or containing a path of the other ("" = all)."""
    return any(x == "" or y == "" or x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in a for y in b)


def in_scope(path: str, scope: list[str]) -> bool:
    return any(s == "" or path == s or path.startswith(s + "/") for s in scope)


def public_args(name: str, args: dict) -> dict:
    """Arguments as shown to the user and kept in the history: header values and request bodies of http_request are hidden."""
    if name != "http_request":
        return args
    out = dict(args)
    if isinstance(out.get("headers"), dict):
        out["headers"] = {k: ("«oculto»" if re.search(r"auth|key|token|secret|cookie|pass", str(k), re.I) else v)
                          for k, v in out["headers"].items()}
    return out


def urlsplit_host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or url


def urlsplit_name(url: str) -> str:
    from urllib.parse import unquote, urlsplit

    tail = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return tail.rsplit(".", 1)[0] or "archivo"


class Runner:
    def __init__(self, turn: Turn, emit: Callable[[dict], None], stop: Callable[[], bool]):
        self.t = turn
        self.emit = emit
        self.stop = stop
        self.parts: list[dict] = []
        self.refs: list[dict] = []
        self.files: dict[str, UserFile] = {}
        self.inst = None
        self.model: RegisteredModel | None = None
        self.ratio = CHARS_PER_TOKEN
        self.agents: dict[str, dict] = {}
        self.preferred_agent: str | None = None
        self.ws_claims: dict[str, tuple[str, list[str]]] = {}  # sub-agent call id -> (agent, write scope)
        self.ws_claims_lock = threading.Lock()
        self.skill_names: list[str] = []
        self.flags = {"web": False, "skills": False, "agents": False, "auto_install": False, "code": False, "docs": False}
        self.project_id: uuid.UUID | None = None
        self.image_failures = 0
        self._vision_cache: bool | None = None
        self._steered = False  # an urgent user message interrupted the current generation
        self._loop_hit: tuple[str, str] | None = None  # (reasoning|text, why) when loopguard cut the last stream
        self._steer_t = 0.0
        self.image_meta: dict[str, dict] = {}  # url -> author/licence of pictures found by search_images
        self.canvas_id: uuid.UUID | None = None
        self.small = False
        self.workspace = None
        self.lock = threading.Lock()
        self.summary: str | None = None
        self.ctx = 8192
        self.dropped = 0
        self.current_user: dict | None = None
        self.max_parallel = 3
        self.plan: list[dict] = []
        self.plan_part: dict | None = None  # the {"type": "plan"} entry of self.parts (latest plan, kept after reload)
        self.protected: set[int] = set()  # id() of injected user messages: compaction keeps them verbatim
        self.task_summary: str | None = None  # summary of this turn's older steps (mid-turn compaction)
        self.task_summary_msg: dict | None = None
        self.unverified = False  # workspace files written and not run/checked since
        self.spec_tokens = 0
        self.compactions = 0
        self.injected = 0
        self.max_steps = MAX_STEPS
        self.max_seconds = MAX_MINUTES_CHAT * 60
        self.decision: director.Decision | None = None
        self.db_names: list[str] = []  # the user's remote database connections (names only), when tools are on

    # -------------------------------------------------------------- context

    def _director_complete(self, model: RegisteredModel, messages: list[dict]) -> str:
        body = {"messages": messages, "max_tokens": director.LLM_TIMEOUT_TOKENS, "temperature": 0}
        if model.backend == "vllm":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        inst = scheduler.ensure_loaded(self.t.model_id, wait_s=10)  # a cold model: no director, the answer will load it
        scheduler.begin_request(inst.id)
        try:
            r = llm.chat_once(model, body)
        finally:
            scheduler.end_request(inst.id)
        return (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def _decide(self, db, conv: Conversation, model: RegisteredModel, files: list, ws_mode: bool) -> director.Decision | None:
        """The director: narrow what this message is offered to what it needs, then say so (event "director")."""
        if self.t.mode != "chat":
            return None
        avail = {c for c in ("docs", "web", "skills", "agents", "code") if self.flags.get(c, c == "docs")}
        if self.flags["web"] and self.flags["agents"]:
            avail.add("research")
        previous = ""
        try:
            last = [m for m in prior_messages(db, conv.id, *self.t.history_bounds()) if m.role == "assistant"]
            previous = _message_text(last[-1]) if last else ""
        except Exception:  # noqa: BLE001
            pass
        allow = director.normalize_allow(self.t.allow, {"thinking": self.t.thinking, "web": self.t.web, "research": self.t.research})
        ask = director.Ask(content=self.t.content, level=self.t.effort, available=avail, workspace=ws_mode, allow=allow,
                           project=bool(conv.project_id),
                           attachments=[f.filename for f in files], previous=previous, es=self.t.lang != "en",
                           director=self.t.director and bool(get_setting(db, "director_enabled")))
        db.commit()  # nothing to save: give the connection back instead of holding it while the model answers
        d = director.decide(ask, lambda msgs: self._director_complete(model, msgs))
        before = dict(self.flags)
        for cap in ("docs", "web", "skills", "agents", "code"):
            self.flags[cap] = self.flags.get(cap, cap == "docs") and getattr(d, cap)
        if self.t.preferred_agent:  # the person picked an agent for this chat: it needs its tools, whatever the message says
            self.flags.update(before)
        if model.backend == "vllm" and not (d.source == "fixed" and allow["think"] == "auto" and not d.think):
            self.t.thinking = d.think  # (without a director, "auto" leaves the model's own default alone)
        self.t.research = d.research and self.flags["web"] and self.flags["agents"]
        if d.steps:  # (a remote database needs room for: look at the schema, query, answer)
            self.max_steps = min(self.max_steps, max(d.steps, 8) if self.db_names else d.steps)
        self.emit({"type": "director", **d.public()})
        return d

    def _system(self, db, user: User, conv: Conversation, model: RegisteredModel, tools_on: bool) -> str:
        prefs = user.preferences or {}
        es = self.t.lang != "en"
        org = branding()["org_name"]
        lines = [(f"Eres el asistente de Spark AI Hub, la plataforma de IA privada{' de ' + org if org else ''}. Te ejecutas "
                  "localmente en un NVIDIA DGX Spark; las conversaciones no salen de este equipo.") if es else
                 (f"You are the Spark AI Hub assistant, the private AI platform{' of ' + org if org else ''}. You run "
                  "locally on an NVIDIA DGX Spark; conversations never leave this machine."),
                 f"{'Fecha' if es else 'Date'}: {_now_str()}. {'Usuario' if es else 'User'}: {user.display_name or user.username}.",
                 "Responde en el idioma del usuario. Usa Markdown (tablas, listas, bloques de código, LaTeX con $...$). "
                 "No inventes: si no estás seguro de un dato concreto (una persona, una obra, una fecha, una cifra, una cita), "
                 "dilo claramente en lugar de responder con seguridad, y ofrece buscarlo."
                 if es else "Answer in the user's language. Use Markdown (tables, lists, code blocks, LaTeX with $...$). "
                 "Do not make things up: if you are not sure of a specific fact (a person, a work, a date, a figure, a quote), "
                 "say so plainly instead of answering with confidence, and offer to look it up."]
        if tools_on:
            docs = self.flags.get("docs", True)
            lines.append(
                ("Herramientas: generate_image y edit_image crean/editan imágenes (la imagen se muestra sola al usuario; "
                 "no escribas enlaces ni markdown de imagen); "
                 + ("search_knowledge busca en los documentos del usuario "
                    "(cita así: [archivo, p. N] y añade la ruta source_path COMPLETA exacta, con el nombre del archivo; "
                    "nunca cites solo la carpeta raíz); read_document lee documentos largos adjuntos; describe_data y query_data "
                    "consultan archivos de datos con SQL; " if docs else "")
                 + "save_memory guarda datos duraderos que el usuario quiere que "
                 "recuerdes. Usa las herramientas solo cuando hagan falta y no inventes resultados."
                 + (" Si la pregunta es de cultura general o trata de un programa, una persona o un tema que no tiene por qué "
                    "estar en sus archivos, respóndela directamente con lo que sabes (di si no estás seguro); no busques en sus "
                    "documentos salvo que la pregunta se refiera a ellos." if docs else "")) if es else
                ("Tools: generate_image/edit_image create or edit images (the image is shown to the user automatically; "
                 "do not write image links); "
                 + ("search_knowledge searches the user's documents (cite as [file, p. N] and "
                    "include the exact COMPLETE source_path with the filename; never cite only its root folder); "
                    "read_document reads long attachments; describe_data/query_data query data files with SQL; " if docs else "")
                 + "save_memory stores lasting facts the user wants you to remember. Use tools only when needed; never invent results."
                 + (" If the question is general knowledge or about a program, person or topic that need not be in their files, "
                    "answer it directly from what you know (say if you are unsure); do not search their documents unless the "
                    "question refers to them." if docs else "")))
        if tools_on and self.flags["web"]:
            lines.append("Internet: web_search busca en la web (a través del buscador privado del Spark) y fetch_url lee "
                         "páginas y PDFs. Úsalas para actualidad, precios, versiones, normativa o datos que no sepas con "
                         "certeza; lee las fuentes antes de responder y cita así: [título](url). Nunca pongas datos "
                         "personales o confidenciales del usuario en una búsqueda. Más herramientas: fetch_urls lee hasta 10 "
                         "páginas a la vez (úsala tras buscar en vez de abrirlas una a una); web_search admite site, filetype, "
                         "page y limit (hasta 20); fetch_url con as_of lee la copia del Internet Archive de una fecha; "
                         "http_request llama a APIs públicas (GET/POST) y crawl_site recorre varias páginas de un sitio. "
                         "Con save_to/save_dir guardas lo descargado como archivo para procesarlo después con un script "
                         "(el código no tiene internet). Si una página no tiene texto porque necesita JavaScript, busca su API "
                         "de datos con http_request o una versión alternativa. Nunca envíes datos privados del usuario a "
                         "terceros con http_request." if es else
                         "Internet: web_search searches the web (through the Spark's private search engine) and fetch_url "
                         "reads pages/PDFs. Use them for current facts; read sources before answering and cite as "
                         "[title](url). Never put personal or confidential data in a query. More tools: fetch_urls reads up to 10 pages at "
                         "once (use it after searching instead of opening them one by one); web_search takes site, filetype, page and "
                         "limit (up to 20); fetch_url with as_of reads the Internet Archive copy of a date; http_request calls public "
                         "APIs (GET/POST) and crawl_site walks several pages of a site. save_to/save_dir keep what was downloaded as a "
                         "file for a script (code has no internet). If a page has no text because it needs JavaScript, find its data "
                         "API with http_request or an alternative version. Never send the user's private data to third parties with "
                         "http_request.")
        if tools_on and self.db_names:
            lines.append(("Bases de datos remotas del usuario: " + ", ".join(self.db_names) + ". Consúltalas con db_schema (tablas y columnas) y "
                          "db_query (UNA consulta SQL de solo lectura que se ejecuta en el servidor remoto). Mira el esquema antes de "
                          "escribir SQL, agrega (COUNT, SUM, GROUP BY) o usa LIMIT y pide solo las columnas necesarias: nunca intentes "
                          "traer la base de datos entera ni le pidas al usuario que la descargue." if es else
                          "The user's remote databases: " + ", ".join(self.db_names) + ". Use db_schema (tables and columns) and db_query "
                          "(ONE read-only SQL statement that runs on the remote server). Look at the schema before writing SQL, aggregate "
                          "(COUNT, SUM, GROUP BY) or use LIMIT and select only the columns you need: never try to bring the whole "
                          "database and never ask the user to download it."))
        if tools_on and self.flags["skills"]:
            sk = skills_svc.visible(db, user)
            self.skill_names = [x.name for x in sk]
            if sk:
                lines.append(("Skills instaladas (instrucciones expertas; carga la adecuada con use_skill ANTES de una tarea "
                              "que encaje y síguela):\n" if es else "Installed skills (load with use_skill before a matching task):\n")
                             + "\n".join(f"- {x.name}: {x.description[:260]}" for x in sk[:40]))
            extra = (" Si una tarea especializada lo merece y no hay skill adecuada, busca con find_skills e instálala con "
                     "install_skill." if self.flags["auto_install"] else
                     " Puedes buscar skills con find_skills; para instalarlas, el usuario debe hacerlo en la página Skills.")
            lines.append(((("Al cargar una skill con use_skill sus archivos se copian a .skills/<nombre>/ del workspace; "
                            "ejecuta sus scripts con ws_run.") if self.workspace is not None else
                           "Si una skill trae scripts, ejecútalos con run_code(skills=[nombre]); estarán en "
                           "/work/skills/<nombre>/." if es else "Run a skill's scripts with run_code(skills=[name]); "
                           "they are in /work/skills/<name>/.") if self.flags["code"] else
                          ("Las skills son solo instrucciones: no puedes ejecutar sus scripts; aplica sus pautas con tus "
                           "herramientas o muestra el código al usuario." if es else
                           "Skills are instructions only: you cannot run their scripts.")) + extra)
        if tools_on and self.workspace is not None:
            from . import workspace as ws_svc

            entries = ws_svc.tree(self.workspace)
            listing = "\n".join(f"- {e['path']}{'/' if e['type'] == 'dir' else ''}" + (f" ({e['size']} B)" if e['type'] == 'file' else "")
                                 for e in entries[:150]) or "(vacío)"
            lines.append((f"MODO WORKSPACE: trabajas en el workspace «{self.workspace.name}» del usuario, una carpeta "
                          "persistente en el Spark (/work). Usa ws_list, ws_read, ws_write, ws_edit y ws_run para crear, "
                          "revisar y ejecutar código y documentos AQUÍ (no uses run_code ni el lienzo). Trabaja como un "
                          "programador: escribe los archivos, ejecútalos, comprueba la salida y corrige hasta que funcione. "
                          "Guarda los resultados (Excel, PDF, gráficos, informes) en el workspace con nombres claros y dile "
                          "al usuario dónde están; él los ve y descarga en el panel de archivos. Los archivos largos (más de "
                          "~200 líneas) escríbelos por partes: ws_write con la primera y ws_append con el resto. Las imágenes "
                          "que generes con generate_image se guardan solas en imagenes/ del workspace. Hay LaTeX instalado "
                          "(compila con latexmk -pdf o xelatex); si falta una librería de Python o Node, instálala tú con "
                          "pip install / npm install en el workspace antes de usarla.") if es else
                         (f"WORKSPACE MODE: you work in the user's workspace «{self.workspace.name}», a persistent folder "
                          "(/work). Use ws_list/ws_read/ws_write/ws_edit/ws_run to create, check and run code and documents "
                          "HERE (not run_code or the canvas). Write files, run them, check the output and fix until it works. "
                          "Save results with clear names and tell the user where they are."))
            lines.append(("Datos de un informe: si el tema es el mundo real (política, economía, ciencia, empresas), recoge los "
                          "datos de fuentes con web_search/fetch_url y cita cada una; NO generes datos sintéticos ni de ejemplo "
                          "salvo que el usuario lo pida, y no des por «hipotético» un hecho reciente sin buscarlo (hoy es la fecha "
                          "indicada arriba; tu conocimiento puede estar desfasado). Todo lo simulado se rotula como simulado. "
                          "Si un subagente te devuelve citas, líneas o cifras, compruébalas antes de aplicarlas." if es else
                          "Report data: for real-world topics (politics, economy, science, companies) collect data from sources "
                          "with web_search/fetch_url and cite each; do NOT generate synthetic or sample data unless asked, and do "
                          "not call a recent fact «hypothetical» without searching (your knowledge may be out of date). Label "
                          "anything simulated as simulated. Verify any quotes, lines or figures a sub-agent returns before acting."))
            lines.append(self._media_guidance(es, True))
            lines.append(("Trabaja hasta terminar la tarea de verdad: aquí no hay un límite pequeño de pasos. Antes de dar "
                          "algo por hecho, verifícalo (ejecuta, compila, abre la salida o pasa los tests) y corrige hasta "
                          "que funcione; no termines con el trabajo a medias ni con «voy a…». OBJETIVOS: al empezar, convierte la "
                          "petición en una lista de requisitos comprobables dentro de tu plan (update_plan) e incluye pasos de "
                          "verificación (compilar, comprobador, mirar las páginas). La tarea NO termina hasta que todos se "
                          "cumplan y estén verificados: nunca cierres con «escribe continúa», «cuando quieras sigo» ni con "
                          "«¿sigo?»; si el trabajo es largo, continúa tú solo (el contexto se compacta solo). Solo te detienes "
                          "para una decisión o dato que únicamente el usuario puede dar: entonces haces UNA pregunta concreta. "
                          "Antes de terminar se te pedirá una auditoría de objetivos contra la petición original." if es else
                          "Work until the task is really finished: there is no small step limit here. Verify before "
                          "claiming something works (run, compile, open the output, run the tests) and fix until it does; "
                          "never stop half-way or with «I will…». GOALS: at the start turn the request into a list of checkable "
                          "requirements in your plan (update_plan) with verification steps (compile, checker, look at the pages). "
                          "The task is NOT over until all are met and verified: never close with «write continue», «tell me when "
                          "to go on» or «shall I go on?»; if the work is long, carry on by yourself (the context compacts "
                          "itself). Stop only for a decision or data only the user can give: then ask ONE concrete question. "
                          "Before finishing you will be asked for a goal audit against the original request."))
            lines.append(("Archivos actuales:\n" if es else "Current files:\n") + listing)
        if tools_on and self.flags["code"] and self.workspace is None:
            lines.append("Código: run_code ejecuta Python, bash o Node en un entorno aislado sin internet. Úsalo para "
                         "cálculos exactos, análisis y gráficos de datos, y para crear o convertir archivos (Word, Excel, "
                         "PowerPoint, PDF, imágenes). Guarda los resultados en /work: el usuario los recibe como archivos "
                         "descargables (no escribas enlaces). Si falla, lee el error, corrige y vuelve a intentarlo." if es else
                         "Code: run_code runs Python, bash or Node in an isolated sandbox without internet, for exact "
                         "calculations, data analysis, charts and creating/converting files. Save results in /work: the "
                         "user receives them as downloads (do not write links). If it fails, fix it and retry.")
        if tools_on and self.flags["code"] and self.workspace is None and not self.small:
            lines.append(self._media_guidance(es, False))
        if tools_on and self.workspace is None and not self.small:
            docs = canvas_svc.for_conversation(db, user.id, conv.id)
            self.canvas_id = docs[0].id if docs else None
            lines.append("Lienzo: con canvas_write abres junto al chat un documento o un programa que el usuario puede "
                         "editar y exportar a Word/PDF; úsalo para informes, artículos, cartas, planes o código largo, y "
                         "en el chat da solo un resumen breve. Para cambiarlo usa canvas_edit." if es else
                         "Canvas: canvas_write opens a document or program next to the chat that the user can edit and "
                         "export; use it for reports, articles, letters, plans or long code, and keep the chat answer "
                         "short. Change it with canvas_edit.")
            if docs:
                d = docs[0]
                lines.append((f"Lienzo actual «{d.title}» (versión {d.version}, puede tener cambios del usuario):\n"
                              if es else f"Current canvas «{d.title}» (version {d.version}, may contain user edits):\n")
                             + d.content[:30000] + ("\n[…recortado…]" if len(d.content) > 30000 else ""))
        if tools_on and self.flags["web"] and self.t.research:
            lines.append("El usuario ha activado «Investigación a fondo»: llama a deep_research con su pregunta completa "
                         "(añade el contexto de la conversación) y después resume en pocas líneas los hallazgos clave; "
                         "el informe completo queda en el lienzo." if es else
                         "The user enabled deep research: call deep_research with the full question, then summarise the "
                         "key findings briefly; the full report is in the canvas.")
        if tools_on and self.flags["agents"]:
            lines.append(("Subagentes (delegate_task): trabajan en paralelo con su propio contexto y te devuelven un "
                          "informe. Úsalos para tareas largas o separables (p. ej. investigar varios temas a la vez); "
                          "no para preguntas simples. Disponibles:\n" if es else
                          "Sub-agents (delegate_task) work in parallel with their own context. Available:\n")
                         + "\n".join(f"- {k}: {v['description'][:200]}" for k, v in self.agents.items()))
            if self.preferred_agent:
                lines.append(
                    (f"Agente preferido de esta conversación: «{self.preferred_agent}». Para cada petición sustantiva "
                     f"delega OBLIGATORIAMENTE la tarea principal a «{self.preferred_agent}» mediante delegate_task, "
                     "incluyendo en la tarea todo el contexto necesario. Integra y explica su resultado en tu respuesta. "
                     "Puedes delegar además partes complementarias a otros agentes cuando ayude; el agente preferido no "
                     "te impide coordinarlos. Para un saludo o una simple petición de aclaración no hace falta delegar."
                     if es else
                     f"Preferred agent for this conversation: '{self.preferred_agent}'. For every substantive request, "
                     f"you MUST delegate the main task to '{self.preferred_agent}' with delegate_task and provide all "
                     "necessary context. Integrate and explain its result. You may also delegate complementary work to "
                     "other agents. Greetings and simple clarification questions do not require delegation."))
            if self.workspace is not None:
                lines.append(
                    "En este workspace los subagentes también leen, escriben y ejecutan archivos. Para un trabajo grande "
                    "y separable (p. ej. varios módulos, código + tests + documentación): planifica, reparte las partes "
                    "independientes entre agentes llamando a delegate_task varias veces en el mismo turno, con write_paths "
                    "distintos para cada uno (archivos o carpetas que no se solapen) y todo el contexto que necesiten "
                    "(interfaces, nombres de funciones, formato de datos). Después integra tú los resultados, ejecuta y "
                    "prueba el conjunto, y corrige. Para cambios pequeños hazlo tú directamente." if es else
                    "In this workspace sub-agents can also read, write and run files. For large, separable work (several "
                    "modules, code + tests + docs): plan, give independent parts to agents by calling delegate_task "
                    "several times in the same turn, each with different non-overlapping write_paths and all the context "
                    "they need (interfaces, function names, data formats). Then integrate, run and test the whole, and "
                    "fix. Do small changes yourself.")
        if tools_on and self._tool_allowed("update_plan"):
            lines.append(
                "Tareas de varios pasos: antes de empezar crea un plan con update_plan (pasos concretos), mantén "
                "exactamente un paso «in_progress», márcalo «completed» en cuanto lo termines y actualiza el plan si la "
                "tarea cambia o el usuario añade algo. No te detengas a mitad: sigue llamando a las herramientas hasta "
                "completar TODOS los pasos. No anuncies lo que vas a hacer sin hacerlo (llama a la herramienta en la misma "
                "respuesta). Comprueba los resultados antes de darlos por buenos. Cuando todo esté completado, da un "
                "resumen final de lo hecho (y dónde está). Si el usuario te escribe mientras trabajas, su mensaje aparece "
                "marcado como nuevo: incorpóralo al plan y sigue. Para preguntas sencillas no hace falta plan." if es else
                "Multi-step tasks: first create a plan with update_plan (concrete steps), keep exactly one step "
                "in_progress, mark it completed as soon as it is done and update the plan when the task changes or the "
                "user adds something. Do not stop half-way: keep calling tools until ALL steps are completed. Never "
                "announce what you will do without doing it (call the tool in the same reply). Check results before "
                "trusting them. When everything is completed, give a final summary. If the user writes while you work, "
                "the message is marked as new: add it to the plan and go on. Simple questions need no plan.")
        if self.summary:
            lines.append(("Resumen de la parte anterior de esta conversación (los mensajes antiguos ya no están en tu "
                          "contexto; confía en este resumen):\n" if es else "Summary of the earlier conversation:\n")
                         + self.summary)
        if prefs.get("about_me"):
            lines.append(("Sobre el usuario (lo ha escrito él): " if es else "About the user (written by them): ")
                         + str(prefs["about_me"])[:3000])
        if prefs.get("custom_instructions"):
            lines.append(("Instrucciones del usuario: " if es else "User instructions: ") + str(prefs["custom_instructions"])[:5000])
        if prefs.get("memory_enabled", True):
            mems = memory_svc.list_for(db, user.id)
            if mems:
                lines.append(("Memoria (lo que sabes de esta persona, de más a menos importante; úsala con naturalidad, sin "
                              "citarla ni mencionar que la tienes salvo que te lo pregunten):\n" if es else
                              "Memory (what you know about this person, most important first; use it naturally, do not "
                              "quote it or mention you have it unless asked):\n") + memory_svc.render_prompt_block(mems, es))
        if conv.project_id:
            pr = db.get(Project, conv.project_id)
            if pr:
                lines.append((f"Proyecto actual: «{pr.name}». " if es else f"Current project: «{pr.name}». ")
                             + (pr.description or ""))
                if pr.instructions:
                    lines.append(("Instrucciones del proyecto: " if es else "Project instructions: ") + pr.instructions[:20000])
                if pr.collection_id:
                    lines.append("Los documentos del proyecto están disponibles con search_knowledge." if es
                                 else "Project documents are available through search_knowledge.")
        if self.refs:
            lines.append(("Imágenes de esta conversación (usa la referencia con edit_image): " if es else "Images: ")
                         + "; ".join(f"{r['ref']} = {r['desc']}" for r in self.refs))
        if self.t.system_prompt:
            lines.append(self.t.system_prompt[:50000])
        return "\n\n".join(lines)

    def _attachment_parts(self, db, model: RegisteredModel, files: list[UserFile], budget_chars: int) -> tuple[list, list[str]]:
        """Content parts for the current user message (+ warnings)."""
        parts: list = []
        warnings: list[str] = []
        vision = llm.supports_vision(model)
        used = 0
        for f in files:
            self.files[str(f.id)] = f
            if f.kind == "image":
                if vision:
                    ref = next((r["ref"] for r in self.refs if r["id"] == str(f.id)), "")
                    parts.append({"type": "text", "text": f"[Imagen adjunta {ref}: {f.filename}]"})
                    parts.append({"type": "image_url", "image_url": {"url": _data_uri(files_svc.abs_path(f))}})
                else:
                    ref = next((r["ref"] for r in self.refs if r["id"] == str(f.id)), "")
                    parts.append({"type": "text", "text": f"[Imagen adjunta {ref}: {f.filename} — este modelo no ve "
                                                          f"imágenes, pero puedes editarla con edit_image]"})
                    warnings.append(f"El modelo {model.alias} no tiene visión: no puede ver «{f.filename}». "
                                    "Elige un modelo con la etiqueta «vision» (p. ej. qwen3.5-35b-a3b) para que la analice.")
            elif f.kind == "data":
                try:
                    info = dataq.describe(f)
                    parts.append({"type": "text", "text": f"[Archivo de datos {f.filename} (file_id={f.id}). Esquema:\n"
                                                          f"{json.dumps(info, ensure_ascii=False, default=str)[:6000]}\n"
                                                          "Usa query_data para consultarlo.]"})
                except dataq.DataError as e:
                    parts.append({"type": "text", "text": f"[Archivo {f.filename}: no se pudo leer ({e})]"})
            elif f.kind == "document":
                txt = files_svc.read_text(f) if f.text_status == "ready" else ""
                if not txt and f.mime == "application/pdf" and vision:
                    for i, png in enumerate(files_svc.render_pdf_pages(f, max_pages=4)):
                        parts.append({"type": "text", "text": f"[{f.filename} — página {i + 1} (escaneada)]"})
                        parts.append({"type": "image_url", "image_url": {"url": _png_uri(png)}})
                    warnings.append(f"«{f.filename}» no tiene texto (¿escaneado?): se envían las 4 primeras páginas como imagen.")
                elif not txt:
                    parts.append({"type": "text", "text": f"[{f.filename}: no se pudo extraer texto]"})
                    warnings.append(f"No se pudo extraer texto de «{f.filename}».")
                elif used + len(txt) <= budget_chars:
                    used += len(txt)
                    parts.append({"type": "text", "text": f"<documento nombre=\"{f.filename}\" file_id=\"{f.id}\">\n{txt}\n</documento>"})
                else:
                    head = txt[: max(2000, min(8000, budget_chars - used))]
                    used += len(head)
                    parts.append({"type": "text", "text": f"<documento nombre=\"{f.filename}\" file_id=\"{f.id}\" "
                                                          f"longitud=\"{len(txt)} caracteres\" parcial=\"sí\">\n{head}\n</documento>\n"
                                                          "Documento largo: usa read_document con este file_id para leer más."})
            else:
                parts.append({"type": "text", "text": f"[Archivo adjunto {f.filename} ({f.mime}); formato no legible por el chat]"})
        return parts, warnings

    def _history(self, db, conv_id, vision: bool, budget_chars: int, after_id: int | None = None) -> list[dict]:
        msgs = [m for m in prior_messages(db, conv_id, *self.t.history_bounds()) if after_id is None or m.id > after_id]
        out: list[dict] = []
        img_idx = 0
        ref_by_id = {r["id"]: r["ref"] for r in self.refs}
        recent_user_images = {m.id for m in [x for x in msgs if x.role == "user" and any(
            a.get("kind") == "image" for a in (x.attachments or []))][-2:]}
        for m in msgs:
            if m.role == "user":
                notes = []
                imgs = []
                for a in m.attachments or []:
                    if a.get("kind") == "image":
                        img_idx += 1
                        ref = ref_by_id.get(a["file_id"], "img?")
                        if vision and m.id in recent_user_images:
                            f = db.get(UserFile, uuid.UUID(a["file_id"]))
                            if f:
                                imgs.append({"type": "image_url", "image_url": {"url": _data_uri(files_svc.abs_path(f), 896)}})
                        notes.append(f"[imagen adjunta {ref}: {a.get('name')}]")
                    else:
                        notes.append(f"[archivo adjunto: {a.get('name')} file_id={a['file_id']}]")
                text = m.content + ("\n" + "\n".join(notes) if notes else "")
                out.append({"role": "user", "content": ([{"type": "text", "text": text}] + imgs) if imgs else text})
            elif m.role == "assistant":
                extra = []
                for part in m.parts or []:
                    if part.get("image"):
                        extra.append(f"[{ref_by_id.get(part['image']['id'], 'img')}: imagen generada y mostrada]")
                    elif part.get("summary"):
                        extra.append(f"[{part.get('name')}: {part['summary'][:300]}]")
                out.append({"role": "assistant", "content": (m.content or "") + ("\n" + "\n".join(extra) if extra else "")})
        # Drop the oldest turns when the history does not fit.
        total = 0
        kept: list[dict] = []
        for msg in reversed(out):
            c = msg["content"]
            size = len(c) if isinstance(c, str) else sum(len(p.get("text", "")) + (3000 if p["type"] == "image_url" else 0) for p in c)
            if total + size > budget_chars and kept:
                break
            total += size
            kept.append(msg)
        self.dropped = len(out) - len(kept)
        return list(reversed(kept))

    # -------------------------------------------------------------- tools

    def _wait_job(self, job_id: uuid.UUID, call_id: str, timeout: float = 1800) -> Job:
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            with SessionLocal() as db:
                j = db.get(Job, job_id)
                if j.status == JobStatus.queued and time.time() - t0 > IMAGE_QUEUE_WAIT_S:
                    # nothing started in minutes (memory or a busy engine): do not hold the whole turn waiting
                    raise AgentError("La imagen sigue en cola tras varios minutos (no hay memoria o el motor está ocupado); "
                                     "continúa sin imágenes generadas y no la repitas. Para fotos reales usa search_images + "
                                     "download_file. El trabajo queda en Trabajos.")
                if self.stop():
                    request_cancel(db, j)
                    db.commit()
                    raise AgentError("Cancelado por el usuario")
                state = (j.status.value, round(j.progress or 0, 2), j.message)
                if state != last:
                    last = state
                    self.emit({"type": "tool_progress", "id": call_id, "progress": j.progress, "message": j.message,
                               "status": j.status.value})
                if j.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled, JobStatus.interrupted):
                    db.expunge(j)
                    return j
            time.sleep(1.0)
        raise AgentError("La generación tarda demasiado; sigue en Trabajos. No la repitas ahora: continúa sin imágenes generadas "
                         "(para fotos reales de personas o lugares usa search_images y download_file).")

    def _release_llm(self):
        if self.inst is not None:
            scheduler.end_request(self.inst.id)
            self.inst = None

    def _image_job(self, call_id: str, wf_id: str, inputs: dict, prompt: str) -> dict:
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            wf = workflows.WORKFLOWS[wf_id]
            params = workflows.validate(wf, inputs)
            try:
                j = create_job(db, user, "image_generate", {"workflow_id": wf_id, "inputs": params},
                               title=f"Chat: {prompt[:60]}", sched_class=SchedClass.interactive,
                               requested_resources={"memory_gb": workflows.estimate_memory(wf, params), "gpu_heavy": wf.gpu_heavy})
            except JobError as e:
                raise AgentError(e.message)
            jid = j.id
        self._release_llm()  # let the scheduler evict the idle chat model if the image model needs the memory
        job = self._wait_job(jid, call_id)
        if job.status != JobStatus.completed:
            raise AgentError(f"La generación falló: {(job.error or job.status.value)[:500]}")
        art = next((a for a in (job.result or {}).get("artifacts", []) if a.get("kind") == "image"), None)
        if not art:
            raise AgentError("La generación terminó sin imagen")
        meta = {"id": art["id"], "url": art["url"], "prompt": prompt, "workflow": wf_id, "job_id": str(jid),
                "seconds": (job.result or {}).get("meta", {}).get("generation_seconds")}
        self.refs.append({"source": "artifact", "id": art["id"], "desc": f"generada: {prompt[:120]}",
                          "ref": f"img{len(self.refs) + 1}"})
        meta["ref"] = self.refs[-1]["ref"]
        if self.workspace is not None:  # in a workspace the image also lands in imagenes/ so documents can use it
            try:
                with SessionLocal() as db:
                    a = db.get(Artifact, uuid.UUID(art["id"]))
                    src = get_settings().data_root / a.path
                    rel = f"imagenes/{meta['ref']}{src.suffix.lower() or '.png'}"

                    def save(db2, ws, svc):
                        svc.write(db2, ws, rel, src.read_bytes())
                        db2.commit()
                    self._ws_call(save)
                    meta["workspace_path"] = rel
            except Exception:  # noqa: BLE001  (the image is still shown in the chat)
                pass
        return meta

    def _image_guard(self):
        if self.image_failures >= IMAGE_FAILS_MAX:
            raise AgentError(f"La generación de imágenes ha fallado {self.image_failures} veces en este turno: no insistas. "
                             "Continúa sin imágenes generadas; para fotos reales usa search_images y download_file.")

    def tool_generate_image(self, call_id: str, args: dict) -> dict:
        self._image_guard()
        with SessionLocal() as db:
            wf_id = pick_workflow(db, "txt2img")
        if not wf_id:
            raise AgentError("No hay ningún modelo de imagen instalado.",
                             "Un administrador puede instalarlo en Image Studio → Instalar (Qwen-Image o SD 1.5).")
        family = workflows.WORKFLOWS[wf_id].family
        w, h = ASPECTS["qwen_image" if family == "qwen_image" else "sd15"].get(args.get("aspect") or "square",
                                                                                 ASPECTS["sd15"]["square"])
        inputs = {"prompt": str(args.get("prompt", ""))[:3000], "width": w, "height": h}
        if args.get("negative_prompt"):
            inputs["negative_prompt"] = str(args["negative_prompt"])[:1000]
        if isinstance(args.get("seed"), int):
            inputs["seed"] = abs(args["seed"]) % (2**48)
        try:
            img = self._image_job(call_id, wf_id, inputs, inputs["prompt"])
        except AgentError:
            self.image_failures += 1
            raise
        where = f" Guardada en el workspace como {img['workspace_path']}." if img.get("workspace_path") else ""
        return {"ok": True, "summary": f"Imagen {img['ref']} generada con {wf_id} ({w}×{h}) y mostrada al usuario.{where}",
                "image": img, **({"workspace": {"changed": [img["workspace_path"]]}} if img.get("workspace_path") else {})}

    def tool_edit_image(self, call_id: str, args: dict) -> dict:
        self._image_guard()
        ref = str(args.get("image_ref", "")).strip().lower()
        target = next((r for r in self.refs if r["ref"] == ref), None) or (self.refs[-1] if self.refs and not ref else None)
        if not target:
            raise AgentError(f"No existe la imagen «{ref}» en esta conversación. Disponibles: "
                             + ", ".join(r["ref"] for r in self.refs) if self.refs else "No hay imágenes en esta conversación.")
        with SessionLocal() as db:
            wf_id = pick_workflow(db, "edit")
        if not wf_id:
            raise AgentError("No hay ningún modelo de edición de imagen instalado.",
                             "Un administrador puede instalar Qwen-Image-Edit en Image Studio.")
        instr = str(args.get("instruction", ""))[:3000]
        inputs = {"prompt": instr, "image": target["id"]}
        if wf_id == "sd15-img2img":
            inputs["denoise"] = 0.6
        if isinstance(args.get("seed"), int):
            inputs["seed"] = abs(args["seed"]) % (2**48)
        try:
            img = self._image_job(call_id, wf_id, inputs, instr)
        except AgentError:
            self.image_failures += 1
            raise
        img["edited_from"] = target["ref"]
        where = f" Guardada en el workspace como {img['workspace_path']}." if img.get("workspace_path") else ""
        return {"ok": True, "summary": f"{target['ref']} editada → {img['ref']} (mostrada al usuario).{where}", "image": img,
                **({"workspace": {"changed": [img["workspace_path"]]}} if img.get("workspace_path") else {})}

    def tool_search_knowledge(self, call_id: str, args: dict) -> dict:
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            res = kb.search(db, user, str(args.get("query", ""))[:1000], None, 6)
        if not res["results"]:
            return {"ok": True, "summary": "Sin resultados en los documentos del usuario.", "sources": [],
                    "content": "No se encontró nada relevante en los documentos del usuario."}
        sources = [{"filename": r["filename"], "page": r["page"], "file_id": r["file_id"], "collection": r["collection"],
                    "source_path": r.get("source_path"), "snippet": r["text"][:280]} for r in res["results"]]
        content = "\n\n".join(f"[{i + 1}] {r['filename']}" + (f", p. {r['page']}" if r["page"] else "")
                              + (f" — {r['source_path']}" if r.get("source_path") else "")
                              + f" ({r['collection']}):\n{r['text'][:1800]}" for i, r in enumerate(res["results"]))
        return {"ok": True, "summary": f"{len(sources)} fragmentos ({res['mode']})", "sources": sources, "content": content}

    def _file(self, fid: str) -> UserFile:
        if fid in self.files:
            return self.files[fid]
        with SessionLocal() as db:
            try:
                f = db.get(UserFile, uuid.UUID(fid))
            except ValueError:
                f = None
            if f is None:
                raise AgentError(f"Archivo {fid} no encontrado")
            user = db.get(User, self.t.user_id)
            if f.owner_id != user.id:  # documents of shared collections/projects are readable too
                from ..models import KbDocument

                ok = db.scalar(select(KbDocument.id).where(KbDocument.file_id == f.id, KbDocument.collection_id.in_(
                    kb.readable_collection_ids(db, user))))
                if not ok:
                    raise AgentError(f"Archivo {fid} no encontrado")
            db.expunge(f)
        self.files[fid] = f
        return f

    def tool_read_document(self, call_id: str, args: dict) -> dict:
        f = self._file(str(args.get("file_id", "")))
        pages = files_svc.read_pages(f)
        if not pages:
            raise AgentError(f"{f.filename} no tiene texto extraído")
        want = [int(p) for p in (args.get("pages") or []) if isinstance(p, (int, float))][:10]
        if want:
            sel = [(p, t) for p, t in pages if p in want]
            content = "\n\n".join(f"--- página {p} ---\n{t}" for p, t in sel)[:TOOL_RESULT_CHARS]
            return {"ok": True, "summary": f"{f.filename}: páginas {want}", "content": content or "Páginas no encontradas"}
        q = str(args.get("query") or "")
        chunks = kb.chunk_pages(pages)
        words = set(re.findall(r"\w{3,}", q.lower()))
        scored = sorted(chunks, key=lambda c: -sum(c[1].lower().count(w) for w in words)) if words else chunks
        out, size = [], 0
        for p, t in scored[:12]:
            if size + len(t) > TOOL_RESULT_CHARS:
                break
            size += len(t)
            out.append((f"--- página {p} ---\n" if p else "--- fragmento ---\n") + t)
        return {"ok": True, "summary": f"{f.filename}: {len(out)} fragmentos", "content": "\n\n".join(out)}

    def tool_describe_data(self, call_id: str, args: dict) -> dict:
        f = self._file(str(args.get("file_id", "")))
        try:
            info = dataq.describe(f)
        except dataq.DataError as e:
            raise AgentError(str(e))
        return {"ok": True, "summary": f"{f.filename}: " + ", ".join(f"{t['table']} ({t['rows']} filas)" for t in info["tables"]),
                "content": json.dumps(info, ensure_ascii=False, default=str)[:TOOL_RESULT_CHARS]}

    def tool_query_data(self, call_id: str, args: dict) -> dict:
        f = self._file(str(args.get("file_id", "")))
        sql = str(args.get("sql", ""))[:5000]
        try:
            res = dataq.query(f, sql)
        except dataq.DataError as e:
            return {"ok": False, "summary": f"Error SQL: {e}"[:300], "content": f"SQL error: {e}", "sql": sql}
        md = dataq.as_markdown(res)
        return {"ok": True, "summary": f"{len(res.get('rows') or [])} filas", "sql": sql, "table": md,
                "content": md[:TOOL_RESULT_CHARS]}

    def _db_connection(self, db, name):
        c = remote_db.find(db, self.t.user_id, str(name or ""))
        if c is None:
            raise AgentError("No tienes ninguna base de datos con ese nombre. Conectadas: " + (", ".join(self.db_names) or "ninguna"))
        return c

    def tool_db_schema(self, call_id: str, args: dict) -> dict:
        with SessionLocal() as db:
            c = self._db_connection(db, args.get("connection"))
            try:
                res = remote_db.schema(db, c, args.get("table"))
            except remote_db.RemoteDbError as e:
                return {"ok": False, "summary": e.message[:300], "content": f"Error: {e.message}"}
            name = c.name
        md = remote_db.schema_markdown(res)
        return {"ok": True, "summary": f"{name}: {len(res['tables'])} tablas", "content": md[:TOOL_RESULT_CHARS]}

    def tool_db_query(self, call_id: str, args: dict) -> dict:
        from ..audit import audit

        sql = str(args.get("sql") or "")[:remote_db.MAX_SQL_CHARS]
        with SessionLocal() as db:
            c = self._db_connection(db, args.get("connection"))
            user = db.get(User, self.t.user_id)
            try:
                res = remote_db.query(db, c, sql)
            except remote_db.RemoteDbError as e:
                audit(db, "databases.query", actor=user, target_type="db_connection", target_id=str(c.id), success=False,
                      details={"name": c.name, "via": "assistant", "sql_chars": len(sql), "code": e.code})
                return {"ok": False, "summary": f"Error SQL: {e.message}"[:300], "content": f"SQL error: {e.message}", "sql": sql}
            audit(db, "databases.query", actor=user, target_type="db_connection", target_id=str(c.id),
                  details={"name": c.name, "via": "assistant", "sql_chars": len(sql), "rows": res["row_count"],
                           "truncated": res["truncated"], "ms": res["ms"]})
            name = c.name
        md = remote_db.as_markdown(res)
        return {"ok": True, "summary": f"{name}: {res['row_count']} filas" + (" (hay más)" if res["truncated"] else ""), "sql": sql,
                "table": md, "content": md[:TOOL_RESULT_CHARS]}

    def _remember_fallback(self) -> None:
        """«Recuerda que…» is a promise of the UI: if the model did not save it, save it here."""
        m = REMEMBER_RE.match(self.t.content or "")
        if not m or any(p.get("name") == "save_memory" for p in self.parts) or len(self.t.content) > 600:
            return
        fact = m.group(1).strip().rstrip(".")
        if len(fact) < 3:
            return
        res = self.run_tool(f"call_{uuid.uuid4().hex[:12]}", "save_memory", {"content": fact[0].upper() + fact[1:]})
        _ = res

    def tool_save_memory(self, call_id: str, args: dict) -> dict:
        content = str(args.get("content", "")).strip()[:500]
        if not content:
            raise AgentError("Memoria vacía")
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            if not (user.preferences or {}).get("memory_enabled", True):
                return {"ok": False, "summary": "La memoria está desactivada por el usuario", "content": "Memory is disabled."}
            m = Memory(owner_id=user.id, content=content, source="assistant", conversation_id=self.t.conversation_id)
            db.add(m)
            db.commit()
            mid = str(m.id)
        return {"ok": True, "summary": f"Guardado en memoria: {content}", "memory": {"id": mid, "content": content},
                "content": "Saved."}

    # -------------------------------------------------------------- internet

    def tool_web_search(self, call_id: str, args: dict) -> dict:
        q = str(args.get("query", "")).strip()[:400]
        if not q:
            raise AgentError("Consulta vacía")
        site = re.sub(r"^https?://|/.*$", "", str(args.get("site") or "").strip())
        ft = re.sub(r"[^a-z0-9]", "", str(args.get("filetype") or "").lower())[:8]
        full = " ".join(x for x in (q, f"site:{site}" if site else "", f"filetype:{ft}" if ft else "") if x)
        try:
            limit = max(1, min(20, int(args.get("limit") or 8)))
            page_no = max(1, min(10, int(args.get("page") or 1)))
        except (TypeError, ValueError):
            limit, page_no = 8, 1
        with SessionLocal() as db:
            try:
                res = web.search(db, full, str(args.get("category") or "web"), None, limit, args.get("time_range"), page_no)
            except web.WebError as e:
                raise AgentError(str(e))
        if not res:
            return {"ok": True, "summary": f"Sin resultados para «{full}»", "sources": [], "query": q,
                    "content": "No results. Try other keywords, another category, another page or drop site/filetype."}
        content = "\n\n".join(f"[{i + 1}] {r['title']}\n{r['url']}" + (f" ({r['published']})" if r["published"] else "")
                               + f"\n{r['snippet']}" for i, r in enumerate(res))
        return {"ok": True, "summary": f"{len(res)} resultados para «{full}»" + (f" (página {page_no})" if page_no > 1 else ""), "query": q,
                "sources": [{"title": r["title"], "url": r["url"], "snippet": r["snippet"], "kind": "web"} for r in res],
                "content": content + "\n\nRead the most relevant results together with fetch_urls (or one by one with fetch_url) "
                                    "before answering; page=2 gives more results."}

    def _store(self, rel: str, data: bytes) -> dict:
        """Keep fetched data as a file: in the workspace (rel = path) or, in a chat, in the user's files («Descargas»).
        Returns {"where": text, "changed": [...]} or {"where": text, "files": [...]}."""
        if self.workspace is not None:
            def save(db, ws, svc):
                out = svc.write(db, ws, rel, data)
                db.commit()
                return out
            out = self._ws_call(save)
            return {"where": f"en el workspace: {out['path']}", "changed": [out["path"]]}
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            try:
                f = sandbox.store_bytes(db, user, rel.rsplit("/", 1)[-1] or "datos.txt", data, folder="Descargas",
                                        project_id=self.project_id, generated_by="web")
            except Exception as e:  # noqa: BLE001 (quota, type)
                raise AgentError(f"No se pudo guardar el archivo: {e}")
        if not f:
            raise AgentError("Ese tipo de archivo no se puede guardar; usa .md, .txt, .json o .csv")
        return {"where": f"en los archivos del usuario (Descargas): «{f['filename']}»", "files": [f]}

    @staticmethod
    def _attach_saved(res: dict, saved: list[dict]) -> dict:
        for s in saved:
            if s.get("changed"):
                res.setdefault("workspace", {"changed": []})["changed"] += s["changed"]
            if s.get("files"):
                res.setdefault("files", []).extend(s["files"])
        return res

    def tool_fetch_url(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        as_of = str(args.get("as_of") or "").strip()
        try:
            page = web.wayback_page(url, as_of) if as_of else web.read_page(url)
        except web.WebError as e:
            raise AgentError(str(e))
        text = page["text"]
        if not text:
            raise AgentError("La página no tiene texto legible")
        title = page["title"] or url
        saved = []
        if args.get("save_to"):
            saved.append(self._store(str(args["save_to"]).strip(), f"# {title}\nURL: {page['url']}\n\n{text}".encode()))
        text = web_tools.excerpt(text, str(args.get("query") or ""), TOOL_RESULT_CHARS)
        via = {"archive": " · copia del Internet Archive", "openalex": " · ficha OpenAlex", "europepmc": " · ficha Europe PMC"}.get(page.get("via") or "", "")
        res = {"ok": True, "summary": f"{title[:90]} ({len(page['text']):,} caracteres){via}".replace(",", "."),
               "sources": [{"title": title, "url": page["url"], "kind": "web", "snippet": page["text"][:240]}],
               "content": f"# {title}\nURL: {page['url']}\n" + (f"{page['note']}\n" if page.get("note") else "")
                          + (f"Texto completo guardado {saved[0]['where']}\n" if saved else "") + f"\n{text}"}
        return self._attach_saved(res, saved)

    def tool_fetch_urls(self, call_id: str, args: dict) -> dict:
        urls = args.get("urls")
        if isinstance(urls, str):
            urls = [u for u in re.split(r"[\s,]+", urls) if u]
        if not isinstance(urls, list) or not urls:
            raise AgentError("Pasa urls como lista de direcciones")
        urls = [str(u) for u in urls][:web_tools.MAX_URLS]
        self.emit({"type": "tool_progress", "id": call_id, "message": f"Leyendo {len(urls)} páginas a la vez…"})
        try:
            pages = web_tools.read_many(urls)
        except web_tools.ToolError as e:
            raise AgentError(e.message)
        per = max(1500, TOOL_RESULT_CHARS // max(1, len(pages)))
        q = str(args.get("query") or "")
        save_dir = str(args.get("save_dir") or "").strip().strip("/")
        saved, blocks, sources = [], [], []
        for i, p in enumerate(pages, 1):
            if not p["ok"]:
                blocks.append(f"## [{i}] ✗ {p['url']}\nNo se pudo leer: {p['error']}")
                continue
            sources.append({"title": p["title"], "url": p["final_url"], "kind": "web", "snippet": p["text"][:240]})
            where = ""
            if save_dir and self.workspace is not None:
                st = self._store(f"{save_dir}/{web_tools.slug_for(p['final_url'])}.md", f"# {p['title']}\nURL: {p['final_url']}\n\n{p['text']}".encode())
                saved.append(st)
                where = f"\n(texto completo: {st['where']})"
            blocks.append(f"## [{i}] {p['title']}\n{p['final_url']}{where}\n{web_tools.excerpt(p['text'], q, per)}")
        ok = sum(1 for p in pages if p["ok"])
        if not ok:
            raise AgentError("No se pudo leer ninguna: " + "; ".join(f"{p['url']}: {p['error']}" for p in pages)[:600])
        res = {"ok": True, "summary": f"{ok}/{len(pages)} páginas leídas" + (f" · {len(saved)} guardadas" if saved else ""),
               "sources": sources, "content": "\n\n".join(blocks)}
        return self._attach_saved(res, saved)

    def tool_http_request(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        try:
            r = web_tools.request(url, str(args.get("method") or "GET"), args.get("params") if isinstance(args.get("params"), dict) else None,
                                  args.get("headers") if isinstance(args.get("headers"), dict) else None,
                                  args.get("json") if args.get("json") is not None else None,
                                  args.get("form") if isinstance(args.get("form"), dict) else None)
        except web_tools.ToolError as e:
            raise AgentError(e.message)
        status, ct, body = r["status"], r["content_type"], r["body"]
        saved = []
        if args.get("save_to") and status < 400:
            saved.append(self._store(str(args["save_to"]).strip(), body))
        shown = web_tools.render_body(body, ct, TOOL_RESULT_CHARS)
        head = f"HTTP {status} · {ct.split(';')[0] or 'sin tipo'} · {len(body) / 1024:.1f} KB · {r['url']}"
        if status >= 400:
            return {"ok": False, "summary": f"HTTP {status} de {urlsplit_host(r['url'])}",
                    "content": f"{head}\n{shown[:3000]}"}
        res = {"ok": True, "summary": f"HTTP {status} · {len(body) / 1024:.1f} KB" + (f" · guardado {saved[0]['where']}" if saved else ""),
               "sources": [{"title": r["url"][:100], "url": r["url"], "kind": "web", "snippet": ""}],
               "content": head + (f"\nRespuesta completa guardada {saved[0]['where']}" if saved else "") + "\n\n" + shown}
        return self._attach_saved(res, saved)

    def tool_crawl_site(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()

        def progress(n, total, u):
            self.emit({"type": "tool_progress", "id": call_id, "progress": min(0.95, n / max(1, total)),
                       "message": f"{n}/{total} páginas · {urlsplit_host(u)}"})
        try:
            out = web_tools.crawl(url, args.get("max_pages") or 15, args.get("depth") if args.get("depth") is not None else 2,
                                  args.get("same_host") is not False, str(args.get("pattern") or "") or None,
                                  bool(args.get("sitemap")), progress, self.stop)
        except (web_tools.ToolError, re.error) as e:
            raise AgentError(getattr(e, "message", None) or f"Patrón no válido: {e}")
        pages = out["pages"]
        if not pages:
            raise AgentError("No se pudo leer ninguna página" + (f" (omitidas: {out['skipped']})" if out["skipped"] else ""),
                             "Prueba con otra URL de inicio, sin pattern, o con sitemap=true")
        save_dir = str(args.get("save_dir") or "").strip().strip("/")
        saved, blocks, sources = [], [], []
        per = max(600, TOOL_RESULT_CHARS // max(1, len(pages)))
        for i, p in enumerate(pages, 1):
            sources.append({"title": p["title"], "url": p["url"], "kind": "web", "snippet": p["text"][:240]})
            where = ""
            if save_dir and self.workspace is not None:
                st = self._store(f"{save_dir}/{web_tools.slug_for(p['url'])}.md", f"# {p['title']}\nURL: {p['url']}\n\n{p['text']}".encode())
                saved.append(st)
                where = f" → {st['changed'][0]}"
            blocks.append(f"[{i}] {p['title']} (nivel {p['depth']}){where}\n{p['url']}\n{web_tools.excerpt(p['text'], None, per)}")
        sk = (" · omitidas: " + ", ".join(f"{k} {v}" for k, v in out["skipped"].items())) if out["skipped"] else ""
        res = {"ok": True, "summary": f"{len(pages)} páginas en {out['seconds']} s" + (f" · {len(saved)} guardadas" if saved else "") + sk,
               "sources": sources[:40],
               "content": f"Rastreo de {url}: {len(pages)} páginas, {out['queued']} enlaces sin visitar{sk}.\n\n" + "\n\n".join(blocks)}
        return self._attach_saved(res, saved)

    # -------------------------------------------------------------- pictures and files from the internet

    def _media_guidance(self, es: bool, ws: bool) -> str:
        """How to make figures/tables that fit, check them, and get real pictures (the sandbox has no internet)."""
        web = self.flags.get("web")
        where = "en /work/.sah (ya instalado)" if ws else "(ya disponible: import sah_plot)"
        if es:
            t = ("Gráficas y tablas legibles: usa `from sah_plot import new_fig, save_fig, table_image` " + where + ". new_fig(15, 9) "
                 "da un tamaño que cabe en una página; save_fig(fig, 'figs/x.png') comprueba y corrige textos solapados, textos "
                 "fuera de la figura y leyendas que tapan datos, e imprime «OK» o «PROBLEMAS»: con PROBLEMAS la figura NO está "
                 "lista (acorta etiquetas, agranda, usa barras horizontales o menos categorías y guarda otra vez). table_image(df, "
                 "'figs/t.png') dibuja tablas que no se salen. Títulos cortos; nunca guardes con plt.savefig a ciegas. ")
            if ws:
                t += ("En LaTeX usa `from sah_tex import PREAMBLE, df_to_latex, figure` (tablas que se adaptan al ancho, apaisadas o "
                      "en longtable según su tamaño; figuras con ancho y alto acotados) y, tras compilar con latexmk -g -pdf, ejecuta "
                      "`python .sah/sah_check.py informe.pdf`: marca cajas desbordadas, contenido pegado al borde, texto encimado, "
                      "referencias y citas rotas. Arregla todo lo que marque y repite antes de decir que has terminado: un PDF "
                      "que compila sin errores NO está listo si el comprobador marca problemas. Después, mira el resultado con view_image "
                      "(pages=\"1-4\", etc.): un modelo de visión pequeño revisa capturas de las páginas y te dice qué se ve mal; "
                      "sus avisos pueden ser falsos, confírmalos antes de corregir, y revisa al menos las páginas con figuras, "
                      "tablas anchas y la portada. ")
            t += self._image_policy(True, web, ws)
            return t
        t = ("Legible charts and tables: use `from sah_plot import new_fig, save_fig, table_image` " + ("(installed in /work/.sah) " if ws else "(available) ")
             + "— save_fig(fig, 'figs/x.png') checks and fixes overlapping text, text outside the figure and legends covering data, "
             "and prints OK or PROBLEMAS: with PROBLEMAS the figure is NOT ready (shorter labels, larger size, horizontal bars, fewer "
             "categories, save again). table_image(df, 'figs/t.png') draws tables that never overflow. Never save blindly with plt.savefig. ")
        if ws:
            t += ("In LaTeX use `from sah_tex import PREAMBLE, df_to_latex, figure` and after latexmk -g -pdf run "
                  "`python .sah/sah_check.py report.pdf`; fix everything it flags (overfull boxes, content at the page edge, "
                  "overprinted text, broken references) before saying you are done. Then look at the result with view_image "
                  "(pages=\"1-4\"): a small vision model reviews page screenshots and says what looks wrong; its remarks can be "
                  "false, confirm them before fixing, and check at least the pages with figures, wide tables and the cover. ")
        t += self._image_policy(False, web, ws)
        return t

    @staticmethod
    def _image_policy(es: bool, web: bool, ws: bool) -> str:
        """Where each kind of picture comes from: real ones from the internet, charts drawn by the LaTeX figure agent."""
        if es:
            if not web:
                return ("Imágenes: la búsqueda en internet está desactivada en este turno, así que no hay fotos reales: no dibujes "
                        "a mano animales, personas, fósiles ni lugares con matplotlib/TikZ (parecen falsos); omite esas figuras "
                        "y avisa al usuario de que activando Internet las traerías. Los gráficos de datos y diagramas sí puedes "
                        "hacerlos. ")
            return ("POLÍTICA DE IMÁGENES (obligatoria). El sandbox NO tiene red (curl/wget/requests fallan: no lo intentes), pero tú "
                    "SÍ tienes internet con tus herramientas. 1) Fotos, retratos, reconstrucciones, fósiles, seres vivos, lugares, "
                    "mapas históricos o paleogeográficos y cualquier imagen del mundo real: búscalas con search_images (varias "
                    "consultas por tema: nombre científico y común, en inglés y en español; queries=[...] hace varias a la vez), "
                    "si la web de un artículo tiene la imagen, extract_images(url) lista sus imágenes con su pie; descárgalas con "
                    "download_file" + (" a imagenes/ del workspace" if ws else " a los archivos del usuario")
                    + ", MÍRALAS con view_image (ruta o varias en una llamada) para comprobar que son lo que buscas y descarta las "
                    "que no lo sean, y acredita cada una (autor, licencia, enlace"
                    + ("; están en imagenes/creditos.json" if ws else "") + "). NUNCA dibujes a mano con matplotlib/TikZ siluetas ni "
                    "«reconstrucciones» de animales, personas o lugares, ni uses generate_image para figuras de un informe: parecen "
                    "falsas. Si de verdad no existe imagen real, omite la figura y dilo. 2) Gráficos, diagramas, líneas de tiempo, "
                    "árboles filogenéticos, esquemas, mapas de calor y tablas visuales: llama al agente graficador-latex con "
                    "delegate_task (dale los datos o rutas de los CSV, qué debe mostrar y una carpeta propia en write_paths); "
                    "dibuja en LaTeX/TikZ/pgfplots, sin solapes y con la tipografía del documento. Solo una gráfica de datos muy "
                    "simple puede hacerse tú con sah_plot. 3) Para referencias bibliográficas reales usa search_papers y "
                    "doi_to_bibtex (nunca inventes DOI ni digas que no hay red). ")
        if not web:
            return ("Pictures: web search is off this turn, so there are no real photos: do not hand-draw animals, people, fossils or "
                    "places with matplotlib/TikZ (they look fake); leave those figures out and tell the user turning the Internet on "
                    "would bring them. Data charts and diagrams are fine. ")
        return ("IMAGE POLICY (mandatory). The sandbox has NO network (curl/wget/requests fail: do not try) but YOU have internet "
                "through your tools. 1) Photos, portraits, reconstructions, fossils, living things, places, historical or "
                "palaeogeographic maps and any real-world picture: find them with search_images (several queries per subject: "
                "scientific and common name, English and Spanish; queries=[...] runs several at once); if an article page has the "
                "picture, extract_images(url) lists its images with captions; download them with download_file"
                + (" into imagenes/ of the workspace" if ws else " into the user's files")
                + ", LOOK at them with view_image to check they are what you wanted and drop the ones that are not, and credit each "
                "(author, licence, link" + ("; imagenes/creditos.json has them" if ws else "") + "). NEVER hand-draw silhouettes or "
                "«reconstructions» of animals, people or places with matplotlib/TikZ, nor use generate_image for report figures: "
                "they look fake. If no real picture exists, leave the figure out and say so. 2) Charts, diagrams, timelines, "
                "phylogenetic trees, schematics, heatmaps and visual tables: call the graficador-latex agent with delegate_task "
                "(give it the data or CSV paths, what to show and its own folder in write_paths); it draws in LaTeX/TikZ/pgfplots "
                "with no overlaps, in the document's typeface. Only a very simple data chart may be made by you with sah_plot. "
                "3) For real references use search_papers and doi_to_bibtex (never invent DOIs or claim there is no network). ")

    def _vision_ok(self) -> bool:
        if self._vision_cache is None:
            try:
                with SessionLocal() as db:
                    self._vision_cache = vision.available(db)
            except Exception:  # noqa: BLE001
                self._vision_cache = False
        return self._vision_cache

    def _view_many(self, call_id: str, paths: list[str], question: str | None) -> dict:
        """view_image over several workspace pictures at once (e.g. every photo just downloaded)."""
        shots: list[tuple[str, bytes]] = []
        for rel in paths[:6]:
            def grab(db, ws, svc, rel=rel):
                p, _ = svc.raw(ws, rel)
                return p
            src = self._ws_call(grab)
            if src.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                raise AgentError(f"{rel}: con paths solo se miran imágenes (para un PDF usa path y pages)")
            if src.stat().st_size > 25 * 1024 * 1024:
                raise AgentError(f"{rel} pesa más de 25 MB")
            shots.append((rel, src.read_bytes()))
        self.emit({"type": "tool_progress", "id": call_id, "message": f"Mirando {len(shots)} imágenes con el modelo de visión…"})
        q = question or ("Para CADA imagen (usa su etiqueta) di en una línea qué muestra exactamente (especie, objeto, lugar, "
                         "mapa, gráfico…), si es una foto/ilustración real o un dibujo esquemático, y si está borrosa, cortada, "
                         "vacía o es un logotipo/icono. No inventes lo que no se vea.")
        with SessionLocal() as db:
            helper = vision.helper_model(db, self.model)
            if helper is None:
                raise AgentError("No hay ningún modelo con visión instalado.", "Instala uno en Modelos (p. ej. qwen3-vl:4b-instruct).")
            alias = helper.alias
            parts: list[str] = []
            for i in range(0, len(shots), 3):
                chunk = shots[i:i + 3]
                try:
                    parts.append(vision.analyze(helper, [b for _, b in chunk], q, [l for l, _ in chunk]))
                except (EngineError, scheduler.SchedulingError) as e:
                    raise AgentError(f"El modelo de visión no pudo responder: {getattr(e, 'message', e)}")
        return {"ok": True, "summary": f"{len(shots)} imágenes vistas con {alias}",
                "content": f"Qué muestra cada imagen (modelo {alias}):\n" + "\n\n".join(x for x in parts if x)
                           + "\n\nAVISO: un modelo pequeño puede confundirse. Descarta las que no correspondan a lo que buscabas, "
                             "las borrosas, las diminutas y los logotipos; si dudas, busca otra."}

    def tool_view_image(self, call_id: str, args: dict) -> dict:
        many = [str(x).strip() for x in (args.get("paths") or []) if str(x).strip()] if isinstance(args.get("paths"), list) else []
        if many and self.workspace is not None:
            return self._view_many(call_id, many, str(args.get("question") or "").strip() or None)
        path = str(args.get("path") or "").strip()
        ref = str(args.get("file") or "").strip()
        question = str(args.get("question") or "").strip() or None
        src, name = None, ""
        if path and self.workspace is not None:
            def grab(db, ws, svc):
                p, _ = svc.raw(ws, path)
                return p
            src, name = self._ws_call(grab), path
        elif ref:
            f = next((x for x in self.files.values() if x.filename == ref or str(x.id) == ref), None) or self._file(ref)
            src, name = files_svc.abs_path(f), f.filename
        else:
            raise AgentError("Indica path (archivo del workspace) o file (archivo del chat)")
        ext = src.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".pdf"):
            raise AgentError(f"No puedo mirar archivos {ext or 'sin extensión'}: solo imágenes y PDF.",
                             "Convierte Word/PowerPoint/Excel a PDF con soffice --headless --convert-to pdf y mira el PDF.")
        shots: list[tuple[str, bytes]] = []
        pagenums: list[int] = []
        saved: list[str] = []
        if ext == ".pdf":
            total = vision.pdf_pages(src)
            pages = vision.parse_pages(args.get("pages"), total)
            if not pages:
                raise AgentError(f"El PDF tiene {total} páginas; pide páginas entre 1 y {total}")
            for p, png in vision.render_pdf(src, pages):
                shots.append((f"página {p} de {total}", png))
                pagenums.append(p)
        else:
            if src.stat().st_size > 25 * 1024 * 1024:
                raise AgentError("La imagen pesa más de 25 MB")
            shots.append(("imagen", src.read_bytes()))
        self.emit({"type": "tool_progress", "id": call_id, "message": f"Mirando {len(shots)} imagen(es) con el modelo de visión…"})
        with SessionLocal() as db:
            helper = vision.helper_model(db, self.model)
            if helper is None:
                raise AgentError("No hay ningún modelo con visión instalado.", "Instala uno en Modelos (p. ej. qwen3-vl:4b-instruct).")
            alias = helper.alias
            parts: list[str] = []
            for i in range(0, len(shots), 3):  # a small model reads better a few pages at a time
                chunk = shots[i:i + 3]
                try:
                    parts.append(vision.analyze(helper, [b for _, b in chunk], question, [l for l, _ in chunk]))
                except (EngineError, scheduler.SchedulingError) as e:
                    raise AgentError(f"El modelo de visión no pudo responder: {getattr(e, 'message', e)}")
        if self.workspace is not None and ext == ".pdf":
            stem = src.stem

            def save(db2, ws, svc):
                for (label, png), p in zip(shots, pagenums):
                    rel = f"capturas/{stem}-p{p}.png"
                    svc.write(db2, ws, rel, png)
                    saved.append(rel)
                db2.commit()
            try:
                self._ws_call(save)
            except AgentError:
                saved = []
        report = "\n\n".join(x for x in parts if x) or "(el modelo de visión no devolvió texto)"
        return {"ok": True, "summary": f"{name}: {len(shots)} imagen(es) vistas con {alias}",
                **({"workspace": {"changed": saved}} if saved else {}),
                "content": f"Revisión visual de «{name}» ({', '.join(l for l, _ in shots)}) con el modelo {alias}:\n{report}\n\n"
                           "AVISO: un modelo pequeño puede equivocarse o repetirse. Confirma cada defecto (sah_check, ws_read, medir) "
                           "antes de cambiar nada y corrige solo lo que sea real. Si dice «OK: sin defectos visibles», fíate "
                           "de ello solo si sah_check tampoco marca nada." + (f"\nCapturas guardadas en: {', '.join(saved)}" if saved else "")}

    def tool_search_images(self, call_id: str, args: dict) -> dict:
        qs = [str(x).strip() for x in (args.get("queries") or []) if str(x).strip()] if isinstance(args.get("queries"), list) else []
        if str(args.get("query", "")).strip():
            qs.insert(0, str(args["query"]).strip())
        qs = list(dict.fromkeys(qs))[:6]
        if not qs:
            raise AgentError("Falta query o queries")
        try:
            limit = max(1, min(10, int(args.get("limit") or 6)))
        except (TypeError, ValueError):
            limit = 6
        per = limit if len(qs) == 1 else min(limit, 4)
        lang = "en" if self.t.lang == "en" else "es"
        blocks, srcs, n, seen, errors = [], [], 0, set(), []
        for q in qs:
            try:
                res = media_fetch.search_images(q, str(args.get("source") or "auto"), per, lang)
            except media_fetch.MediaError as e:
                errors.append(f"«{q}»: {e.message}")
                continue
            res = [r for r in res if r["url"] not in seen]
            seen.update(r["url"] for r in res)
            if not res:
                blocks.append(f"## «{q}»: sin resultados nuevos (prueba el nombre en inglés, el científico o source=commons)")
                continue
            lines = []
            for i, r in enumerate(res, 1):
                self.image_meta[r["url"]] = r
                self.image_meta[r.get("original_url") or r["url"]] = r
                n += 1
                lines.append(f"{i}. {r['title']} — {r.get('width')}×{r.get('height')} px · {r.get('license') or 'licencia desconocida'}"
                             f" · autor: {r.get('author') or 'desconocido'} · {r['source']} · coincidencia con la búsqueda: "
                             f"{r.get('match', '?')}\n   url: {r['url']}\n   ficha: {r.get('page_url') or '-'}")
                srcs.append({"title": r["title"], "url": r.get("page_url") or r["url"], "kind": "web",
                             "snippet": media_fetch.credit_line(r)})
            blocks.append(f"## «{q}»\n" + "\n".join(lines))
        if not n:
            raise AgentError("No encontré imágenes para " + ", ".join(f"«{q}»" for q in qs) + (f" ({'; '.join(errors)})" if errors else ""),
                             "Prueba con el nombre completo, en inglés, el nombre científico o source=commons; o busca la página con "
                             "web_search y usa extract_images(url)")
        return {"ok": True, "summary": f"{n} imágenes para " + ", ".join(f"«{q}»" for q in qs), "sources": srcs,
                "content": "Candidatas (la coincidencia solo compara palabras del título/ficha: antes de usarlas, descárgalas y mira "
                           "cada una con view_image; descarta las que no sean lo que buscas). Descarga la elegida con "
                           "download_file(url=…). Conserva autor y licencia para acreditarla en el documento.\n\n"
                           + "\n\n".join(blocks) + (("\n\nErrores: " + "; ".join(errors)) if errors else "")}

    def tool_extract_images(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        try:
            out = research_tools.page_images(url, int(args.get("limit") or 20), int(args.get("min_px") or 200))
        except research_tools.ToolError as e:
            raise AgentError(e.message, e.hint)
        except (TypeError, ValueError):
            raise AgentError("limit y min_px deben ser números")
        if not out["images"]:
            raise AgentError("La página no tiene imágenes utilizables (quizá se cargan con JavaScript)",
                             "Prueba search_images con el tema, o busca otra página con web_search")
        lines = []
        for i, im in enumerate(out["images"], 1):
            self.image_meta[im["url"]] = {"title": im["alt"] or im["caption"] or f"imagen de {out['title']}", "author": "",
                                          "license": "ver la página de origen", "page_url": out["url"],
                                          "source": urlsplit_host(out["url"])}
            size = f"{im['width']}×{im['height']} px" if im.get("width") and im.get("height") else "tamaño desconocido"
            lines.append(f"{i}. {size}" + (f" · {im['kind']}" if im["kind"] == "principal" else "")
                         + (f" · pie: {im['caption']}" if im["caption"] else "") + (f" · alt: {im['alt']}" if im["alt"] else "")
                         + f"\n   url: {im['url']}")
        return {"ok": True, "summary": f"{len(out['images'])} imágenes de {urlsplit_host(url)}",
                "sources": [{"title": out["title"][:100], "url": out["url"], "kind": "web", "snippet": ""}],
                "content": f"Imágenes de «{out['title']}» ({out['url']}), {out['total']} en total. Antes de usar una, descárgala con "
                           "download_file(url=…), mírala con view_image y comprueba la licencia en la página; acredita la página "
                           "de origen.\n" + "\n".join(lines)}

    def tool_search_papers(self, call_id: str, args: dict) -> dict:
        def num(k):
            try:
                return int(args[k]) if args.get(k) else None
            except (TypeError, ValueError):
                return None
        try:
            res = research_tools.search_papers(str(args.get("query", "")), int(args.get("limit") or 8), num("year_from"), num("year_to"),
                                               str(args.get("source") or "auto"), bool(args.get("open_access")))
        except research_tools.ToolError as e:
            raise AgentError(e.message, e.hint)
        if not res:
            raise AgentError("No encontré artículos para esa búsqueda", "Prueba con palabras clave en inglés o quita el filtro de años")
        return {"ok": True, "summary": f"{len(res)} artículos ({res[0]['source']})",
                "sources": [{"title": r["title"][:120], "url": ("https://doi.org/" + r["doi"]) if r["doi"] else (r["oa_url"] or ""),
                             "kind": "web", "snippet": f"{', '.join(r['authors'][:2])} ({r.get('year') or 's. f.'}) {r.get('venue') or ''}"[:240]}
                            for r in res if r["doi"] or r["oa_url"]],
                "content": "Artículos reales encontrados (cita solo lo que figura aquí; para la entrada BibTeX usa doi_to_bibtex "
                           "con el DOI; el resumen puede faltar):\n" + "\n".join(research_tools.format_paper(i, p) for i, p in enumerate(res, 1))}

    def tool_doi_to_bibtex(self, call_id: str, args: dict) -> dict:
        dois = [str(x).strip() for x in (args.get("dois") or []) if str(x).strip()] if isinstance(args.get("dois"), list) else []
        if not dois:
            raise AgentError("Falta la lista dois")
        entries, bad = [], []
        for d in dois[:research_tools.MAX_DOIS]:
            try:
                entries.append(research_tools.bibtex(d))
            except research_tools.ToolError as e:
                bad.append(e.message)
        if not entries:
            raise AgentError("No se obtuvo ninguna entrada: " + "; ".join(bad))
        text = "\n\n".join(entries)
        res = {"ok": True, "summary": f"{len(entries)} entradas BibTeX" + (f" · {len(bad)} fallidas" if bad else "")}
        save_to = str(args.get("save_to") or "").strip()
        note = ""
        if save_to and self.workspace is not None:
            def merge(db, ws, svc):
                try:
                    cur = svc.read(ws, save_to)["content"] or ""
                except Exception:  # noqa: BLE001
                    cur = ""
                new, added, skipped = research_tools.merge_bib(cur, entries)
                svc.write(db, ws, save_to, new.encode())
                db.commit()
                return added, skipped
            added, skipped = self._ws_call(merge)
            res["workspace"] = {"changed": [save_to]}
            note = (f"\nGuardadas en {save_to}: {len(added)} nuevas ({', '.join(added)[:300]})"
                    + (f"; ya estaban: {', '.join(skipped)}" if skipped else "") + ". Cítalas con \\cite{clave}.")
        res["content"] = text[:9000] + note + (("\nFallaron: " + "; ".join(bad)) if bad else "")
        return res

    def _dl_default_path(self, args: dict) -> str:
        meta = self.image_meta.get(str(args.get("url", "")).strip())
        name = media_fetch.slug((meta or {}).get("title", "").rsplit(".", 1)[0] or urlsplit_name(str(args.get("url", ""))))
        return f"imagenes/{name}"

    def tool_download_file(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        try:
            max_px = max(200, min(6000, int(args.get("max_px") or 2000)))
        except (TypeError, ValueError):
            max_px = 2000
        want = str(args.get("path") or "").strip()
        base = want or self._dl_default_path(args)
        suffix = "." + base.rsplit(".", 1)[1].lower() if "." in base.rsplit("/", 1)[-1] else ""
        try:
            got = media_fetch.fetch_any(url, suffix, max_px, "png" if suffix == ".png" else None)
        except media_fetch.MediaError as e:
            raise AgentError(e.message)
        rel = (base[: -len(suffix)] if suffix else base) + got["ext"]
        meta = self.image_meta.get(url)
        credit = media_fetch.credit_line(meta) if meta else ""
        size_txt = (f"{got['width']}×{got['height']} px (original {got['original_size'][0]}×{got['original_size'][1]})"
                    if got["kind"] == "image" else f"{len(got['data']) // 1024} KB")
        if self.workspace is not None:
            def save(db, ws, svc):
                out = svc.write(db, ws, rel, got["data"])
                cred_path = "imagenes/creditos.json"
                if got["kind"] == "image" and meta:  # one place with every picture's credit, for the document's credits section
                    import json as _json
                    try:
                        cur = _json.loads(svc.read(ws, cred_path)["content"] or "{}")
                    except Exception:  # noqa: BLE001
                        cur = {}
                    cur[rel] = {k: meta.get(k) for k in ("title", "author", "license", "license_url", "page_url", "source")}
                    svc.write(db, ws, cred_path, _json.dumps(cur, ensure_ascii=False, indent=1).encode())
                db.commit()
                return out
            self._ws_call(save)
            hint = ""
            if got["kind"] == "image":
                hint = (f"\nEn LaTeX: \\includegraphics[width=0.45\\linewidth]{{{rel}}} (o usa sah_tex.figure). "
                        "Compila con latexmk -g para que detecte el archivo nuevo.")
            return {"ok": True, "summary": f"{rel} ({size_txt})", "workspace": {"changed": [rel]},
                    "content": f"Guardado en el workspace: {rel} — {size_txt}." + (f"\nCrédito: {credit}" if credit else "")
                               + (f"\nTodos los créditos están en imagenes/creditos.json." if credit else "") + hint}
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            try:
                f = sandbox.store_bytes(db, user, rel.rsplit("/", 1)[-1], got["data"], folder="Descargas", project_id=self.project_id,
                                        generated_by="download")
            except Exception as e:  # noqa: BLE001 (quota, type)
                raise AgentError(f"No se pudo guardar el archivo: {e}")
        if not f:
            raise AgentError("El tipo de archivo no está permitido")
        show = (f"\nPara mostrarla dentro de tu respuesta escribe: ![descripción](/api/v1/files/{f['id']}/content)"
                if got["kind"] == "image" else "")
        return {"ok": True, "summary": f"{f['filename']} ({size_txt})", "files": [f],
                "content": f"Guardado en los archivos del usuario (carpeta Descargas): «{f['filename']}» — {size_txt}."
                           + (f"\nCrédito: {credit}" if credit else "") + show}

    # -------------------------------------------------------------- skills

    def tool_use_skill(self, call_id: str, args: dict) -> dict:
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            sk = skills_svc.find(db, user, str(args.get("name", "")))
            if not sk:
                raise AgentError(f"No hay ninguna skill «{args.get('name')}» instalada. Instaladas: "
                                 + (", ".join(self.skill_names) or "ninguna"))
            skills_svc.mark_used(db, sk)
            db.commit()
            files = sorted((sk.files or {}).keys())
            content = sk.content[:24000]
            name, src = sk.name, sk.source_url
        note = ("\n\nBundled files (read with read_skill_file): " + ", ".join(files)) if files else ""
        copied = ""
        if self.workspace is not None and self.flags["code"]:
            def put(db, ws, svc):
                svc.write(db, ws, f".skills/{name}/SKILL.md", content.encode())
                with SessionLocal() as db2:
                    sk2 = skills_svc.find(db2, db2.get(User, self.t.user_id), name)
                    for rel, text in (sk2.files or {}).items() if sk2 else []:
                        try:
                            svc.write(db, ws, f".skills/{name}/{rel}", str(text).encode())
                        except svc.WorkspaceError:
                            pass
                db.commit()
            try:
                self._ws_call(put)
                copied = f".skills/{name}/"
            except AgentError:
                pass
        tail = (f"(Its files are in the workspace at {copied}: run its scripts there with ws_run, e.g. "
                f"python {copied}scripts/<script>.py.)" if copied else
                f"(To run its scripts use run_code with skills=[\"{name}\"]; they are in /work/skills/{name}/.)"
                if self.flags["code"] else "(Scripts cannot be executed here: apply the guidance with your tools or give "
                                           "the code to the user.)")
        return {"ok": True, "summary": f"Skill «{name}» cargada", "skill": {"name": name, "source_url": src},
                "content": f"# Skill: {name}\n{content}{note}\n\n{tail}"}

    def tool_read_skill_file(self, call_id: str, args: dict) -> dict:
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            sk = skills_svc.find(db, user, str(args.get("name", "")))
            if not sk:
                raise AgentError(f"Skill «{args.get('name')}» no instalada")
            path = str(args.get("path", "")).strip().lstrip("./")
            text = (sk.files or {}).get(path)
            if text is None:
                raise AgentError(f"La skill no tiene «{path}». Archivos: " + ", ".join(sorted(sk.files or {})))
        return {"ok": True, "summary": f"{sk.name}/{path}", "content": text[:TOOL_RESULT_CHARS]}

    def tool_find_skills(self, call_id: str, args: dict) -> dict:
        q = str(args.get("query", "")).strip()[:200]
        words = set(re.findall(r"\w{3,}", q.lower()))

        def score(name: str, desc: str) -> int:
            hay = f"{name} {name} {desc}".lower()
            return sum(hay.count(w) for w in words)

        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            installed = [(score(x.name, x.description), x.name, x.description) for x in skills_svc.visible(db, user)]
            try:
                cat = [c for c in skills_svc.catalog(db) if "error" not in c]
            except Exception:  # noqa: BLE001
                cat = []
            web_hits: list[dict] = []
            if self.flags["web"] and q:
                try:
                    web_hits = [r for r in web.search(db, f"SKILL.md {q} skill", "code", None, 8)
                                if "github.com" in r["url"]][:5]
                except web.WebError:
                    pass
        have = {n for _, n, _ in installed}
        inst = sorted([x for x in installed if x[0] > 0], reverse=True)[:5]
        catm = sorted([(score(c["name"], c["description"]), c) for c in cat if c["name"] not in have],
                      key=lambda x: -x[0])
        catm = [c for s_, c in catm if s_ > 0][:6]
        lines = []
        if inst:
            lines.append("Installed (use_skill):\n" + "\n".join(f"- {n}: {d[:200]}" for _, n, d in inst))
        if catm:
            lines.append("Catalog (install_skill with the source):\n" + "\n".join(
                f"- {c['name']} — source: {c['source']} — {c['description'][:220]}" for c in catm))
        if web_hits:
            lines.append("On GitHub (check that the link contains a SKILL.md; install_skill accepts the URL):\n"
                         + "\n".join(f"- {r['title'][:80]} — {r['url']} — {r['snippet'][:160]}" for r in web_hits))
        if not lines:
            lines.append("No skills found for this task. Continue without one.")
        return {"ok": True, "summary": f"{len(inst)} instaladas, {len(catm)} en catálogo, {len(web_hits)} en GitHub",
                "skills_found": [{"name": c["name"], "source": c["source"], "description": c["description"][:200]} for c in catm],
                "content": "\n\n".join(lines)}

    def tool_install_skill(self, call_id: str, args: dict) -> dict:
        src = str(args.get("source", "")).strip()
        if not self.flags["auto_install"]:
            return {"ok": False, "summary": "Instalar skills requiere tu confirmación",
                    "remediation": f"Instálala tú en Skills → Instalar desde enlace: {src}",
                    "content": "The administrator requires the user to install skills manually (Skills page). Tell the "
                               f"user the source: {src}"}
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            try:
                sk = skills_svc.install(db, user, src, installed_by="assistant")
                db.commit()
            except (skills_svc.SkillError, web.WebError) as e:
                raise AgentError(f"No se pudo instalar: {e}")
            info = {"id": str(sk.id), "name": sk.name, "source_url": sk.source_url, "license": sk.license,
                    "files": len(sk.files or {})}
        if info["name"] not in self.skill_names:
            self.skill_names.append(info["name"])
        return {"ok": True, "summary": f"Skill «{info['name']}» instalada ({info['files']} archivos)", "skill": info,
                "content": f"Installed skill '{info['name']}'. Load it now with use_skill."}

    # -------------------------------------------------------------- sub-agents

    def tool_delegate_task(self, call_id: str, args: dict) -> dict:
        name = str(args.get("agent") or "general")
        prof = self.agents.get(name)
        if not prof:
            raise AgentError(f"No existe el agente «{name}». Disponibles: {', '.join(self.agents)}")
        task = str(args.get("task", "")).strip()[:8000]
        if not task:
            raise AgentError("Tarea vacía")
        tools = [t for t in prof["tools"] if t in SUB_AGENT_TOOLS and self._tool_allowed(t)]
        if self.workspace is not None and any(t in WS_TOOLS for t in tools):
            tools = list(dict.fromkeys(tools + ["ws_list", "ws_read"]))  # whoever works in the workspace can look at it
        es = self.t.lang != "en"
        sys_lines = [prof["instructions"], f"{'Fecha' if es else 'Date'}: {_now_str()}.",
                     "Trabajas para el asistente principal de Spark AI Hub, no hablas con el usuario. Devuelve solo el "
                     "resultado final (informe completo y conciso, en el idioma de la tarea)." if es else
                     "You work for the main Spark AI Hub assistant, not the user. Return only the final result."]
        writes = self.workspace is not None and any(t in WS_WRITE_TOOLS or t == "ws_run" for t in tools)
        scope: list[str] = []
        if writes:
            from . import workspace as ws_svc

            try:
                scope = sorted({ws_svc.clean_rel(str(x)) for x in (args.get("write_paths") or []) if str(x).strip()}) or [""]
            except ws_svc.WorkspaceError as e:
                raise AgentError(f"write_paths no válido: {e.message}")
            with self.ws_claims_lock:
                clash = next(((n, sc) for n, sc in self.ws_claims.values() if scope_overlaps(scope, sc)), None)
                if clash:
                    raise AgentError(f"El agente «{clash[0]}» ya está escribiendo en {', '.join(clash[1]) or 'todo el workspace'}. "
                                     "Da a cada agente en paralelo archivos o carpetas distintos con write_paths.")
                self.ws_claims[call_id] = (name, scope)
            where = ", ".join(scope) if scope != [""] else ("todo el workspace" if es else "the whole workspace")
            sys_lines.append(
                f"Trabajas en el workspace del usuario (carpeta /work, rutas relativas). Puedes crear y modificar SOLO: "
                f"{where}; lee lo que necesites del resto. Escribe archivos largos por partes (ws_write y luego "
                "ws_append), ejecuta y prueba con ws_run, corrige los errores y no borres archivos que no sean tuyos. "
                "Otros agentes pueden estar trabajando a la vez en otras partes. Termina con la lista de archivos que "
                "has creado o cambiado y cómo probarlos." if es else
                f"You work in the user's workspace (/work, relative paths). You may create and modify ONLY: {where}; "
                "read anything else you need. Write long files in parts (ws_write then ws_append), run and test with "
                "ws_run, fix errors, never delete files that are not yours. Other agents may work at the same time on "
                "other parts. End with the list of files you created or changed and how to test them.")
        if args.get("instructions"):
            sys_lines.insert(1, str(args["instructions"])[:6000])
        preload = ""
        if prof.get("skills"):
            with SessionLocal() as db:
                user = db.get(User, self.t.user_id)
                for sn in prof["skills"][:3]:
                    sk = skills_svc.find(db, user, sn)
                    if sk:
                        preload += f"\n\n# Skill: {sk.name}\n{sk.content[:12000]}"
        user_msg = task + (f"\n\nContexto:\n{str(args['context'])[:30000]}" if args.get("context") else "")
        messages = [{"role": "system", "content": "\n\n".join(sys_lines) + preload}, {"role": "user", "content": user_msg}]
        specs = [t for t in self._all_specs() if t["function"]["name"] in tools]
        if "search_knowledge" in tools:
            messages[0]["content"] += ("\n\nAl buscar documentos, empieza con UNA consulta precisa basada en los términos "
                                       "del usuario. Haz una segunda búsqueda solo si es imprescindible. En las fuentes "
                                       "copia literalmente la ruta source_path COMPLETA de cada archivo, incluido su "
                                       "nombre y extensión; no cites únicamente la carpeta raíz.")
        steps: list[dict] = []
        sources: list[dict] = []
        changed: set[str] = set()
        text = ""
        document_searches = 0
        must_search_docs = "search_knowledge" in tools and bool(re.search(
            r"\b(document\w*|archivo\w*|colecci\w*|fuente\w*|acuerdo\w*|contrato\w*|convenio\w*|memorando\w*)\b",
            task, re.I))
        max_steps, max_out = (SUB_WS_MAX_STEPS, 8192) if writes else (SUB_MAX_STEPS, 2048)
        try:
            inst = scheduler.ensure_loaded(self.t.model_id, wait_s=1800)
        except BaseException:
            with self.ws_claims_lock:
                self.ws_claims.pop(call_id, None)
            raise
        scheduler.begin_request(inst.id)
        try:
            for step in range(max_steps):
                if self.stop():
                    raise AgentError("Cancelado por el usuario")
                self._fit(messages, max_out)
                last = step == max_steps - 1
                active_specs = specs
                if document_searches >= 2:
                    active_specs = [s for s in specs if s["function"]["name"] != "search_knowledge"]
                text, calls, _ = self._llm_step(messages, active_specs if active_specs and not last else None, max_out,
                                                thinking=False)
                if not calls and step == 0 and "web_search" in tools and (name == "investigador" or args.get("_must_search")):
                    # a researcher must look things up instead of answering from memory
                    messages += [{"role": "assistant", "content": text or ""},
                                 {"role": "user", "content": "Antes de responder busca con web_search y lee las mejores "
                                                             "fuentes con fetch_url; cita cada dato con su URL."}]
                    continue
                if not calls and step == 0 and must_search_docs:
                    # A document agent must inspect the user's index instead of inventing that files do or do not exist.
                    messages += [{"role": "assistant", "content": text or ""},
                                 {"role": "user", "content": "Antes de responder debes ejecutar UNA consulta precisa a "
                                                             "search_knowledge basada en los términos del usuario. Haz "
                                                             "una segunda solo si los resultados son insuficientes. Basa "
                                                             "la conclusión únicamente en los resultados y no afirmes "
                                                             "que no existe documentación sin buscarla. En cada fuente "
                                                             "copia la ruta source_path COMPLETA exacta, con nombre de "
                                                             "archivo y extensión, además de la página o sección."}]
                    continue
                if not calls:
                    break
                assistant_msg, results = self._calls_to_msg(text, calls)
                messages.append(assistant_msg)
                for cid, tname, targs in results:
                    if tname not in tools:
                        res = {"ok": False, "summary": f"{tname} no disponible", "content": f"Tool {tname} not available"}
                    elif tname == "search_knowledge" and document_searches >= 2:
                        res = {"ok": True, "summary": "Límite de dos búsquedas documentales alcanzado",
                               "content": "Ya dispones de hasta doce fragmentos. Sintetiza la respuesta con esos "
                                          "resultados y sus rutas exactas; no hagas más búsquedas."}
                    elif tname in WEB_SAVE_TOOLS and (targs.get("save_to") or targs.get("save_dir")) and not (
                            writes and self._sub_may_write({"path": targs.get("save_to") or targs.get("save_dir")}, scope)):
                        res = {"ok": False, "summary": "No puede guardar ahí",
                               "content": "This agent may only save inside its write zone"
                                          + (f": {', '.join(scope)}" if scope else " (it has none: do not use save_to/save_dir)")
                                          + ". Return the content in your report instead, or save inside your zone."}
                    elif tname in WS_WRITE_TOOLS and not self._sub_may_write(
                            {**targs, "path": targs.get("path") or (self._dl_default_path(targs) if tname == "download_file" else "")}, scope):
                        res = {"ok": False, "summary": "Fuera de su zona de escritura",
                               "content": f"You may only write inside: {', '.join(scope)}. Put your work there, or report "
                                          "what should change elsewhere so the main assistant does it."}
                    else:
                        what = targs.get("url") if tname == "fetch_url" else targs.get("query")
                        self.emit({"type": "tool_progress", "id": call_id, "message": f"{name}: {TOOL_LABELS.get(tname, tname)}"
                                   + (f" «{str(what)[:70]}»" if what else "")})
                        res = self._exec(cid, tname, targs)
                        if tname == "search_knowledge":
                            document_searches += 1
                    steps.append({"name": tname, "label": TOOL_LABELS.get(tname, tname), "ok": res.get("ok", True),
                                  "summary": str(res.get("summary", ""))[:200]})
                    sources += [x for x in res.get("sources") or [] if x.get("url") or x.get("source_path")]
                    changed.update((res.get("workspace") or {}).get("changed") or [])
                    messages.append({"role": "tool", "tool_call_id": cid,
                                     "content": str(res.get("content") or res.get("summary"))[:TOOL_RESULT_CHARS]})
            if not re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip() and not self.stop():
                # it ran out of steps (or only thought): ask once for the final report instead of returning nothing
                messages.append({"role": "user", "content": "Escribe ahora tu informe final, sin llamar a más herramientas: qué "
                                 "has hecho, qué archivos has creado o cambiado, qué falló o queda pendiente y los resultados."})
                try:
                    self._fit(messages, max_out)
                    text, _calls, _ = self._llm_step(messages, None, max_out, thinking=False)
                except Exception:  # noqa: BLE001
                    text = ""
        finally:
            scheduler.end_request(inst.id)
            with self.ws_claims_lock:
                self.ws_claims.pop(call_id, None)
        text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
        if not text:
            last = "; ".join(f"{x['label']}: {x['summary'][:80]}" for x in steps[-5:]) or "ningún paso"
            text = (f"(el agente no devolvió informe) Pasos: {len(steps)}. Archivos cambiados: "
                    f"{', '.join(sorted(changed)[:15]) or 'ninguno'}. Últimos pasos: {last}. Comprueba el resultado tú mismo.")
        unverified = not steps and bool(re.search(r"(/work/|\.(tex|py|csv|json|md|pdf|bib|xlsx|docx)\b|workspace|archivos?|ficheros?|capítulos?)",
                                                  task, re.I))
        if unverified:
            text = ("⚠ SIN VERIFICAR: este agente no abrió ningún archivo ni fuente (no tenía herramientas o no las usó). Lo que "
                    "sigue es una opinión del modelo: sus citas, líneas y cifras NO son evidencia hasta que las compruebes tú "
                    "(grep/ws_read). No apliques sus correcciones sin verificarlas.\n\n") + text
        seen, uniq = set(), []
        for x in sources:
            key = x.get("url") or x.get("source_path")
            if key not in seen:
                seen.add(key)
                uniq.append(x)
        exact_docs = [x for x in uniq if x.get("source_path")]
        if exact_docs:
            text = _remove_unverified_unc_paths(text, {x["source_path"] for x in exact_docs})
        exact_catalog = "\n".join(
            f"- [{x.get('filename') or 'documento'}" + (f", p. {x['page']}" if x.get("page") else "")
            + f"] `{x['source_path']}`" for x in exact_docs)
        out = {"ok": True, "summary": f"{name}: {len(steps)} pasos" + (" · SIN VERIFICAR" if unverified else "") + (f", {len(uniq)} fuentes" if uniq else "")
               + (f", {len(changed)} archivo(s)" if changed else ""),
               "agent": {"name": name, "task": task[:500], "steps": steps, "report": text[:20000]},
               "sources": uniq[:15], "content": f"Report from agent '{name}':\n{text[:16000]}"}
        if exact_catalog:
            out["content"] += ("\n\nVerified document sources from the index. Copy these paths verbatim; do not "
                               "infer folders or alter them:\n" + exact_catalog)
        if changed:
            out["workspace"] = {"changed": sorted(changed)}
            out["content"] += "\n\nFiles changed by this agent: " + ", ".join(sorted(changed)[:100])
        return out

    def _sub_may_write(self, targs: dict, scope: list[str]) -> bool:
        from . import workspace as ws_svc

        try:
            return in_scope(ws_svc.clean_rel(str(targs.get("path") or "")), scope)
        except ws_svc.WorkspaceError:
            return False

    def tool_run_code(self, call_id: str, args: dict) -> dict:
        code = str(args.get("code") or "")
        if not code.strip():
            raise AgentError("Falta el código")
        lang = str(args.get("language") or "python")
        inputs: list[UserFile] = []
        for ref in (args.get("files") or [])[:20]:
            ref = str(ref).strip()
            f = next((x for x in self.files.values() if x.filename == ref or str(x.id) == ref), None)
            if f is None:
                try:
                    f = self._file(ref)
                except AgentError:
                    raise AgentError(f"No encuentro el archivo «{ref}». Adjuntos: "
                                     + (", ".join(x.filename for x in self.files.values()) or "ninguno"))
            inputs.append(f)
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            skills = []
            for n in (args.get("skills") or [])[:5]:
                sk = skills_svc.find(db, user, str(n))
                if not sk:
                    raise AgentError(f"No hay ninguna skill «{n}» instalada")
                skills.append(sk)
            self.emit({"type": "tool_progress", "id": call_id, "message": "Ejecutando en el entorno aislado…"})
            try:
                res = sandbox.run(db, user, code=code[:200_000], language=lang, inputs=inputs, skills=skills,
                                  project_id=self.project_id, conversation_id=self.t.conversation_id)
            except sandbox.SandboxError as e:
                raise AgentError(e.message, e.remediation)
        ok = res["exit_code"] == 0 and not res["timed_out"]
        state = ("se pasó del tiempo límite" if res["timed_out"] else "sin memoria" if res.get("oom_killed")
                 else "terminó bien" if ok else f"terminó con error {res['exit_code']}")
        files = res["files"]
        summary = f"Código {state} en {res['seconds']:.1f} s" + (f" · {len(files)} archivo(s)" if files else "")
        listing = "\n".join(f"- {f['filename']} ({f['kind']}, {f['size_bytes']} bytes)" for f in files)
        content = (f"exit_code={res['exit_code']} timed_out={res['timed_out']} oom={res.get('oom_killed')}\n"
                   f"--- stdout ---\n{res['stdout'][-8000:]}\n--- stderr ---\n{res['stderr'][-4000:]}\n"
                   + (f"--- files given to the user (shown with download links; do not write links) ---\n{listing}"
                      if files else "--- no new files in /work ---"))
        if res.get("kept") is False:
            content += "\n--- /work is over 2 GB: its files were NOT kept for the next run; download or shrink them ---"
        return {"ok": ok, "summary": summary,
                "code": {"language": lang, "code": code[:20000], "stdout": res["stdout"][-6000:],
                         "stderr": res["stderr"][-3000:], "exit_code": res["exit_code"], "timed_out": res["timed_out"],
                         "seconds": res["seconds"], "files": files},
                "content": content}

    def _canvas_result(self, d, created: bool) -> dict:
        info = {"id": str(d.id), "title": d.title, "version": d.version, "kind": d.kind, "created": created}
        self.canvas_id = d.id
        return {"ok": True, "summary": f"«{d.title}» v{d.version}", "canvas": info,
                "content": f"Canvas «{d.title}» saved as version {d.version} and shown to the user next to the chat. "
                           "Do not repeat its content in the chat; give a short summary."}

    def tool_canvas_write(self, call_id: str, args: dict) -> dict:
        content = str(args.get("content") or "")
        if not content.strip():
            raise AgentError("El lienzo está vacío")
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            try:
                d = canvas_svc.create(db, user, title=str(args.get("title") or "Documento"), content=content,
                                      kind=str(args.get("kind") or "document"), language=args.get("language"),
                                      conversation_id=self.t.conversation_id)
            except canvas_svc.CanvasError as e:
                raise AgentError(e.message)
            db.commit()
            return self._canvas_result(d, True)

    def tool_canvas_edit(self, call_id: str, args: dict) -> dict:
        if not self.canvas_id:
            raise AgentError("No hay lienzo en esta conversación: créalo con canvas_write")
        with SessionLocal() as db:
            d = db.get(CanvasDoc, self.canvas_id)
            if not d or d.owner_id != self.t.user_id:
                raise AgentError("Lienzo no encontrado")
            try:
                new = str(args["content"]) if args.get("content") else canvas_svc.apply_edits(d, list(args.get("edits") or []))
                canvas_svc.update(db, d, content=new, author="assistant", title=args.get("title"))
            except canvas_svc.CanvasError as e:
                raise AgentError(e.message)
            db.commit()
            return self._canvas_result(d, False)

    def _complete(self, messages: list[dict], max_tokens: int) -> str:
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        if self.model is not None and self.model.backend == "vllm":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        inst = scheduler.ensure_loaded(self.t.model_id, wait_s=1800)
        scheduler.begin_request(inst.id)
        try:
            r = llm.chat_once(self.model, body)
        finally:
            scheduler.end_request(inst.id)
        text = (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def tool_deep_research(self, call_id: str, args: dict) -> dict:
        question = str(args.get("question") or "").strip()[:6000]
        if not question:
            raise AgentError("Falta la pregunta")
        if "investigador" not in self.agents:
            raise AgentError("La investigación a fondo necesita la búsqueda en internet y los agentes activados.")
        n = {"quick": 3, "standard": 5, "deep": 7}.get(str(args.get("depth") or "standard"), 5)
        self.emit({"type": "tool_progress", "id": call_id, "progress": 0.03, "message": "Planificando la investigación…"})
        raw = self._complete([{"role": "system", "content": RESEARCH_PLAN_PROMPT.format(n=n)},
                              {"role": "user", "content": question}], 1500)
        try:
            plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            subs = [str(x.get("q") if isinstance(x, dict) else x).strip() for x in plan.get("subquestions", [])][:n]
            title = str(plan.get("title") or question[:80])
        except (ValueError, AttributeError):
            subs, title = [question], question[:80]
        subs = [x for x in subs if x] or [question]
        self.emit({"type": "tool_progress", "id": call_id, "progress": 0.08,
                   "message": f"{len(subs)} líneas de investigación en paralelo"})
        jobs = [{"agent": "investigador", "task": f"{q}\n\nForma parte de una investigación más amplia sobre: {question[:1500]}"}
                for q in subs]
        if args.get("use_documents") and "documentalista" in self.agents:
            jobs.append({"agent": "documentalista", "task": f"Busca en los documentos del usuario todo lo relevante para: {question[:2000]}"})
        results: list[dict] = [None] * len(jobs)  # type: ignore[list-item]
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, self.max_parallel)) as ex:
            futs = {ex.submit(self._exec, call_id, "delegate_task", j): i for i, j in enumerate(jobs)}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
                done += 1
                self.emit({"type": "tool_progress", "id": call_id, "progress": 0.1 + 0.7 * done / len(jobs),
                           "message": f"{done}/{len(jobs)} investigaciones terminadas"})
        sources: list[dict] = []
        blocks = []
        for j, r in zip(jobs, results):
            local = []
            for src in r.get("sources") or []:
                if src["url"] not in [x["url"] for x in sources]:
                    sources.append(src)
                local.append(next(i for i, x in enumerate(sources, 1) if x["url"] == src["url"]))
            report = (r.get("agent") or {}).get("report") or r.get("summary") or ""
            blocks.append(f"### {j['task'].splitlines()[0]}\n(sources: {', '.join(f'[{i}]' for i in local) or 'none'})\n{report[:9000]}")
        if not any((r.get("agent") or {}).get("report") for r in results):
            raise AgentError("Los agentes no pudieron investigar (¿búsqueda web disponible?)")
        numbered = "\n".join(f"[{i}] {x.get('title') or x['url']} — {x['url']}" for i, x in enumerate(sources[:60], 1))
        self.emit({"type": "tool_progress", "id": call_id, "progress": 0.85, "message": "Redactando el informe…"})
        words = {3: 1200, 5: 2000, 7: 3000}.get(n, 2000)
        report = self._complete([
            {"role": "system", "content": RESEARCH_REPORT_PROMPT.format(words=words)},
            {"role": "user", "content": f"Question: {question}\nSuggested title: {title}\n\nNumbered sources:\n{numbered}"
                                        f"\n\nFindings by sub-question:\n\n" + "\n\n".join(blocks)}], min(12000, words * 3))
        if not report:
            raise AgentError("No se pudo redactar el informe")
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            d = canvas_svc.create(db, user, title=title, content=report, conversation_id=self.t.conversation_id)
            db.commit()
            res = self._canvas_result(d, True)
        steps = [{"name": "delegate_task", "label": f"{j['agent']}: {j['task'].splitlines()[0][:120]}",
                  "ok": r.get("ok", True), "summary": r.get("summary", "")} for j, r in zip(jobs, results)]
        return {**res, "summary": f"{len(jobs)} agentes · {len(sources)} fuentes · informe «{title}»",
                "research": {"title": title, "subquestions": subs, "steps": steps}, "sources": sources[:40],
                "content": f"Deep research finished. The full report «{title}» ({len(report.split())} words, "
                           f"{len(sources)} sources) is in the canvas next to the chat. Reply with a short summary of the "
                           f"key findings (5–8 bullets) and invite the user to read/export the canvas. Report:\n{report[:12000]}"}

    def tool_schedule_task(self, call_id: str, args: dict) -> dict:
        from ..models import ScheduledTask
        from . import tasks as tasks_svc

        sched = {k: args[k] for k in ("freq", "time", "weekdays", "weekday", "day") if args.get(k) is not None}
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            tz = (user.preferences or {}).get("timezone") or "Europe/Madrid"
            try:
                sched = tasks_svc.validate(sched, tz)
                nxt = tasks_svc.next_run(sched, tz)
            except tasks_svc.TaskError as e:
                raise AgentError(e.message)
            t = ScheduledTask(owner_id=user.id, name=str(args.get("name") or "Tarea")[:120], prompt=str(args.get("prompt"))[:20000],
                              schedule=sched, timezone=tz, model_alias=self.model.alias if self.model else None,
                              options={"web": self.flags["web"], "email": bool(args.get("email"))},
                              project_id=self.project_id, next_run_at=nxt)
            db.add(t)
            db.commit()
            when = tasks_svc.describe(sched, self.t.lang)
            local = nxt.astimezone(tasks_svc._tz(tz)).strftime("%d/%m/%Y %H:%M")
            return {"ok": True, "summary": f"«{t.name}» · {when}", "task": {"id": str(t.id), "name": t.name, "when": when, "next": local},
                    "content": f"Task created: «{t.name}», {when}; next run {local} ({tz}). The user can edit or delete it "
                               "on the Tasks page."}

    def _ws_call(self, fn):
        from . import workspace as ws_svc

        if self.workspace is None:
            raise AgentError("No hay workspace en esta conversación")
        from ..models import Workspace

        with SessionLocal() as db:
            ws = db.get(Workspace, self.workspace.id)
            if ws is None:
                raise AgentError("Este workspace se ha borrado")
            try:
                return fn(db, ws, ws_svc)
            except ws_svc.WorkspaceError as e:
                raise AgentError(e.message)
            except sandbox.SandboxError as e:
                raise AgentError(e.message, e.remediation)

    def tool_ws_list(self, call_id: str, args: dict) -> dict:
        def go(db, ws, svc):
            pre = svc.clean_rel(str(args.get("path") or ""))
            items = [e for e in svc.tree(ws) if not pre or e["path"] == pre or e["path"].startswith(pre + "/")]
            text = "\n".join(f"{e['path']}{'/' if e['type'] == 'dir' else ''}" + (f"  {e['size']} B" if e["type"] == "file" else "")
                             for e in items[:400])
            return {"ok": True, "summary": f"{len(items)} entradas", "content": text or "(vacío)"}
        return self._ws_call(go)

    def tool_ws_read(self, call_id: str, args: dict) -> dict:
        def go(db, ws, svc):
            r = svc.read(ws, str(args.get("path") or ""))
            if not r["text"]:
                return {"ok": True, "summary": f"{r['path']}: binario ({r['size']} B)",
                        "content": f"{r['path']} is a binary file ({r['mime']}, {r['size']} bytes); inspect it with ws_run."}
            lines = r["content"].splitlines()
            a = max(1, int(args.get("start_line") or 1))
            b = min(len(lines), int(args.get("end_line") or (a + 799)))
            body = "\n".join(f"{i:5d}| {ln}" for i, ln in enumerate(lines[a - 1:b], a))
            more = f"\n[... {len(lines) - b} more lines: read with start_line={b + 1}]" if b < len(lines) else ""
            return {"ok": True, "summary": f"{r['path']} ({len(lines)} líneas)", "content": body[:TOOL_RESULT_CHARS * 2] + more}
        return self._ws_call(go)

    def tool_ws_write(self, call_id: str, args: dict) -> dict:
        def go(db, ws, svc):
            content = str(args.get("content") or "")
            r = svc.write(db, ws, str(args.get("path") or ""), content.encode())
            db.commit()
            return {"ok": True, "summary": f"{r['path']} ({len(content.splitlines())} líneas)",
                    "workspace": {"changed": [r["path"]]}, "content": f"Saved {r['path']} ({r['size']} bytes)."}
        return self._ws_call(go)

    def tool_ws_append(self, call_id: str, args: dict) -> dict:
        def go(db, ws, svc):
            path = str(args.get("path") or "")
            add = str(args.get("content") or "")
            try:
                cur = svc.read(ws, path)
                if not cur["text"] or cur["truncated"]:
                    raise AgentError("Solo se puede añadir a archivos de texto de menos de 2 MB")
                old = cur["content"]
            except svc.WorkspaceError as e:
                if e.status != 404:
                    raise
                old = ""
            sep = "" if not old or old.endswith("\n") else "\n"
            r = svc.write(db, ws, path, (old + sep + add).encode())
            db.commit()
            total = (old + sep + add).count("\n") + 1
            return {"ok": True, "summary": f"{r['path']} (+{add.count(chr(10)) + 1} líneas, {total} en total)",
                    "workspace": {"changed": [r["path"]]}, "content": f"Appended to {r['path']} (now {total} lines)."}
        return self._ws_call(go)

    def tool_ws_edit(self, call_id: str, args: dict) -> dict:
        def go(db, ws, svc):
            r = svc.read(ws, str(args.get("path") or ""))
            if not r["text"] or r["truncated"]:
                raise AgentError("Solo se pueden editar archivos de texto de menos de 2 MB")
            from ..models import CanvasDoc

            try:
                new = canvas_svc.apply_edits(CanvasDoc(content=r["content"]), list(args.get("edits") or []))
            except canvas_svc.CanvasError as e:
                raise AgentError(e.message)
            svc.write(db, ws, r["path"], new.encode())
            db.commit()
            return {"ok": True, "summary": f"{r['path']} editado", "workspace": {"changed": [r["path"]]},
                    "content": f"Edited {r['path']}."}
        return self._ws_call(go)

    def tool_ws_run(self, call_id: str, args: dict) -> dict:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            raise AgentError("Falta el comando")
        self.emit({"type": "tool_progress", "id": call_id, "message": cmd[:120]})

        def go(db, ws, svc):
            t = args.get("timeout_s")
            for attempt in range(60):  # agents in parallel share the user's run slots: wait for one (≤ 5 min)
                try:
                    res = svc.run(db, ws, cmd, timeout_s=int(t) if isinstance(t, int) else None)
                    break
                except sandbox.SlotBusy:
                    if attempt == 59 or self.stop():
                        raise
                    if attempt == 0:
                        self.emit({"type": "tool_progress", "id": call_id, "message": "Esperando turno para ejecutar…"})
                    time.sleep(5)
            ok = res["exit_code"] == 0 and not res["timed_out"]
            state = "tiempo agotado" if res["timed_out"] else "sin memoria" if res.get("oom_killed") else \
                ("ok" if ok else f"error {res['exit_code']}")
            ch = res["changed"]
            return {"ok": ok, "summary": f"{state} · {res['seconds']:.1f} s" + (f" · {len(ch)} archivo(s) cambiados" if ch else ""),
                    "terminal": {"command": cmd[:4000], "stdout": res["stdout"][-6000:], "stderr": res["stderr"][-3000:],
                                 "exit_code": res["exit_code"], "timed_out": res["timed_out"], "seconds": res["seconds"]},
                    "workspace": {"changed": ch, "removed": res["removed"]},
                    "content": f"exit_code={res['exit_code']} timed_out={res['timed_out']}\n--- stdout ---\n{res['stdout'][-8000:]}"
                               f"\n--- stderr ---\n{res['stderr'][-4000:]}\n--- changed files ---\n" + ("\n".join(ch[:100]) or "(none)")}
        return self._ws_call(go)

    # -------------------------------------------------------------- tool plumbing

    def _tool_allowed(self, name: str) -> bool:
        if name in ("search_knowledge", "read_document", "describe_data", "query_data") and not self.flags.get("docs", True):
            return False  # the director left the person's documents out of this answer
        if name in ("db_schema", "db_query"):
            return bool(self.db_names)
        if name in ("web_search", "fetch_url", "fetch_urls", "http_request", "crawl_site", "search_images", "download_file",
                    "extract_images", "search_papers", "doi_to_bibtex"):
            return self.flags["web"]
        if name == "view_image":
            return self._vision_ok() and (self.workspace is not None or self.flags["code"])
        if name in ("use_skill", "read_skill_file", "find_skills", "install_skill"):
            return self.flags["skills"]
        if name == "delegate_task":
            return self.flags["agents"]
        if name == "run_code":
            return self.flags["code"] and self.workspace is None
        if name in ("describe_data", "query_data", "read_document") and self.workspace is not None:
            return False  # in a workspace the files are read and processed with ws_read / ws_run
        if name.startswith("ws_"):
            return self.workspace is not None and (name != "ws_run" or self.flags["code"])
        if name in ("canvas_write", "canvas_edit") and self.workspace is not None:
            return False
        if name == "deep_research":
            return self.flags["web"] and self.flags["agents"]
        if name == "update_plan":
            return not self.small or self.workspace is not None
        return True

    def _all_specs(self) -> list[dict]:
        specs = (TOOLS + WEB_TOOLS + SKILL_TOOLS + CODE_TOOLS + CANVAS_TOOLS + RESEARCH_TOOLS + TASK_TOOLS + WORKSPACE_TOOLS
                 + PLAN_TOOLS)
        if self.agents:
            specs = specs + [_delegate_spec(self.agents)]
        if self.db_names:
            specs = specs + DB_TOOLS
        return specs

    def tool_specs(self) -> list[dict]:
        small = self.small and self.workspace is None
        return [t for t in self._all_specs() if self._tool_allowed(t["function"]["name"])
                and (not small or t["function"]["name"] in CORE_TOOLS)]

    def _exec(self, call_id: str, name: str, args: dict) -> dict:
        if "_invalid_arguments" in args:
            n = args["_invalid_arguments"]
            return {"ok": False, "summary": "Llamada cortada: el contenido era demasiado largo",
                    "content": f"Your {name} call was cut off ({n} characters) and its arguments are not valid JSON, so "
                               "nothing was done. Write long files in several smaller parts: create the file with the "
                               "first part (ws_write) and add the rest in pieces of ~150 lines with ws_append."}
        fn = getattr(self, f"tool_{name}", None)
        if fn is None or not self._tool_allowed(name):
            return {"ok": False, "summary": f"Herramienta no disponible: {name}", "content": f"Unknown tool {name}"}
        try:
            return fn(call_id, args)
        except AgentError as e:
            return {"ok": False, "summary": e.message, "content": f"Error: {e.message}", "remediation": e.remediation}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "summary": f"{type(e).__name__}: {e}"[:300], "content": f"Error: {e}"}

    def run_tool(self, call_id: str, name: str, args: dict) -> dict:
        started = time.time()
        orig_args = args
        args = public_args(name, args)  # API keys in headers never reach the chat history
        self.emit({"type": "tool_start", "id": call_id, "name": name, "label": TOOL_LABELS.get(name, name),
                   "started": round(started, 3),
                   "args": {k: v for k, v in args.items() if k not in ("sql", "context")} | ({"sql": args["sql"]} if "sql" in args else {})})
        res = self._exec(call_id, name, orig_args)
        public = {k: v for k, v in res.items() if k != "content"}
        public["seconds"] = round(time.time() - started, 2)
        self.emit({"type": "tool_result", "id": call_id, "name": name, **public})
        with self.lock:
            self.parts.append({"type": "tool", "name": name, "args": {k: v for k, v in args.items() if k != "context"}, **public})
        return res

    @staticmethod
    def _calls_to_msg(text: str, calls: dict[int, dict]) -> tuple[dict, list]:
        assistant_msg = {"role": "assistant", "content": text or None, "tool_calls": []}
        results = []
        for idx in sorted(calls):
            c = calls[idx]
            cid = c["id"] or f"call_{uuid.uuid4().hex[:12]}"
            try:
                args = json.loads(c["args"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                # usually the call was cut by max_tokens while writing a long file: tell the model instead of
                # running the tool with empty arguments
                args = {"_invalid_arguments": len(c["args"] or "")}
            assistant_msg["tool_calls"].append({"id": cid, "type": "function",
                                                "function": {"name": c["name"], "arguments": json.dumps(
                                                    {} if "_invalid_arguments" in args else args, ensure_ascii=False)}})
            results.append((cid, c["name"], args))
        return assistant_msg, results

    def _fit(self, messages: list[dict], max_tokens: int) -> int:
        """Keep the request inside the context window: shrink old tool results first, then the oldest turns.
        Returns the max_tokens that still fits."""
        limit = self.ctx - 256
        total = est_tokens(messages, self.ratio)
        if total + max_tokens > limit:
            tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            for i in tool_idx[:-2] + tool_idx[-2:]:
                if total + max_tokens <= limit:
                    break
                c = messages[i]["content"]
                if isinstance(c, str) and len(c) > 600:
                    messages[i]["content"] = c[:500] + "\n[... resultado recortado para no exceder el contexto]"
                    total = est_tokens(messages, self.ratio)
            # Then drop the oldest history turns (never the system prompt or the current request).
            while total + max_tokens > limit and len(messages) > 3 and messages[1].get("role") != "tool" \
                    and not messages[1].get("tool_calls") and messages[1] is not self.current_user:
                messages.pop(1)
                self.dropped = getattr(self, "dropped", 0) + 1
                total = est_tokens(messages, self.ratio)
        return max(256, min(max_tokens, limit - total))

    def _steer_check(self) -> bool:
        """Stream cancel check of the main loop: stop requested, or the user sent an urgent message (read at most every 0.3 s).
        An urgent message sets `_steered`; the loop then keeps what was reasoned and reads the message at once."""
        if self.stop():
            return True
        now = time.monotonic()
        if now - self._steer_t < 0.3:
            return False
        self._steer_t = now
        try:
            due = turns_svc.steer_pending(self.t.conversation_id)
        except Exception:  # noqa: BLE001  (Redis hiccup: the message is read after the step instead)
            return False
        if due:
            self._steered = True
        return due

    def _llm_step(self, messages: list[dict], specs: list[dict] | None, max_tokens: int, thinking: bool | None = None,
                  on_delta=None, on_reasoning=None, usage: dict | None = None, interruptible: bool = False,
                  main: bool = False) -> tuple[str, dict, str | None]:
        body = {"messages": messages, **{k: v for k, v in self.t.params.items() if k not in ("aspect", "max_tokens")},
                "max_tokens": max_tokens, **({"tools": specs} if specs else {})}
        if thinking is not None and self.model.backend == "vllm":
            body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        payload = llm.sanitize_chat_payload(self.model, body)
        calls: dict[int, dict] = {}
        out: list[str] = []
        finish = None
        told = 0.0  # a long file written as a tool argument takes minutes: say how it goes (at most once per second)
        rguard, tguard = loopguard.LoopGuard(max_total=loopguard.REASONING_MAX_CHARS), loopguard.LoopGuard(min_chars=4000)
        looped = False  # (only the main loop reads self._loop_hit: sub-agents run in parallel threads)
        if main:
            self._loop_hit = None
        for chunk in llm.stream_json(self.model, payload, cancel_check=self._steer_check if interruptible else self.stop):
            if chunk.get("usage") and usage is not None:
                for k in ("prompt_tokens", "completion_tokens"):
                    usage[k] = usage.get(k, 0) + (chunk["usage"].get(k) or 0)
                usage["last_prompt"] = chunk["usage"].get("prompt_tokens") or 0
                usage["last_completion"] = chunk["usage"].get("completion_tokens") or 0
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                piece = d.get("content") or ""
                rpiece = d.get("reasoning_content") or d.get("reasoning") or ""
                if rpiece and on_reasoning:
                    on_reasoning(rpiece)
                if rpiece and rguard.feed(rpiece):
                    looped = True
                    if main:
                        self._loop_hit = ("reasoning", rguard.reason)
                if piece:
                    out.append(piece)
                    if on_delta:
                        on_delta(piece)
                    if tguard.feed(piece):
                        looped = True
                        if main:
                            self._loop_hit = ("text", tguard.reason)
                for tc in d.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["name"] += fn.get("name") or ""
                    c["args"] += fn.get("arguments") or ""
                    if time.monotonic() - told >= 1.0 and c["name"]:
                        told = time.monotonic()
                        self.emit({"type": "tool_writing", "name": c["name"], "label": TOOL_LABELS.get(c["name"], c["name"]),
                                   "chars": len(c["args"])})
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
            if looped:
                break  # looping: stop paying for it (closing the stream aborts the generation)
        return "".join(out), calls, finish

    # -------------------------------------------------------------- plan, inbox, mid-turn compaction

    def _update_plan(self, args: dict) -> dict:
        if "_invalid_arguments" in args:
            return {"ok": False, "content": "Your update_plan call was cut off; send the plan again (shorter items)."}
        items = normalize_plan(args.get("items"))
        if not items:
            return {"ok": False, "content": "The plan is empty: send items=[{content, status}, ...]."}
        with self.lock:
            self.plan = items
            if self.plan_part is None:
                self.plan_part = {"type": "plan", "items": items}
                self.parts.append(self.plan_part)
            else:
                self.plan_part["items"] = items
        self.emit({"type": "plan", "items": items})
        done = sum(1 for i in items if i["status"] == "completed")
        rest = plan_open(items)
        return {"ok": True, "content": f"Plan saved ({done}/{len(items)} completed)." + (
            f" Next: {rest[0]['content']}. Keep working; do not stop until every item is completed." if rest else
            " Everything is completed: give the final summary to the user.")}

    def _absorb_inbox(self, messages: list[dict], closing: bool = False, partial_reasoning: str = "") -> int:
        """Append the messages the user sent while the turn runs (see services/turns.py) as user messages.

        `closing`: the model is about to finish, so the inbox is closed atomically when it is empty (later messages get
        a turn of their own). Returns how many messages were injected."""
        cid = self.t.conversation_id
        try:
            ids = turns_svc.close_inbox(cid) if closing else turns_svc.take_inbox(cid)
        except Exception:  # noqa: BLE001  (Redis hiccup: the messages stay queued for the follow-up turn)
            return 0
        if not ids:
            return 0
        es = self.t.lang != "en"
        n = 0
        with SessionLocal() as db:
            for mid in ids:
                m = db.get(Message, mid)
                if not m or m.conversation_id != cid or m.role != "user":
                    continue
                files = []
                for a in m.attachments or []:
                    try:
                        f = db.get(UserFile, uuid.UUID(a["file_id"]))
                    except (KeyError, ValueError, TypeError):
                        continue
                    if f and f.owner_id == self.t.user_id:
                        db.expunge(f)
                        files.append(f)
                        if f.kind == "image":
                            self.refs.append({"source": "file", "id": str(f.id), "ref": f"img{len(self.refs) + 1}",
                                              "desc": f"subida por el usuario mientras trabajabas: {f.filename}"})
                att, warnings = self._attachment_parts(db, self.model, files, int(self.ctx * 0.15 * self.ratio)) if files \
                    else ([], [])
                for w in warnings:
                    self.emit({"type": "warning", "message": w})
                carried = ""
                if partial_reasoning.strip() and n == 0:  # the user interrupted a generation: hand its reasoning back
                    tail = partial_reasoning.strip()[-8000:]
                    carried = (("[Te escribieron mientras pensabas. Este es tu razonamiento hasta ese momento, interrumpido: no "
                                "lo repitas, continúa desde ahí teniendo en cuenta el mensaje.]\n<razonamiento>\n" if es else
                                "[The user wrote while you were thinking. This is your reasoning up to that point, interrupted: do "
                                "not repeat it, carry on from there taking the message into account.]\n<reasoning>\n")
                               + tail + ("\n</razonamiento>\n\n" if es else "\n</reasoning>\n\n"))
                text = carried + (("[Mensaje nuevo del usuario mientras trabajabas]: " if es else
                         "[New message from the user while you were working]: ") + (m.content or "")
                        + ("\n\n(Incorpóralo a la tarea: si la cambia o la amplía, actualiza el plan con update_plan y "
                           "sigue; no abandones lo que estabas haciendo salvo que te lo pida.)" if es else
                           "\n\n(Take it into account: if it changes or extends the task, update the plan with "
                           "update_plan and go on; do not drop the current work unless asked to.)"))
                msg = {"role": "user", "content": ([{"type": "text", "text": text}] + att) if att else text}
                messages.append(msg)
                self.protected.add(id(msg))
                self.emit({"type": "user_injected", "message_id": m.id, "content": m.content})
                with self.lock:
                    self.parts.append({"type": "user_injected", "message_id": m.id, "content": (m.content or "")[:4000]})
                n += 1
        self.injected += n
        return n

    def _compact_turn(self, messages: list[dict], out_tokens: int, force: bool = False) -> bool:
        """Mid-turn compaction: when the prompt nears the window, summarise the turn's older steps (the system prompt,
        the history, the user's requests, the plan and the latest steps stay verbatim). True when it compacted."""
        before = est_tokens(messages, self.ratio) + self.spec_tokens
        if not force and before + min(out_tokens, self.ctx // 4) <= self.ctx * COMPACT_AT:
            return False
        try:
            start = next(i for i, m in enumerate(messages) if m is self.current_user) + 1
        except StopIteration:
            return False
        if not force and est_tokens(messages[start:], self.ratio) < self.ctx * 0.25:
            return False  # the history, not this turn's work, fills the window: _fit trims it
        protected = {i for i, m in enumerate(messages) if id(m) in self.protected}
        split = compaction_split(messages, start, int(self.ctx * COMPACT_KEEP), self.ratio, protected)
        if not split:
            return False
        cid = f"call_{uuid.uuid4().hex[:12]}"
        started = time.time()
        self.emit({"type": "status", "status": "compacting"})
        self.emit({"type": "tool_start", "id": cid, "name": "compact_context", "label": TOOL_LABELS["compact_context"],
                   "started": round(started, 3)})
        old_msgs = [messages[i] for i in split["old"]]
        previous = self.task_summary if any(m is self.task_summary_msg for m in old_msgs) else None
        pieces = turn_transcript([m for m in old_msgs if m is not self.task_summary_msg])
        words = max(400, min(1500, int(self.ctx * 0.006)))
        chunk_tokens = max(1500, int(self.ctx * 0.4))
        summary = previous
        try:
            buf: list[str] = []
            used = 0
            for piece in pieces:
                t = est_tokens(piece, self.ratio)
                if buf and used + t > chunk_tokens:
                    summary = _summarize(self.model, summary, "\n\n".join(buf), words, TASK_SUMMARY_PROMPT, "Trabajo")
                    buf, used = [], 0
                buf.append(piece[: int(chunk_tokens * self.ratio)])
                used += min(t, chunk_tokens)
            if buf:
                summary = _summarize(self.model, summary, "\n\n".join(buf), words, TASK_SUMMARY_PROMPT, "Trabajo")
        except Exception as e:  # noqa: BLE001  (engine busy/down: a mechanical summary still frees the window)
            self.emit({"type": "warning", "message": f"No se pudo resumir con el modelo ({getattr(e, 'message', e)}); "
                                                     "se usa un resumen automático de los pasos."[:400]})
            summary = ((previous + "\n") if previous else "") + mechanical_summary(old_msgs)
        if not summary:
            summary = mechanical_summary(old_msgs)
        es = self.t.lang != "en"
        plan_txt = "\n".join(f"- [{i['status']}] {i['content']}" for i in self.plan)
        note = {"role": "user", "content": (
            ("[Contexto compactado automáticamente: los pasos anteriores de esta tarea ya no están en tu contexto. "
             "Resumen de lo hecho hasta ahora:]\n" if es else
             "[Context compacted automatically: the earlier steps of this task are no longer in your context. Summary "
             "of the work so far:]\n") + summary
            + ((("\n\nPlan actual:\n" if es else "\n\nCurrent plan:\n") + plan_txt) if plan_txt else "")
            + ("\n\nContinúa la tarea desde aquí (no repitas lo ya hecho)." if es else
               "\n\nContinue the task from here (do not redo what is done).")).strip()}
        self.task_summary, self.task_summary_msg = summary, note
        keep_users = [messages[i] for i in split["protected"]]
        messages[:] = messages[:start] + keep_users + [note] + messages[split["tail"]:]
        after = est_tokens(messages, self.ratio) + self.spec_tokens
        self.compactions += 1
        info = {"before": int(before), "after": int(after)}
        res = {"ok": True, "summary": (f"Contexto de la tarea compactado: {len(old_msgs)} mensajes resumidos "
                                       f"(≈{before:,} → {after:,} tokens)").replace(",", "."),
               "compaction": {**info, "messages": len(old_msgs), "mid_turn": True}, "seconds": round(time.time() - started, 2)}
        self.emit({"type": "context_compacted", **info})
        self.emit({"type": "tool_result", "id": cid, "name": "compact_context", **res})
        with self.lock:
            self.parts.append({"type": "tool", "name": "compact_context", "args": {}, **res})
        return True

    # -------------------------------------------------------------- main loop

    def run(self) -> dict:
        t0 = time.perf_counter()
        with SessionLocal() as db:
            user = db.get(User, self.t.user_id)
            conv = db.get(Conversation, self.t.conversation_id)
            model = db.get(RegisteredModel, self.t.model_id)
            _ = model.runtime_config
            files = []
            for fid in self.t.attachments:
                f = db.get(UserFile, fid)
                if f and f.owner_id == user.id:
                    db.expunge(f)
                    files.append(f)
            self.refs = image_refs(db, conv.id, files, *self.t.history_bounds())
            self.ctx = effective_context(model, self.t.context_limit)
            tools_on = self.t.use_tools and llm.supports_tools(model) and self.t.mode == "chat"
            self.flags = {"web": tools_on and self.t.web and bool(get_setting(db, "web_search_enabled")),
                          "skills": tools_on and bool(get_setting(db, "skills_enabled")),
                          "agents": tools_on and bool(get_setting(db, "agents_enabled")),
                          "auto_install": bool(get_setting(db, "skills_auto_install")),
                          "code": tools_on and bool(get_setting(db, "code_exec_enabled")),
                          "docs": tools_on}  # search_knowledge / read_document / describe_data / query_data
            self.small = (model.parameters_b or 99) < SMALL_MODEL_B
            self.db_names = remote_db.names(db, user.id) if tools_on else []
            if self.small:  # small models: core tools only (see tool_specs)
                self.flags.update(agents=False)  # run_code is filtered in tool_specs; ws_run stays for workspaces
            self.project_id = conv.project_id
            wid = self.t.workspace_id or (conv.settings or {}).get("workspace_id")
            if wid and tools_on:
                from ..models import Workspace

                ws = db.get(Workspace, uuid.UUID(str(wid)))
                if ws and ws.owner_id == user.id:
                    db.expunge(ws)
                    self.workspace = ws
            ws_mode = self.workspace is not None
            self.max_steps = max(1, int(get_setting(db, "agent_max_steps_workspace" if ws_mode else "agent_max_steps_chat")
                                        or (WORKSPACE_MAX_STEPS if ws_mode else MAX_STEPS)))
            self.max_seconds = 60 * float(get_setting(db, "agent_max_minutes_workspace" if ws_mode else "agent_max_minutes_chat")
                                          or (MAX_MINUTES_WORKSPACE if ws_mode else MAX_MINUTES_CHAT))
            agents_available = self.flags["agents"]
            self.decision = self._decide(db, conv, model, files, ws_mode)
            if self.t.preferred_agent and agents_available:
                self.flags["agents"] = True  # the explicit conversation choice overrides the director's automatic pruning
            self.agents = agent_profiles(db, user) if self.flags["agents"] else {}
            self.preferred_agent = self.t.preferred_agent
            if not self.flags["web"] and self.preferred_agent != "investigador":
                self.agents.pop("investigador", None)
            if self.preferred_agent and self.preferred_agent not in self.agents:
                raise AgentError(f"El agente preferido «{self.preferred_agent}» no está disponible con la configuración actual.",
                                 "Activa Herramientas y las capacidades que necesita, elige otro agente o vuelve a Automático.")
            self.max_parallel = int(get_setting(db, "max_parallel_agents") or 3)
            summary_upto = conv.summary_upto
            self.summary = conv.summary
            system = self._system(db, user, conv, model, tools_on)
            db.expunge(model)
        self.model = model
        max_tokens = int(self.t.params.get("max_tokens") or 1024)
        sys_tokens = est_tokens(system) + (sum(est_tokens(json.dumps(t)) for t in self.tool_specs()) if tools_on else 0)
        avail = max(1024, self.ctx - max_tokens - sys_tokens)
        # Auto-compaction: summarise old messages when the history would take more than half of the free window.
        if self.t.auto_compact and self.t.mode == "chat":
            with SessionLocal() as db:
                hist = [m for m in prior_messages(db, self.t.conversation_id, *self.t.history_bounds())
                        if summary_upto is None or m.id > summary_upto]
                hist_tokens = sum(est_tokens(_message_text(m)) for m in hist)
            if hist_tokens > avail * 0.5 and len(hist) >= 3:
                self.emit({"type": "status", "status": "compacting"})
                cid = f"call_{uuid.uuid4().hex[:12]}"
                c_started = time.time()
                self.emit({"type": "tool_start", "id": cid, "name": "compact_context", "label": TOOL_LABELS["compact_context"],
                           "started": round(c_started, 3)})
                try:
                    upto, excl = self.t.history_bounds()
                    info = compact_conversation(self.t.conversation_id, self.t.model_id, self.ctx, budget=avail,
                                                before_id=upto, exclude=excl)
                except (EngineError, scheduler.SchedulingError) as e:
                    info = None
                    self.emit({"type": "warning", "message": f"No se pudo compactar el contexto: {e.message}"})
                if info:
                    res = {"ok": True, "summary": f"{info['summarized_now']} mensajes antiguos resumidos "
                                                  f"(≈{info['tokens_before']:,} → {info['tokens_after']:,} tokens)".replace(",", "."),
                           "compaction": info, "seconds": round(time.time() - c_started, 2)}
                    self.emit({"type": "tool_result", "id": cid, "name": "compact_context", **res})
                    self.parts.append({"type": "tool", "name": "compact_context", "args": {}, **res})
                    self.emit({"type": "compacted", **info})
                    with SessionLocal() as db:
                        conv = db.get(Conversation, self.t.conversation_id)
                        summary_upto, self.summary = conv.summary_upto, conv.summary
                        user = db.get(User, self.t.user_id)
                        model2 = db.get(RegisteredModel, self.t.model_id)
                        system = self._system(db, user, conv, model2, tools_on)
                else:
                    self.emit({"type": "tool_result", "id": cid, "name": "compact_context", "ok": True,
                               "summary": "No hacía falta compactar", "seconds": round(time.time() - c_started, 2)})
        with SessionLocal() as db:
            history = self._history(db, self.t.conversation_id, llm.supports_vision(model),
                                    int(avail * 0.55 * CHARS_PER_TOKEN), summary_upto)
            att_parts, warnings = self._attachment_parts(db, model, files, int(avail * 0.4 * CHARS_PER_TOKEN))
        if self.dropped:
            warnings.append(f"La conversación no cabe en la ventana de contexto ({self.ctx:,} tokens): el modelo no ve los "
                            f"{self.dropped} mensajes más antiguos. Activa «Compactar automáticamente» o amplía la ventana."
                            .replace(",", "."))
        for w in warnings:
            self.emit({"type": "warning", "message": w})
        if self.t.mode == "image":
            return self._direct_image(files, t0)
        user_content = ([{"type": "text", "text": self.t.content}] + att_parts) if att_parts else self.t.content
        self.current_user = {"role": "user", "content": user_content}
        messages = [{"role": "system", "content": system}] + history + [self.current_user]
        text_out: list[str] = []
        reasoning: list[str] = []
        usage_total: dict = {"prompt_tokens": 0, "completion_tokens": 0}
        first = {"t": None}
        finish = None
        specs = self.tool_specs() if tools_on else None
        preferred_agent_completed = False
        preferred_profile = self.agents.get(self.preferred_agent) if self.preferred_agent else None
        preferred_tools = set((preferred_profile or {}).get("tools") or [])
        # A document-only preferred agent does not need a second LLM planning pass: retrieve once here, then let the
        # main model synthesise the answer. This preserves the selected agent's instructions and exact source metadata
        # while avoiding the former delegate -> search -> report -> rewrite round trip.
        if preferred_profile and "search_knowledge" in preferred_tools \
                and preferred_tools.issubset({"search_knowledge", "read_document"}):
            cid = f"call_{uuid.uuid4().hex[:12]}"
            retrieval_query = _document_search_query(self.t.content)
            result = self.run_tool(cid, "search_knowledge", {"query": retrieval_query})
            messages.append({"role": "user", "content":
                             f"El agente seleccionado «{self.preferred_agent}» ya ha ejecutado su búsqueda documental "
                             "en el servidor. No delegues ni uses más herramientas: responde ahora a la petición "
                             "original basándote únicamente en estos resultados y siguiendo sus instrucciones.\n\n"
                             f"Instrucciones del agente:\n{str(preferred_profile.get('instructions') or '')[:6000]}\n\n"
                             f"Resultados verificados:\n{str(result.get('content') or result.get('summary'))[:TOOL_RESULT_CHARS]}"})
            preferred_agent_completed = True

        def on_delta(piece):
            if first["t"] is None:
                first["t"] = time.perf_counter() - t0
            self.emit({"type": "delta", "content": piece})

        def on_reasoning(piece):
            if first["t"] is None:
                first["t"] = time.perf_counter() - t0
            reasoning.append(piece)
            self.emit({"type": "reasoning", "content": piece})

        if specs:
            self.spec_tokens = sum(est_tokens(json.dumps(t, ensure_ascii=False)) for t in specs)
        # With thinking on, the reasoning shares max_tokens with the answer: give the step room for both.
        step_tokens = max(max_tokens, THINKING_MIN_TOKENS) if self.t.thinking else max_tokens
        es = self.t.lang != "en"
        deadline = time.monotonic() + self.max_seconds
        max_nudges = MAX_NUDGES_WORKSPACE if self.workspace is not None else MAX_NUDGES_CHAT
        row_limit = NUDGES_ROW_WORKSPACE if self.workspace is not None else 2
        rollovers = 0  # workspace: how many times the time/step limit was lifted because the goal was not met yet
        seg_step0 = 0  # step number where the current segment started
        audits = 0  # goal audits asked this turn (workspace)
        audit_open = False  # the last message asked for an audit and its answer has not come yet
        goal_met = False
        work_steps = 0  # steps that called tools
        step = 0
        ctx_retries = 0
        continuations = 0  # an answer cut by max_tokens is continued automatically (3 times at most)
        think_retries = 0  # steps cut while still reasoning (no text, no tool call)
        loops = 0  # streams cut because the model was going round in circles (loopguard)
        think_off = 0  # steps left without thinking after a loop
        nudges = nudges_in_row = 0
        glue = False
        last = False
        limit_hit: str | None = None
        step_thinking = self.t.thinking
        while True:
            if self.stop():
                break
            if not last and (step - seg_step0 >= self.max_steps or time.monotonic() > deadline):
                limit_hit = "steps" if step - seg_step0 >= self.max_steps else "time"
                why = (f"{self.max_steps} pasos" if limit_hit == "steps" else f"{round(self.max_seconds / 60)} min")
                if self.workspace is not None and not goal_met and rollovers < MAX_ROLLOVERS_WORKSPACE and not self.stop():
                    # The goal is not met yet: the safety limit only closes this segment. Compact and go on by itself
                    # (the user asked for tasks that finish, not for «continúa» after every few hours).
                    rollovers += 1
                    seg_step0 = step
                    deadline = time.monotonic() + self.max_seconds
                    nudges_in_row = 0
                    limit_hit = None
                    self._compact_turn(messages, step_tokens, force=True)
                    self.emit({"type": "warning", "message": (
                        f"Tramo de trabajo terminado ({why}); la tarea sigue y continúa sola (tramo {rollovers + 1} de "
                        f"{MAX_ROLLOVERS_WORKSPACE + 1})." if es else f"Work segment finished ({why}); the task goes on by itself "
                        f"(segment {rollovers + 1} of {MAX_ROLLOVERS_WORKSPACE + 1}).")})
                    pend = "\n".join(f"- [{i['status']}] {i['content']}" for i in plan_open(self.plan)[:20])
                    messages.append({"role": "user", "content": (
                        "[Continuación automática] El tramo anterior llegó a su límite de seguridad pero la tarea NO ha terminado. "
                        "Sigue donde lo dejaste, sin repetir lo ya hecho y sin pedir permiso." + (("\nPendiente según tu plan:\n" + pend) if pend else
                                                                                                    "\nRelee la petición original y comprueba qué falta.")
                        if es else "[Automatic continuation] The previous segment hit its safety limit but the task is NOT finished. "
                        "Go on where you left off, without repeating what is done and without asking permission."
                        + (("\nPending in your plan:\n" + pend) if pend else "\nRe-read the original request and check what is missing."))})
            if not last and (step - seg_step0 >= self.max_steps or time.monotonic() > deadline):
                # Safety net (admin settings): one last step without tools to report what is done and what is left.
                limit_hit = "steps" if step - seg_step0 >= self.max_steps else "time"
                last = True
                why = (f"{self.max_steps} pasos" if limit_hit == "steps" else f"{round(self.max_seconds / 60)} min")
                self.emit({"type": "warning", "message": f"Se ha alcanzado el límite de seguridad del turno ({why}). "
                                                         "Escribe «continúa» para que siga trabajando."})
                messages.append({"role": "user", "content": (
                    f"[Límite de seguridad de este turno alcanzado ({why}).] No puedes usar más herramientas ahora. "
                    "Resume lo que has hecho, qué queda pendiente y dile al usuario que escriba «continúa» para seguir."
                    if es else f"[Safety limit of this turn reached ({why}).] You cannot use tools now. Summarise what "
                    "is done and what is left, and tell the user to write «continue» to go on.")})
            if not last:  # on the final no-tools step queued messages wait for the follow-up turn instead
                self._absorb_inbox(messages)
            step += 1
            self.emit({"type": "step", "n": step})
            self.emit({"type": "status", "status": "loading" if self.inst is None else "generating"})
            if self.inst is None:
                self.inst = scheduler.ensure_loaded(self.t.model_id, wait_s=1800)
                scheduler.begin_request(self.inst.id)
            self.emit({"type": "status", "status": "generating", "load_seconds": self.inst.load_seconds})
            self._compact_turn(messages, step_tokens)
            fit_tokens = self._fit(messages, step_tokens)
            chars = sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in messages)
            r0 = len(reasoning)
            try:
                step_specs = specs
                if preferred_agent_completed and specs:
                    step_specs = [s for s in specs if s["function"]["name"] not in
                                  {"delegate_task", "search_knowledge", "read_document"}]
                text, calls, finish = self._llm_step(messages, step_specs if not last else None, fit_tokens,
                                                     step_thinking, on_delta, on_reasoning, usage_total,
                                                     interruptible=not last, main=True)
            except EngineError as e:
                if ctx_retries < 3 and is_context_error(e.message):
                    # the estimate was too optimistic: be stricter, compact the turn's steps and retry
                    ctx_retries += 1
                    step -= 1
                    self.ratio = max(1.5, self.ratio * 0.75)
                    if not self._compact_turn(messages, fit_tokens, force=True):
                        self.emit({"type": "warning", "message": "El mensaje no cabía en el contexto; se ha recortado y reintentado."})
                    continue
                raise
            step_thinking = self.t.thinking and think_off <= 0
            think_off -= 1
            if usage_total.get("last_prompt"):  # calibrate chars/token on this conversation
                self.ratio = max(1.5, min(6.0, chars / max(1, usage_total["last_prompt"])))
                self.emit({"type": "context", "used": usage_total["last_prompt"] + usage_total.get("last_completion", 0),
                           "limit": self.ctx})
            if self._steered and not self.stop():
                # The user wrote while the model was still generating (maybe minutes of reasoning): keep what it had
                # thought and said, drop a tool call cut halfway, and read the message right now.
                self._steered = False
                if glue and text_out:
                    text_out[-1] += text
                elif text.strip():
                    text_out.append(text)
                glue = False
                if text.strip():
                    messages.append({"role": "assistant", "content": text})
                if not self._absorb_inbox(messages, partial_reasoning="".join(reasoning[r0:])):
                    self._steered = False  # nothing to read after all (taken meanwhile): carry on normally
                self.emit({"type": "warning", "message": "Pensamiento interrumpido para leer tu mensaje; se conserva lo razonado." if es
                           else "Thinking interrupted to read your message; what was reasoned is kept."})
                nudges_in_row = 0
                continue
            self._steered = False
            if self._loop_hit and not self.stop():
                kind, why = self._loop_hit
                self._loop_hit = None
                loops += 1
                if kind == "text" and text.strip():
                    text = loopguard.cut_repeat(text)
                    if glue and text_out:
                        text_out[-1] += text
                    else:
                        text_out.append(text)
                    glue = False
                    messages.append({"role": "assistant", "content": text})
                think_off = 3 if loops < 3 else 10 ** 6  # the third loop of a turn: no more thinking until it ends
                step -= 1  # the cut stream is not a step
                step_thinking = False
                self.emit({"type": "warning", "message": (
                    f"El modelo entró en un bucle ({why}); se ha cortado y se continúa " + ("sin razonar." if loops >= 3 else
                                                                                           "sin razonar en los próximos pasos.")
                    if es else f"The model went into a loop ({why}); it was cut and the work goes on without reasoning.")})
                messages.append({"role": "user", "content": (
                    "[Aviso del sistema] Tu " + ("razonamiento" if kind == "reasoning" else "respuesta") + " se repetía en bucle "
                    f"({why}) y se ha cortado. No vuelvas a deliberar lo mismo: con lo que ya sabes, decide AHORA la siguiente acción "
                    "concreta y ejecútala llamando a la herramienta (o, si ya está todo hecho, da el resumen final). Si te falta un dato "
                    "que solo el usuario puede dar, pregúntalo en una frase." if es else
                    "[System notice] Your " + ("reasoning" if kind == "reasoning" else "answer") + f" was looping ({why}) and was cut. "
                    "Do not deliberate the same again: with what you know, decide the next concrete action NOW and run it by "
                    "calling the tool (or, if everything is done, give the final summary).")})
                if loops <= 12:
                    continue
            if not calls and not text.strip() and finish == "length" and len(reasoning) > r0 and think_retries < 2 \
                    and not self.stop():
                # The whole budget went into reasoning: retry with more room, then without thinking. Not an answer.
                think_retries += 1
                step -= 1
                if think_retries == 1:
                    step_tokens = min(max(step_tokens * 2, THINKING_MIN_TOKENS), 65536)
                else:
                    step_thinking = False
                self.emit({"type": "warning", "message": "El razonamiento agotó los tokens del paso; se reintenta "
                                                         + ("con más margen." if think_retries == 1 else "sin pensar.")})
                continue
            if text.strip() or calls:
                think_retries = 0
            if not calls and step == 1 and self.preferred_agent and not preferred_agent_completed and not re.match(
                    r"^\s*(hola|buenos días|buenas tardes|buenas noches|hello|hi|gracias|thanks)\b[.!?\s]*$",
                    self.t.content, re.I):
                messages += [{"role": "assistant", "content": text or ""},
                             {"role": "user", "content":
                              f"La conversación tiene seleccionado el agente «{self.preferred_agent}». Esta petición "
                              "es sustantiva: debes delegar ahora la tarea principal a ese agente mediante "
                              "delegate_task y basar la respuesta en su resultado."}]
                self.emit({"type": "warning", "message":
                           f"Se fuerza el uso del agente preferido «{self.preferred_agent}» para esta petición."})
                continue
            if glue and text_out:
                text_out[-1] += text  # seamless continuation of the previous piece
            else:
                text_out.append(text)
            glue = False
            if not calls and finish == "length" and text.strip() and continuations < 3 and not self.stop() and not last:
                continuations += 1
                glue = True
                step_thinking = False  # continuing a cut answer needs no new reasoning
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "Continúa exactamente donde lo has dejado, sin repetir nada y sin "
                                                         "comentarios sobre la continuación." if es else
                                                         "Continue exactly where you stopped, without repeating anything."}]
                continue
            if self.stop():
                break
            if not calls:
                if last:
                    break
                was_audit, audit_open = audit_open, False
                reason = continue_reason(text, self.plan, self.unverified and self.flags["code"], nudges_in_row,
                                         nudges, max_nudges, row_limit) if specs else \
                    ("empty" if not text.strip() and nudges_in_row < 2 and nudges < max_nudges else None)
                asking = text.strip().endswith("?") and not hands_off(text)  # a real question: the user has to answer
                if specs and self.workspace is not None and reason is None and not asking:
                    low = (text or "").lower()
                    if GOAL_DONE in low or "goals met" in low:
                        goal_met = True  # the audit passed (or the model closed with its own «objetivos cumplidos» summary)
                    elif was_audit:
                        if audits < MAX_AUDITS:
                            reason = "audit_fail"
                    elif not goal_met and audits < MAX_AUDITS and work_steps >= AUDIT_MIN_STEPS and nudges < max_nudges:
                        reason = "audit"
                elif was_audit and asking:
                    goal_met = True  # it stopped to ask the user something: that is a legitimate end
                if reason:
                    nudges += 1
                    nudges_in_row += 1
                    if reason == "audit":
                        audits += 1
                        audit_open = True
                    if reason == "verify":
                        self.unverified = False  # asked once; do not insist
                    messages += [{"role": "assistant", "content": text or ""},
                                 {"role": "user", "content": nudge_message(reason, self.plan, self.t.lang)}]
                    continue
                messages.append({"role": "assistant", "content": text or ""})
                if self._absorb_inbox(messages, closing=True):  # the user wrote meanwhile: keep going with it
                    nudges_in_row = 0
                    continue
                break
            nudges_in_row = 0
            work_steps += 1
            assistant_msg, results = self._calls_to_msg(text, calls)
            messages.append(assistant_msg)
            self.emit({"type": "status", "status": "tool"})
            delegated = [r for r in results if r[1] == "delegate_task"]
            outputs: dict[str, dict] = {}
            if len(delegated) > 1:  # sub-agents in parallel; the rest in order
                with ThreadPoolExecutor(max_workers=max(1, min(self.max_parallel, len(delegated)))) as ex:
                    futs = {cid: ex.submit(self.run_tool, cid, name, args) for cid, name, args in delegated}
                    for cid, fut in futs.items():
                        outputs[cid] = fut.result()
            for cid, name, args in results:
                if name == "update_plan":
                    res = self._update_plan(args)
                else:
                    res = outputs.get(cid) or self.run_tool(cid, name, args)
                    if name == "delegate_task" and self.preferred_agent \
                            and (res.get("agent") or {}).get("name") == self.preferred_agent:
                        preferred_agent_completed = True
                    if name in WS_WRITE_TOOLS and name != "download_file" and res.get("ok"):
                        self.unverified = True
                    elif name == "ws_run":
                        self.unverified = False
                messages.append({"role": "tool", "tool_call_id": cid, "content": str(res.get("content") or res.get("summary"))[:TOOL_RESULT_CHARS]})
            if self.stop():
                break
            if model.backend == "vllm":
                with SessionLocal() as db:  # tools may have changed memory; refresh runtime config
                    model = db.get(RegisteredModel, self.t.model_id)
                    _ = model.runtime_config
                    db.expunge(model)
                self.model = model
        self._release_llm()
        self._remember_fallback()
        total = time.perf_counter() - t0
        final = "\n\n".join(x for x in text_out if x.strip())
        doc_sources = []
        doc_seen = set()
        for part in self.parts:
            for src in part.get("sources") or []:
                path = src.get("source_path")
                key = path
                if path and key not in doc_seen:
                    doc_seen.add(key)
                    doc_sources.append(src)
        if doc_sources:
            final = _remove_unverified_unc_paths(final, {src["source_path"] for src in doc_sources})
            verified = "\n".join(
                f"- [{src.get('filename') or 'documento'}"
                + (f", p. {src['page']}" if src.get("page") else "")
                + f"] `{src['source_path']}`" for src in doc_sources[:30])
            final += ("\n\n### Fuentes documentales verificadas\n\n"
                      "Estas rutas proceden directamente del índice y no han sido redactadas por el modelo:\n\n"
                      + verified)
        ct = usage_total["completion_tokens"]
        ttft = first["t"]
        decode = total - (ttft or 0)
        metrics = {"ttft_ms": round(ttft * 1000, 1) if ttft else None, "latency_ms": round(total * 1000, 1),
                   "prompt_tokens": usage_total["prompt_tokens"] or None, "completion_tokens": ct or None,
                   "tokens_per_s": round((ct - 1) / decode, 2) if ct and ct > 1 and decode > 0 and not self.parts else None,
                   "token_count_source": "engine_usage", "finish_reason": finish, "steps": step,
                   "nudges": nudges or None, "compactions": self.compactions or None, "injected": self.injected or None,
                   "limit_hit": limit_hit, "plan_open": len(plan_open(self.plan)) if self.plan else None,
                   "tools": [p["name"] for p in self.parts if p.get("type") == "tool"],
                   "context_used": (usage_total.get("last_prompt") or 0) + (usage_total.get("last_completion") or 0) or None,
                   "context_limit": self.ctx,
                   "director": self.decision.public() if self.decision else None}
        return {"text": final, "reasoning": "".join(reasoning) or None, "metrics": metrics, "parts": self.parts}


    def _direct_image(self, files: list[UserFile], t0: float) -> dict:
        """Image mode: the message is the prompt (or the edit instruction when an image is attached)."""
        src = next((f for f in files if f.kind == "image"), None)
        cid = f"call_{uuid.uuid4().hex[:12]}"
        if src is not None:
            ref = next(r["ref"] for r in self.refs if r["id"] == str(src.id))
            self.run_tool(cid, "edit_image", {"image_ref": ref, "instruction": self.t.content})
        elif self.refs and re.match(r"^\s*(edita|edit|cambia|change|modifica|haz que|make)\b", self.t.content, re.I):
            self.run_tool(cid, "edit_image", {"image_ref": self.refs[-1]["ref"], "instruction": self.t.content})
        else:
            self.run_tool(cid, "generate_image", {"prompt": self.t.content, "aspect": self.t.params.get("aspect", "square")})
        total = time.perf_counter() - t0
        ok = all(p.get("ok") for p in self.parts)
        return {"text": "" if ok else (self.parts[-1].get("summary") if self.parts else ""), "reasoning": None,
                "parts": self.parts, "metrics": {"latency_ms": round(total * 1000, 1), "tools": [p["name"] for p in self.parts if p.get("type") == "tool"],
                                                 "mode": "image"}}


def run_turn(turn: Turn, emit: Callable[[dict], None], stop: Callable[[], bool]) -> dict:
    r = Runner(turn, emit, stop)
    try:
        return r.run()
    except (scheduler.SchedulingError, EngineError) as e:
        raise AgentError(e.message, getattr(e, "remediation", None))
    finally:
        r._release_llm()
