"""Personal cloud jobs: text extraction, knowledge-base indexing, account export."""
from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from ...config import get_settings
from ...db import SessionLocal
from ...models import (Artifact, Conversation, KbCollection, KbDocument, Memory, Message, Project, SavedPrompt, User,
                       UserFile)
from ...services import files as files_svc
from ...services import kb
from ...services.notify import notify
from ..context import JobContext
from . import Handler, register


@register
class FileProcess(Handler):
    """Extract text from an uploaded file, then index it into the requested collections."""

    type = "file_process"

    def run(self, ctx: JobContext) -> None:
        fid = uuid.UUID(ctx.params["file_id"])
        with SessionLocal() as db:
            f = db.get(UserFile, fid)
            if f is None:
                ctx.set_result({"skipped": "file deleted"})
                return
            f.text_status = "pending"
            db.commit()
            try:
                res = files_svc.process(f)
            except Exception as e:  # noqa: BLE001 - parser errors on hostile/corrupt files must not crash the worker
                f.text_status = "failed"
                f.meta = {**(f.meta or {}), "extract_error": str(e)[:500]}
                db.commit()
                raise
            f.text_status = "ready" if res["status"] == "ready" else ("unsupported" if res["status"] == "unsupported" else "failed")
            f.text_chars = res["chars"]
            f.meta = {**(f.meta or {}), "pages": res["pages"]}
            if res["status"] == "empty":
                f.meta = {**f.meta, "extract_error": "no text layer (scanned document?)"}
            db.commit()
            ctx.progress(0.4, f"Text extracted: {res['chars']} chars", force=True)
            docs = list(db.scalars(select(KbDocument).where(KbDocument.file_id == fid)))
        indexed = 0
        for d in docs:
            with SessionLocal() as db:
                doc = db.get(KbDocument, d.id)
                if doc is None:
                    continue
                doc.status = "indexing"
                db.commit()
                try:
                    indexed += kb.index_document(db, doc, progress=lambda v: ctx.progress(0.4 + 0.6 * v))
                except kb.KbError as e:
                    doc.status, doc.error = "failed", e.message
                    db.commit()
        ctx.set_result({"text_chars": res["chars"], "pages": res["pages"], "chunks": indexed})


@register
class KbIndex(Handler):
    type = "kb_index"

    def run(self, ctx: JobContext) -> None:
        with SessionLocal() as db:
            ids = [uuid.UUID(x) for x in ctx.params["document_ids"]]
            total = 0
            for i, did in enumerate(ids):
                ctx.raise_if_cancelled()
                doc = db.get(KbDocument, did)
                if doc is None:
                    continue
                doc.status = "indexing"
                db.commit()
                try:
                    total += kb.index_document(db, doc)
                except kb.KbError as e:
                    doc.status, doc.error = "failed", e.message
                    db.commit()
                ctx.progress((i + 1) / len(ids), f"{i + 1}/{len(ids)} documents")
        ctx.set_result({"documents": len(ids), "chunks": total})


@register
class AccountExport(Handler):
    """Zip with everything the user owns: chats (JSON + Markdown), memories, projects, files and generated media."""

    type = "account_export"

    def run(self, ctx: JobContext) -> None:
        s = get_settings()
        out = ctx.job_dir() / f"spark-ai-hub-export-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
        with SessionLocal() as db, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            user = db.get(User, ctx.owner_id)
            z.writestr("account.json", json.dumps({"username": user.username, "email": user.email,
                                                   "display_name": user.display_name, "role": user.role.value,
                                                   "created_at": user.created_at.isoformat(),
                                                   "preferences": user.preferences}, ensure_ascii=False, indent=2))
            convs = db.scalars(select(Conversation).where(Conversation.owner_id == user.id)).all()
            for i, c in enumerate(convs):
                msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).all()
                data = {"id": str(c.id), "title": c.title, "model": c.model_alias, "created_at": c.created_at.isoformat(),
                        "messages": [{"role": m.role, "content": m.content, "model": m.model_alias,
                                      "attachments": m.attachments, "parts": m.parts,
                                      "created_at": m.created_at.isoformat()} for m in msgs]}
                z.writestr(f"chats/{i:04d}-{c.id}.json", json.dumps(data, ensure_ascii=False, indent=2))
                md = [f"# {c.title}", ""] + [f"**{m.role}**\n\n{m.content}\n" for m in msgs]
                z.writestr(f"chats/{i:04d}-{c.id}.md", "\n".join(md))
                ctx.progress(0.5 * (i + 1) / max(len(convs), 1), "Chats")
            z.writestr("memories.json", json.dumps([{"content": m.content, "created_at": m.created_at.isoformat()}
                                                    for m in db.scalars(select(Memory).where(Memory.owner_id == user.id))],
                                                   ensure_ascii=False, indent=2))
            z.writestr("projects.json", json.dumps([{"name": p.name, "description": p.description,
                                                     "instructions": p.instructions}
                                                    for p in db.scalars(select(Project).where(Project.owner_id == user.id))],
                                                   ensure_ascii=False, indent=2))
            z.writestr("collections.json", json.dumps([{"name": c.name, "description": c.description}
                                                       for c in db.scalars(select(KbCollection).where(KbCollection.owner_id == user.id))],
                                                      ensure_ascii=False, indent=2))
            z.writestr("prompts.json", json.dumps([{"title": p.title, "content": p.content, "system_prompt": p.system_prompt}
                                                   for p in db.scalars(select(SavedPrompt).where(SavedPrompt.owner_id == user.id))],
                                                  ensure_ascii=False, indent=2))
            for f in db.scalars(select(UserFile).where(UserFile.owner_id == user.id)):
                src = files_svc.abs_path(f)
                if src.exists():
                    z.write(src, f"files/{f.folder + '/' if f.folder else ''}{f.id}-{f.filename}")
            ctx.progress(0.8, "Files")
            for a in db.scalars(select(Artifact).where(Artifact.owner_id == user.id, Artifact.job_id != ctx.job_id)):
                src = s.data_root / a.path
                if src.exists() and a.kind != "export":
                    z.write(src, f"media/{a.kind}/{a.id}-{a.filename}")
        art = ctx.add_artifact(out, "export", {"generated_at": datetime.now(timezone.utc).isoformat()})
        notify_link = art["url"] + "?download=true"
        with SessionLocal() as db:
            notify(db, ctx.owner_id, "export", "Tu exportación de datos está lista",
                   "Descarga el ZIP con todos tus datos.", link=notify_link)
            db.commit()
        ctx.set_result({"artifact": art})


@register
class ConnectorSync(Handler):
    """Synchronise one knowledge connector (WebDAV, GitHub, Zotero, SMB) into its collection."""

    type = "connector_sync"

    def run(self, ctx: JobContext) -> None:
        from ...models import KbConnector
        from ...services import connectors

        with SessionLocal() as db:
            c = db.get(KbConnector, uuid.UUID(ctx.params["connector_id"]))
            if c is None or c.owner_id != ctx.owner_id:
                ctx.set_result({"skipped": "connector deleted"})
                return
            try:
                stats = connectors.sync(db, c, progress=lambda v, m: ctx.progress(v, m))
            except connectors.ConnectorError as e:
                db.rollback()
                c = db.get(KbConnector, c.id)
                c.status, c.status_message = "error", e.message
                c.last_sync_at = datetime.now(timezone.utc)
                db.commit()
                raise
            if stats.get("added") or stats.get("updated"):
                notify(db, c.owner_id, "connector", f"«{c.name}» sincronizado: {stats['added']} nuevos, {stats['updated']} "
                       f"actualizados", link="/knowledge?tab=connectors", email=False)
                db.commit()
        ctx.set_result(stats)
