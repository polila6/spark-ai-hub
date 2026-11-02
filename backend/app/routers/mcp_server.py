"""MCP server (Model Context Protocol, Streamable HTTP transport, stateless JSON responses) at /mcp.

Gives coding agents and desktop clients (Claude Code, OpenCode, Cursor, Claude Desktop via mcp-remote...) access to
the user's personal cloud: knowledge search, files, data queries, chats, projects, memory and image generation.
Authentication: `Authorization: Bearer sah-...` (API key with the "inference" scope); every tool runs as the key owner.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..db import SessionLocal
from ..deps import Principal, api_key_principal
from ..models import (Conversation, Job, JobStatus, Memory, Message, ModelStatus, Project, RegisteredModel, SchedClass,
                      User, UserFile)
from ..services import dataq, kb, workflows
from ..services import files as files_svc
from ..services.jobs import JobError, create_job

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "spark-ai-hub", "title": "Spark AI Hub", "version": __import__("app").__version__}
INSTRUCTIONS = ("Personal private cloud of the user on their DGX Spark. Use search_knowledge for questions about the "
                "user's documents, list_files/read_file for their drive, describe_data/query_data for CSV/Excel/SQLite "
                "files, search_chats/get_chat for previous conversations, list_memories/save_memory for durable facts "
                "and generate_image to create images with the local image model.")


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


TOOLS = [
    {"name": "search_knowledge", "title": "Search my documents",
     "description": "Hybrid semantic + keyword search over the user's knowledge base (personal and project collections). "
                    "Returns passages with file name and page.",
     "inputSchema": _schema({"query": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "list_files", "title": "List my files",
     "description": "List files in the user's drive (id, name, folder, kind, size, text status).",
     "inputSchema": _schema({"folder": {"type": "string"}, "kind": {"type": "string", "enum": ["document", "image", "data", "audio", "other"]},
                             "query": {"type": "string", "description": "substring of the file name"},
                             "limit": {"type": "integer", "maximum": 200}}),
     "annotations": {"readOnlyHint": True}},
    {"name": "read_file", "title": "Read a file",
     "description": "Extracted text of a document (PDF, Word, PowerPoint, Markdown, code...) or an image of the drive.",
     "inputSchema": _schema({"file_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                             "max_chars": {"type": "integer", "maximum": 200000}}, ["file_id"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "describe_data", "title": "Describe a data file",
     "description": "Tables, columns, types and sample rows of a CSV/TSV/Excel/Parquet/JSON/SQLite file.",
     "inputSchema": _schema({"file_id": {"type": "string"}}, ["file_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "query_data", "title": "Query a data file with SQL",
     "description": "Run one SQL query (DuckDB; SQLite dialect for .sqlite/.db) on a data file; up to 200 rows.",
     "inputSchema": _schema({"file_id": {"type": "string"}, "sql": {"type": "string"}}, ["file_id", "sql"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "search_chats", "title": "Search my chats",
     "description": "Full-text search over the user's previous web chats; returns conversation ids and snippets.",
     "inputSchema": _schema({"query": {"type": "string"}}, ["query"]), "annotations": {"readOnlyHint": True}},
    {"name": "get_chat", "title": "Read a chat",
     "description": "Messages of one of the user's conversations (last N messages).",
     "inputSchema": _schema({"conversation_id": {"type": "string"}, "last_n": {"type": "integer", "maximum": 200}},
                            ["conversation_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "list_projects", "title": "List my projects",
     "description": "Projects (workspaces) the user owns or that are shared with them, with their instructions.",
     "inputSchema": _schema({}), "annotations": {"readOnlyHint": True}},
    {"name": "list_memories", "title": "List memories",
     "description": "Facts and preferences the user asked the assistant to remember.",
     "inputSchema": _schema({}), "annotations": {"readOnlyHint": True}},
    {"name": "save_memory", "title": "Save a memory",
     "description": "Store a short durable fact or preference about the user (visible and editable in the web UI).",
     "inputSchema": _schema({"content": {"type": "string"}}, ["content"])},
    {"name": "list_models", "title": "List hub models",
     "description": "Models installed on the hub that this user can use (alias, backend, capabilities, context).",
     "inputSchema": _schema({}), "annotations": {"readOnlyHint": True}},
    {"name": "generate_image", "title": "Generate an image",
     "description": "Generate an image with the local image model (Qwen-Image or SD 1.5). Waits for the result and "
                    "returns the image and its URL in the hub (also saved in the user's media).",
     "inputSchema": _schema({"prompt": {"type": "string"},
                             "aspect": {"type": "string", "enum": ["square", "landscape", "portrait", "wide", "tall"]}},
                            ["prompt"])},
]


class ToolError(Exception):
    pass


def _text(obj) -> dict:
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str, indent=1)}


def _own_or_shared_file(db, user: User, fid: str) -> UserFile:
    try:
        f = db.get(UserFile, uuid.UUID(fid))
    except ValueError:
        f = None
    if f and f.owner_id != user.id:
        from ..models import KbDocument

        if not db.scalar(select(KbDocument.id).where(KbDocument.file_id == f.id,
                                                     KbDocument.collection_id.in_(kb.readable_collection_ids(db, user)))):
            f = None
    if not f:
        raise ToolError(f"File {fid} not found")
    return f


def call_tool(user_id: uuid.UUID, name: str, args: dict) -> list[dict]:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if name == "search_knowledge":
            res = kb.search(db, user, str(args.get("query", "")), None, int(args.get("k") or 8))
            if not res["results"]:
                return [_text("No results.")]
            return [_text("\n\n".join(f"[{i + 1}] {r['filename']}" + (f", p. {r['page']}" if r["page"] else "")
                                      + f" (file_id={r['file_id']}, collection={r['collection']})\n{r['text']}"
                                      for i, r in enumerate(res["results"])) + f"\n\n(mode: {res['mode']})")]
        if name == "list_files":
            q = select(UserFile).where(UserFile.owner_id == user.id).order_by(UserFile.created_at.desc())
            if args.get("folder") is not None:
                q = q.where(UserFile.folder == args["folder"])
            if args.get("kind"):
                q = q.where(UserFile.kind == args["kind"])
            if args.get("query"):
                q = q.where(UserFile.filename.ilike(f"%{str(args['query']).replace('%', '')}%"))
            rows = db.scalars(q.limit(min(int(args.get("limit") or 100), 200)))
            return [_text([{"file_id": str(f.id), "name": f.filename, "folder": f.folder, "kind": f.kind,
                            "size_bytes": f.size_bytes, "text": f.text_status, "created_at": f.created_at} for f in rows])]
        if name == "read_file":
            f = _own_or_shared_file(db, user, str(args.get("file_id", "")))
            if f.kind == "image":
                data = files_svc.abs_path(f).read_bytes()
                return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": f.mime},
                        _text(f"{f.filename} ({f.size_bytes} bytes)")]
            txt = files_svc.read_text(f)
            if not txt:
                raise ToolError(f"{f.filename} has no extracted text (status: {f.text_status})")
            off = int(args.get("offset") or 0)
            n = int(args.get("max_chars") or 50000)
            chunk = txt[off:off + n]
            more = f"\n\n[… {len(txt) - off - n} more characters; call again with offset={off + n}]" if off + n < len(txt) else ""
            return [_text(f"# {f.filename}\n\n{chunk}{more}")]
        if name in ("describe_data", "query_data"):
            f = _own_or_shared_file(db, user, str(args.get("file_id", "")))
            try:
                res = dataq.describe(f) if name == "describe_data" else dataq.query(f, str(args.get("sql", "")))
            except dataq.DataError as e:
                raise ToolError(str(e))
            return [_text(res if name == "describe_data" else dataq.as_markdown(res, 200))]
        if name == "search_chats":
            from .playground import search_conversations

            class _P:  # minimal principal for the shared search function
                id = user.id

            return [_text(search_conversations(str(args.get("query", "")), _P(), db))]  # type: ignore[arg-type]
        if name == "get_chat":
            try:
                c = db.get(Conversation, uuid.UUID(str(args.get("conversation_id"))))
            except ValueError:
                c = None
            if not c or c.owner_id != user.id:
                raise ToolError("Conversation not found")
            n = min(int(args.get("last_n") or 50), 200)
            msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id.desc()).limit(n)).all()
            return [_text(f"# {c.title}\n\n" + "\n\n".join(f"**{m.role}**: {m.content}" for m in reversed(msgs)))]
        if name == "list_projects":
            from ..models import SharedResource

            shared = select(SharedResource.resource_id).where(SharedResource.resource_type == "project",
                                                              SharedResource.grantee_id == user.id)
            rows = db.scalars(select(Project).where((Project.owner_id == user.id) | Project.id.in_(shared)))
            return [_text([{"project_id": str(p.id), "name": p.name, "description": p.description,
                            "instructions": p.instructions[:4000]} for p in rows])]
        if name == "list_memories":
            rows = db.scalars(select(Memory).where(Memory.owner_id == user.id).order_by(Memory.created_at))
            return [_text([m.content for m in rows] or "No memories.")]
        if name == "save_memory":
            content = str(args.get("content", "")).strip()[:500]
            if not content:
                raise ToolError("content is empty")
            db.add(Memory(owner_id=user.id, content=content, source="mcp"))
            db.commit()
            return [_text(f"Saved: {content}")]
        if name == "list_models":
            from ..deps import visible_filter

            p = Principal(user=user, via="api_key")
            q = select(RegisteredModel).where(RegisteredModel.status.in_([ModelStatus.installed, ModelStatus.ready]))
            if not p.is_admin:
                q = q.where(visible_filter(RegisteredModel, p, "model"))
            return [_text([{"alias": m.alias, "backend": m.backend, "capabilities": m.capabilities,
                            "status": m.status.value} for m in db.scalars(q)])]
        if name == "generate_image":
            from ..services.agent import ASPECTS, pick_workflow

            wf_id = pick_workflow(db, "txt2img")
            if not wf_id:
                raise ToolError("No image model is installed on the hub")
            wf = workflows.WORKFLOWS[wf_id]
            w, h = ASPECTS["qwen_image" if wf.family == "qwen_image" else "sd15"].get(args.get("aspect") or "square")
            params = workflows.validate(wf, {"prompt": str(args.get("prompt", ""))[:3000], "width": w, "height": h})
            try:
                j = create_job(db, user, "image_generate", {"workflow_id": wf_id, "inputs": params},
                               title=f"MCP: {params['prompt'][:60]}", sched_class=SchedClass.interactive,
                               requested_resources={"memory_gb": workflows.estimate_memory(wf, params)})
            except JobError as e:
                raise ToolError(e.message)
            jid = j.id
    t0 = time.time()
    while time.time() - t0 < 1500:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled, JobStatus.interrupted):
                break
        time.sleep(1.5)
    if job.status != JobStatus.completed:
        raise ToolError(f"Image generation {job.status.value}: {job.error or ''}")
    art = next(a for a in job.result["artifacts"] if a["kind"] == "image")
    from ..config import get_settings
    from ..models import Artifact

    with SessionLocal() as db:
        a = db.get(Artifact, uuid.UUID(art["id"]))
        data = (get_settings().data_root / a.path).read_bytes()
    url = f"{get_settings().public_base_url}{art['url']}"
    return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": art["mime"]},
            _text(f"Image generated with {wf_id} in {job.result['meta']['generation_seconds']} s: {url}")]


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


async def _handle(msg: dict, user_id: uuid.UUID) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized) or a response from the client
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        version = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": version,
                                                        "capabilities": {"tools": {"listChanged": False}},
                                                        "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method in ("resources/list", "resources/templates/list", "prompts/list"):
        key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[method]
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return _rpc_error(mid, -32602, f"Unknown tool: {name}")
        try:
            content = await asyncio.to_thread(call_tool, user_id, name, params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": False}}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [_text(f"Error: {e}")], "isError": True}}
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [_text(f"Error: {type(e).__name__}: {e}")],
                                                            "isError": True}}
    return _rpc_error(mid, -32601, f"Method not found: {method}")


def _authenticate(request: Request) -> tuple[uuid.UUID | None, Response | None]:
    """(owner id, None) for a valid API key with the "inference" scope, else (None, 401/403 response)."""
    from fastapi import HTTPException

    with SessionLocal() as db:
        try:
            p = api_key_principal(request, db, scope="inference")
        except HTTPException as e:
            d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            return None, JSONResponse(status_code=e.status_code, content=_rpc_error(None, -32001, d.get("message", "unauthorized")),
                                      headers={"WWW-Authenticate": 'Bearer realm="spark-ai-hub"'})
        if p is None:
            return None, JSONResponse(status_code=401, content=_rpc_error(None, -32001, "Missing API key (Authorization: Bearer sah-...)"),
                                      headers={"WWW-Authenticate": 'Bearer realm="spark-ai-hub"'})
        return p.user.id, None


@router.post("/mcp")
async def mcp_post(request: Request):
    uid, err = _authenticate(request)
    if err is not None:
        return err
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, "Parse error"))
    if isinstance(body, list):
        out = [r for r in [await _handle(m, uid) for m in body if isinstance(m, dict)] if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_rpc_error(None, -32600, "Invalid request"))
    res = await _handle(body, uid)
    return JSONResponse(res) if res is not None else Response(status_code=202)


# GET /mcp: the optional server-to-client SSE stream of the Streamable HTTP transport. This server never sends
# requests or notifications of its own (all responses go back on the POST), so the stream only carries keep-alive
# comments. Answering 405 is allowed by the spec, but some clients (OpenCode) log every 405 as an error; an idle stream
# that is closed cleanly with a reconnection hint keeps them quiet. Bounded in time so restarts are not held up.
SSE_KEEPALIVE_S = 15
SSE_MAX_S = 300
SSE_RETRY_MS = 5000
SSE_MAX_STREAMS = 64
_sse_streams = 0


def _server_exiting() -> bool:
    """True once uvicorn received SIGTERM/SIGINT (its handler is a bound method of the Server)."""
    import signal

    srv = getattr(signal.getsignal(signal.SIGTERM), "__self__", None)
    return bool(getattr(srv, "should_exit", False))


async def _sse_idle_stream(request: Request, prime: bool):
    global _sse_streams
    _sse_streams += 1
    try:
        # 2025-11-25: an event with an id and empty data primes the client to reconnect with Last-Event-ID. Older
        # clients try to parse every data field as JSON-RPC, so they only get the retry hint.
        yield (f"id: {uuid.uuid4().hex}\nretry: {SSE_RETRY_MS}\ndata:\n\n" if prime else f"retry: {SSE_RETRY_MS}\n\n")
        start = last = time.monotonic()
        while True:
            await asyncio.sleep(1)
            if _server_exiting() or await request.is_disconnected():
                return
            now = time.monotonic()
            if now - start >= SSE_MAX_S:
                return
            if now - last >= SSE_KEEPALIVE_S:
                last = now
                yield ": keep-alive\n\n"
    finally:
        _sse_streams -= 1


@router.get("/mcp")
async def mcp_get(request: Request):
    from fastapi.responses import StreamingResponse

    _uid, err = _authenticate(request)
    if err is not None:
        return err
    if "text/event-stream" not in request.headers.get("accept", "").lower():
        return JSONResponse(status_code=406, content=_rpc_error(None, -32600, "GET /mcp opens an SSE stream: send "
                                                                              "Accept: text/event-stream (requests go by POST)"))
    if _sse_streams >= SSE_MAX_STREAMS:
        return Response(status_code=405, headers={"Allow": "POST"})  # spec: "no SSE stream offered" for now
    prime = request.headers.get("mcp-protocol-version", "") >= "2025-11-25"
    return StreamingResponse(_sse_idle_stream(request, prime), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.delete("/mcp")
async def mcp_delete():
    # Stateless server: there is no session to terminate (the spec allows 405 here).
    return Response(status_code=405, headers={"Allow": "GET, POST"})
