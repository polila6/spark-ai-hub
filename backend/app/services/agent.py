"""Web chat agent: personal context (instructions, memory, project), attachments (images, documents, data) and tools
(image generation/editing, knowledge search, document reading, data queries, memory, web search, skills and
sub-agents) executed server-side. Long conversations are compacted automatically to fit the context window.

`run_turn` is synchronous (runs in a thread) and reports progress through `emit(event_dict)`; the router streams the
events to the browser as SSE. Tool results are stored in `Message.parts` so the UI can replay them.
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
from ..settings_store import get_setting
from . import canvas as canvas_svc
from . import dataq, kb, llm, sandbox, scheduler, web, workflows
from . import skills as skills_svc
from . import files as files_svc
from .jobs import JobError, create_job, request_cancel

MAX_STEPS = 10
WORKSPACE_MAX_STEPS = 30  # write → run → read the error → fix loops need more steps
SUB_MAX_STEPS = 8
IMAGE_MAX_SIDE = 1280
TOOL_RESULT_CHARS = 12000
CHARS_PER_TOKEN = 3.2  # first estimate; recalibrated with the engine's real prompt_tokens during the turn
IMAGE_TOKENS = 1100


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
    workspace_id: uuid.UUID | None = None  # workspace mode: files and commands in the user's persistent workspace


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


def prior_messages(db, conv_id: uuid.UUID) -> list[Message]:
    """Conversation messages before the turn being answered (the router stores the new user message first)."""
    msgs = list(db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)))
    if msgs and msgs[-1].role == "user":
        msgs.pop()
    return msgs


def image_refs(db, conv_id: uuid.UUID, extra: list[UserFile] | None = None) -> list[dict]:
    """Stable labels img1..imgN for every image in the conversation (uploads and generated), oldest first."""
    refs: list[dict] = []
    for m in prior_messages(db, conv_id):
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


def _summarize(model: RegisteredModel, previous: str | None, transcript: str, words: int) -> str:
    body = {"messages": [{"role": "system", "content": SUMMARY_PROMPT.format(words=words)},
                         {"role": "user", "content": (f"Resumen previo:\n{previous}\n\n" if previous else "")
                          + f"Conversación:\n{transcript}"}],
            "temperature": 0.2, "max_tokens": min(4000, int(words * 2.2))}
    if model.backend == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    out = llm.chat_once(model, body)
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def compact_conversation(conv_id: uuid.UUID, model_id: uuid.UUID, ctx: int, budget: int | None = None,
                         force: bool = False, exclude_last_user: bool = True) -> dict | None:
    """Summarise the oldest messages so the rest fits. `budget`: tokens available for the history.

    Without `force`, nothing happens while the history uses less than half of the budget. The most recent messages
    (about a quarter of the budget, at least the last exchange) stay verbatim. Returns stats or None.
    """
    budget = budget or int(ctx * 0.6)
    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        msgs = prior_messages(db, conv_id) if exclude_last_user else list(db.scalars(
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
                       "questions about their papers, notes, reports or any uploaded document. Cite the sources.",
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
            "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Open a public web page or PDF and read its text (the most relevant passages when `query` is given).",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "query": {"type": "string", "description": "What to look for in the page (optional)"}},
            "required": ["url"]}}},
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
                       "automatically. Print the key results to stdout.",
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
                     "tools": ["web_search", "fetch_url"]},
    "documentalista": {"description": "Busca y lee en los documentos del usuario (base de conocimiento y adjuntos).",
                       "instructions": "Eres un documentalista. Busca con search_knowledge (prueba varias consultas) y "
                                       "lee con read_document lo necesario. Devuelve los fragmentos relevantes con su "
                                       "cita [archivo, p. N] y una síntesis.",
                       "tools": ["search_knowledge", "read_document"]},
    "analista": {"description": "Analiza archivos de datos (CSV, Excel, Parquet, SQLite) con consultas SQL.",
                 "instructions": "Eres un analista de datos. Usa describe_data y query_data para responder con cifras "
                                 "exactas; muestra tablas en Markdown y explica el método. No inventes datos.",
                 "tools": ["describe_data", "query_data", "read_document", "run_code"]},
    "revisor": {"description": "Revisa críticamente un texto, plan, código o respuesta: errores, huecos y mejoras.",
                "instructions": "Eres un revisor exigente. Analiza lo que te pasan, señala errores, supuestos dudosos, "
                                "riesgos y omisiones, y propone mejoras concretas priorizadas.",
                "tools": []},
    "general": {"description": "Agente de propósito general con las instrucciones que le des (campo instructions).",
                "instructions": "Eres un agente auxiliar. Cumple la tarea con las herramientas disponibles y devuelve "
                                "un resultado completo y conciso.",
                "tools": ["web_search", "fetch_url", "search_knowledge", "read_document", "describe_data", "query_data",
                          "use_skill", "read_skill_file"]},
}
SUB_AGENT_TOOLS = {"web_search", "fetch_url", "search_knowledge", "read_document", "describe_data", "query_data",
                   "use_skill", "read_skill_file", "run_code"}

TOOL_LABELS = {"generate_image": "Generando imagen", "edit_image": "Editando imagen", "search_knowledge": "Buscando en tus documentos",
               "read_document": "Leyendo documento", "describe_data": "Analizando datos", "query_data": "Consultando datos",
               "save_memory": "Guardando en memoria", "web_search": "Buscando en internet", "fetch_url": "Leyendo página web",
               "use_skill": "Cargando skill", "read_skill_file": "Leyendo archivo de skill", "find_skills": "Buscando skills",
               "install_skill": "Instalando skill", "delegate_task": "Agente trabajando", "compact_context": "Contexto compactado",
               "run_code": "Ejecutando código", "canvas_write": "Escribiendo en el lienzo",
               "canvas_edit": "Editando el lienzo", "deep_research": "Investigación a fondo",
               "schedule_task": "Programando tarea", "ws_list": "Mirando archivos", "ws_read": "Leyendo archivo",
               "ws_write": "Escribiendo archivo", "ws_append": "Añadiendo al archivo", "ws_edit": "Editando archivo", "ws_run": "Ejecutando en el workspace"}


def agent_profiles(db, user: User) -> dict[str, dict]:
    """Built-in agents plus the user's own and public custom agents (name -> {description, instructions, tools})."""
    out = {k: {**v, "builtin": True} for k, v in BUILTIN_AGENTS.items()}
    from sqlalchemy import or_

    for a in db.scalars(select(AgentProfile).where(AgentProfile.enabled.is_(True), or_(
            AgentProfile.owner_id == user.id, AgentProfile.visibility == Visibility.public)).order_by(AgentProfile.name)):
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
            "instructions": {"type": "string", "description": "Extra role/instructions (for agent=general)"}},
            "required": ["agent", "task"]}}}


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
        self.skill_names: list[str] = []
        self.flags = {"web": False, "skills": False, "agents": False, "auto_install": False, "code": False}
        self.project_id: uuid.UUID | None = None
        self.canvas_id: uuid.UUID | None = None
        self.workspace = None
        self.lock = threading.Lock()
        self.summary: str | None = None
        self.ctx = 8192
        self.dropped = 0
        self.current_user: dict | None = None
        self.max_parallel = 3

    # -------------------------------------------------------------- context

    def _system(self, db, user: User, conv: Conversation, model: RegisteredModel, tools_on: bool) -> str:
        prefs = user.preferences or {}
        es = self.t.lang != "en"
        org = get_settings().org_name.strip()
        lines = [(f"Eres el asistente de Spark AI Hub, la plataforma de IA privada{' de ' + org if org else ''}. Te ejecutas "
                  "localmente en un NVIDIA DGX Spark; las conversaciones no salen de este equipo.") if es else
                 (f"You are the Spark AI Hub assistant, the private AI platform{' of ' + org if org else ''}. You run "
                  "locally on an NVIDIA DGX Spark; conversations never leave this machine."),
                 f"{'Fecha' if es else 'Date'}: {_now_str()}. {'Usuario' if es else 'User'}: {user.display_name or user.username}.",
                 "Responde en el idioma del usuario. Usa Markdown (tablas, listas, bloques de código, LaTeX con $...$)."
                 if es else "Answer in the user's language. Use Markdown (tables, lists, code blocks, LaTeX with $...$)."]
        if tools_on:
            lines.append(
                "Herramientas: generate_image y edit_image crean/editan imágenes (la imagen se muestra sola al usuario; "
                "no escribas enlaces ni markdown de imagen); search_knowledge busca en los documentos del usuario "
                "(cita así: [archivo, p. N]); read_document lee documentos largos adjuntos; describe_data y query_data "
                "consultan archivos de datos con SQL; save_memory guarda datos duraderos que el usuario quiere que "
                "recuerdes. Usa las herramientas solo cuando hagan falta y no inventes resultados." if es else
                "Tools: generate_image/edit_image create or edit images (the image is shown to the user automatically; "
                "do not write image links); search_knowledge searches the user's documents (cite as [file, p. N]); "
                "read_document reads long attachments; describe_data/query_data query data files with SQL; save_memory "
                "stores lasting facts the user wants you to remember. Use tools only when needed; never invent results.")
        if tools_on and self.flags["web"]:
            lines.append("Internet: web_search busca en la web (a través del buscador privado del Spark) y fetch_url lee "
                         "páginas y PDFs. Úsalas para actualidad, precios, versiones, normativa o datos que no sepas con "
                         "certeza; lee las fuentes antes de responder y cita así: [título](url). Nunca pongas datos "
                         "personales o confidenciales del usuario en una búsqueda." if es else
                         "Internet: web_search searches the web (through the Spark's private search engine) and fetch_url "
                         "reads pages/PDFs. Use them for current facts; read sources before answering and cite as "
                         "[title](url). Never put personal or confidential data in a query.")
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
            lines.append(("Archivos actuales:\n" if es else "Current files:\n") + listing)
        if tools_on and self.flags["code"] and self.workspace is None:
            lines.append("Código: run_code ejecuta Python, bash o Node en un entorno aislado sin internet. Úsalo para "
                         "cálculos exactos, análisis y gráficos de datos, y para crear o convertir archivos (Word, Excel, "
                         "PowerPoint, PDF, imágenes). Guarda los resultados en /work: el usuario los recibe como archivos "
                         "descargables (no escribas enlaces). Si falla, lee el error, corrige y vuelve a intentarlo." if es else
                         "Code: run_code runs Python, bash or Node in an isolated sandbox without internet, for exact "
                         "calculations, data analysis, charts and creating/converting files. Save results in /work: the "
                         "user receives them as downloads (do not write links). If it fails, fix it and retry.")
        if tools_on and self.workspace is None:
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
            mems = db.scalars(select(Memory).where(Memory.owner_id == user.id).order_by(Memory.created_at.desc()).limit(60)).all()
            if mems:
                lines.append(("Memoria (cosas que el usuario te pidió recordar):\n" if es else "Memory:\n")
                             + "\n".join(f"- {m.content}" for m in reversed(mems)))
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
        msgs = [m for m in prior_messages(db, conv_id) if after_id is None or m.id > after_id]
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
        raise AgentError("La generación tarda demasiado; sigue en Trabajos.")

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

    def tool_generate_image(self, call_id: str, args: dict) -> dict:
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
        img = self._image_job(call_id, wf_id, inputs, inputs["prompt"])
        where = f" Guardada en el workspace como {img['workspace_path']}." if img.get("workspace_path") else ""
        return {"ok": True, "summary": f"Imagen {img['ref']} generada con {wf_id} ({w}×{h}) y mostrada al usuario.{where}",
                "image": img, **({"workspace": {"changed": [img["workspace_path"]]}} if img.get("workspace_path") else {})}

    def tool_edit_image(self, call_id: str, args: dict) -> dict:
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
        img = self._image_job(call_id, wf_id, inputs, instr)
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
                    "snippet": r["text"][:280]} for r in res["results"]]
        content = "\n\n".join(f"[{i + 1}] {r['filename']}" + (f", p. {r['page']}" if r["page"] else "")
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
        with SessionLocal() as db:
            try:
                res = web.search(db, q, str(args.get("category") or "web"), None, 8, args.get("time_range"))
            except web.WebError as e:
                raise AgentError(str(e))
        if not res:
            return {"ok": True, "summary": f"Sin resultados para «{q}»", "sources": [], "query": q,
                    "content": "No results. Try other keywords or another category."}
        content = "\n\n".join(f"[{i + 1}] {r['title']}\n{r['url']}" + (f" ({r['published']})" if r["published"] else "")
                              + f"\n{r['snippet']}" for i, r in enumerate(res))
        return {"ok": True, "summary": f"{len(res)} resultados para «{q}»", "query": q,
                "sources": [{"title": r["title"], "url": r["url"], "snippet": r["snippet"], "kind": "web"} for r in res],
                "content": content + "\n\nOpen the most relevant results with fetch_url before answering."}

    def tool_fetch_url(self, call_id: str, args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        try:
            page = web.read_page(url)
        except web.WebError as e:
            raise AgentError(str(e))
        text = page["text"]
        if not text:
            raise AgentError("La página no tiene texto legible")
        q = str(args.get("query") or "")
        limit = TOOL_RESULT_CHARS
        if len(text) > limit and q:
            words = set(re.findall(r"\w{3,}", q.lower()))
            paras = [p for p in re.split(r"\n(?=#)|\n\n", text) if p.strip()]
            scored = sorted(range(len(paras)), key=lambda i: -sum(paras[i].lower().count(w) for w in words))
            pick, size = set(), 0
            for i in scored:
                if size + len(paras[i]) > limit:
                    continue
                pick.add(i)
                size += len(paras[i])
            text = "\n\n".join(paras[i] for i in sorted(pick))
        elif len(text) > limit:
            text = text[:limit] + "\n[... página recortada]"
        title = page["title"] or url
        return {"ok": True, "summary": f"{title[:90]} ({len(page['text']):,} caracteres)".replace(",", "."),
                "sources": [{"title": title, "url": page["url"], "kind": "web", "snippet": page["text"][:240]}],
                "content": f"# {title}\nURL: {page['url']}\n\n{text}"}

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
        es = self.t.lang != "en"
        sys_lines = [prof["instructions"], f"{'Fecha' if es else 'Date'}: {_now_str()}.",
                     "Trabajas para el asistente principal de Spark AI Hub, no hablas con el usuario. Devuelve solo el "
                     "resultado final (informe completo y conciso, en el idioma de la tarea)." if es else
                     "You work for the main Spark AI Hub assistant, not the user. Return only the final result."]
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
        steps: list[dict] = []
        sources: list[dict] = []
        text = ""
        inst = scheduler.ensure_loaded(self.t.model_id, wait_s=1800)
        scheduler.begin_request(inst.id)
        try:
            for step in range(SUB_MAX_STEPS):
                if self.stop():
                    raise AgentError("Cancelado por el usuario")
                self._fit(messages, 2048)
                last = step == SUB_MAX_STEPS - 1
                text, calls, _ = self._llm_step(messages, specs if specs and not last else None, 2048,
                                                thinking=False)
                if not calls and step == 0 and "web_search" in tools and (name == "investigador" or args.get("_must_search")):
                    # a researcher must look things up instead of answering from memory
                    messages += [{"role": "assistant", "content": text or ""},
                                 {"role": "user", "content": "Antes de responder busca con web_search y lee las mejores "
                                                             "fuentes con fetch_url; cita cada dato con su URL."}]
                    continue
                if not calls:
                    break
                assistant_msg, results = self._calls_to_msg(text, calls)
                messages.append(assistant_msg)
                for cid, tname, targs in results:
                    if tname not in tools:
                        res = {"ok": False, "summary": f"{tname} no disponible", "content": f"Tool {tname} not available"}
                    else:
                        what = targs.get("url") if tname == "fetch_url" else targs.get("query")
                        self.emit({"type": "tool_progress", "id": call_id, "message": f"{name}: {TOOL_LABELS.get(tname, tname)}"
                                   + (f" «{str(what)[:70]}»" if what else "")})
                        res = self._exec(cid, tname, targs)
                    steps.append({"name": tname, "label": TOOL_LABELS.get(tname, tname), "ok": res.get("ok", True),
                                  "summary": str(res.get("summary", ""))[:200]})
                    sources += [x for x in res.get("sources") or [] if x.get("url")]
                    messages.append({"role": "tool", "tool_call_id": cid,
                                     "content": str(res.get("content") or res.get("summary"))[:TOOL_RESULT_CHARS]})
        finally:
            scheduler.end_request(inst.id)
        text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip() or "(el agente no devolvió texto)"
        seen, uniq = set(), []
        for x in sources:
            if x["url"] not in seen:
                seen.add(x["url"])
                uniq.append(x)
        return {"ok": True, "summary": f"{name}: {len(steps)} pasos" + (f", {len(uniq)} fuentes" if uniq else ""),
                "agent": {"name": name, "task": task[:500], "steps": steps, "report": text[:20000]},
                "sources": uniq[:15], "content": f"Report from agent '{name}':\n{text[:16000]}"}

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
                                  project_id=self.project_id)
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
        with SessionLocal() as db:
            ws = db.merge(self.workspace)
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
            res = svc.run(db, ws, cmd, timeout_s=int(t) if isinstance(t, int) else None)
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
        if name in ("web_search", "fetch_url"):
            return self.flags["web"]
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
        return True

    def _all_specs(self) -> list[dict]:
        specs = TOOLS + WEB_TOOLS + SKILL_TOOLS + CODE_TOOLS + CANVAS_TOOLS + RESEARCH_TOOLS + TASK_TOOLS + WORKSPACE_TOOLS
        if self.agents:
            specs = specs + [_delegate_spec(self.agents)]
        return specs

    def tool_specs(self) -> list[dict]:
        return [t for t in self._all_specs() if self._tool_allowed(t["function"]["name"])]

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
        self.emit({"type": "tool_start", "id": call_id, "name": name, "label": TOOL_LABELS.get(name, name),
                   "args": {k: v for k, v in args.items() if k not in ("sql", "context")} | ({"sql": args["sql"]} if "sql" in args else {})})
        res = self._exec(call_id, name, args)
        public = {k: v for k, v in res.items() if k != "content"}
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

    def _llm_step(self, messages: list[dict], specs: list[dict] | None, max_tokens: int, thinking: bool | None = None,
                  on_delta=None, on_reasoning=None, usage: dict | None = None) -> tuple[str, dict, str | None]:
        body = {"messages": messages, **{k: v for k, v in self.t.params.items() if k not in ("aspect", "max_tokens")},
                "max_tokens": max_tokens, **({"tools": specs} if specs else {})}
        if thinking is not None and self.model.backend == "vllm":
            body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        payload = llm.sanitize_chat_payload(self.model, body)
        calls: dict[int, dict] = {}
        out: list[str] = []
        finish = None
        for chunk in llm.stream_json(self.model, payload, cancel_check=self.stop):
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
                if piece:
                    out.append(piece)
                    if on_delta:
                        on_delta(piece)
                for tc in d.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["name"] += fn.get("name") or ""
                    c["args"] += fn.get("arguments") or ""
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        return "".join(out), calls, finish

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
            self.refs = image_refs(db, conv.id, files)
            self.ctx = effective_context(model, self.t.context_limit)
            tools_on = self.t.use_tools and llm.supports_tools(model) and self.t.mode == "chat"
            self.flags = {"web": tools_on and self.t.web and bool(get_setting(db, "web_search_enabled")),
                          "skills": tools_on and bool(get_setting(db, "skills_enabled")),
                          "agents": tools_on and bool(get_setting(db, "agents_enabled")),
                          "auto_install": bool(get_setting(db, "skills_auto_install")),
                          "code": tools_on and bool(get_setting(db, "code_exec_enabled"))}
            self.project_id = conv.project_id
            wid = self.t.workspace_id or (conv.settings or {}).get("workspace_id")
            if wid and tools_on:
                from ..models import Workspace

                ws = db.get(Workspace, uuid.UUID(str(wid)))
                if ws and ws.owner_id == user.id:
                    db.expunge(ws)
                    self.workspace = ws
            self.agents = agent_profiles(db, user) if self.flags["agents"] else {}
            if not self.flags["web"]:
                self.agents.pop("investigador", None)
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
                hist = [m for m in prior_messages(db, self.t.conversation_id) if summary_upto is None or m.id > summary_upto]
                hist_tokens = sum(est_tokens(_message_text(m)) for m in hist)
            if hist_tokens > avail * 0.5 and len(hist) >= 3:
                self.emit({"type": "status", "status": "compacting"})
                cid = f"call_{uuid.uuid4().hex[:12]}"
                self.emit({"type": "tool_start", "id": cid, "name": "compact_context", "label": TOOL_LABELS["compact_context"]})
                try:
                    info = compact_conversation(self.t.conversation_id, self.t.model_id, self.ctx, budget=avail)
                except (EngineError, scheduler.SchedulingError) as e:
                    info = None
                    self.emit({"type": "warning", "message": f"No se pudo compactar el contexto: {e.message}"})
                if info:
                    res = {"ok": True, "summary": f"{info['summarized_now']} mensajes antiguos resumidos "
                                                  f"(≈{info['tokens_before']:,} → {info['tokens_after']:,} tokens)".replace(",", "."),
                           "compaction": info}
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
                               "summary": "No hacía falta compactar"})
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

        def on_delta(piece):
            if first["t"] is None:
                first["t"] = time.perf_counter() - t0
            self.emit({"type": "delta", "content": piece})

        def on_reasoning(piece):
            if first["t"] is None:
                first["t"] = time.perf_counter() - t0
            reasoning.append(piece)
            self.emit({"type": "reasoning", "content": piece})

        step = 0
        retried = False
        steps_max = WORKSPACE_MAX_STEPS if self.workspace is not None else MAX_STEPS
        for step in range(steps_max):
            self.emit({"type": "status", "status": "loading" if self.inst is None else "generating"})
            if self.inst is None:
                self.inst = scheduler.ensure_loaded(self.t.model_id, wait_s=1800)
                scheduler.begin_request(self.inst.id)
            self.emit({"type": "status", "status": "generating", "load_seconds": self.inst.load_seconds})
            fit_tokens = self._fit(messages, max_tokens)
            chars = sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in messages)
            try:
                text, calls, finish = self._llm_step(messages, specs if step < steps_max - 1 else None, fit_tokens,
                                                     self.t.thinking, on_delta, on_reasoning, usage_total)
            except EngineError as e:
                if not retried and re.search(r"context length|maximum context|too long|max_tokens|exceed", e.message, re.I):
                    retried = True  # the estimate was too optimistic: be stricter and retry once
                    self.ratio = max(1.5, self.ratio * 0.7)
                    self.emit({"type": "warning", "message": "El mensaje no cabía en el contexto; se ha recortado y reintentado."})
                    continue
                raise
            if usage_total.get("last_prompt"):  # calibrate chars/token on this conversation
                self.ratio = max(1.5, min(6.0, chars / max(1, usage_total["last_prompt"])))
                self.emit({"type": "context", "used": usage_total["last_prompt"] + usage_total.get("last_completion", 0),
                           "limit": self.ctx})
            text_out.append(text)
            if self.stop() or not calls:
                break
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
                res = outputs.get(cid) or self.run_tool(cid, name, args)
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
        total = time.perf_counter() - t0
        final = "\n\n".join(x for x in text_out if x.strip())
        ct = usage_total["completion_tokens"]
        ttft = first["t"]
        decode = total - (ttft or 0)
        metrics = {"ttft_ms": round(ttft * 1000, 1) if ttft else None, "latency_ms": round(total * 1000, 1),
                   "prompt_tokens": usage_total["prompt_tokens"] or None, "completion_tokens": ct or None,
                   "tokens_per_s": round((ct - 1) / decode, 2) if ct and ct > 1 and decode > 0 and not self.parts else None,
                   "token_count_source": "engine_usage", "finish_reason": finish, "steps": step + 1,
                   "tools": [p["name"] for p in self.parts],
                   "context_used": (usage_total.get("last_prompt") or 0) + (usage_total.get("last_completion") or 0) or None,
                   "context_limit": self.ctx}
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
                "parts": self.parts, "metrics": {"latency_ms": round(total * 1000, 1), "tools": [p["name"] for p in self.parts],
                                                 "mode": "image"}}


def run_turn(turn: Turn, emit: Callable[[dict], None], stop: Callable[[], bool]) -> dict:
    r = Runner(turn, emit, stop)
    try:
        return r.run()
    except (scheduler.SchedulingError, EngineError) as e:
        raise AgentError(e.message, getattr(e, "remediation", None))
    finally:
        r._release_llm()
