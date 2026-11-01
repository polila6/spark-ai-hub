"""Usage panel: tokens by day/model/source/project, GPU jobs, storage and quotas (one user, or everyone for managers)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from ..models import ApiUsage, Conversation, Job, JobStatus, Message, Project, User
from . import storage

JOB_GROUPS = {"image_generate": "Imagen", "video_generate": "Vídeo", "audio_transcribe": "Audio", "audio_tts": "Audio",
              "benchmark": "Benchmarks", "sci_infer": "Laboratorio", "sci_train": "Laboratorio", "kb_index": "Conocimiento",
              "file_process": "Conocimiento", "connector_sync": "Conocimiento", "hf_download": "Descargas",
              "ollama_pull": "Descargas", "comparison": "Comparaciones", "smoke_test": "Pruebas"}


def report(db: Session, user_ids: list[uuid.UUID] | None, days: int = 30) -> dict:
    """user_ids None = everyone."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def scoped(q, col):
        return q.where(col.in_(user_ids)) if user_ids is not None else q

    tok = ApiUsage.prompt_tokens + ApiUsage.completion_tokens
    base = scoped(select(func.count(), func.coalesce(func.sum(ApiUsage.prompt_tokens), 0),
                         func.coalesce(func.sum(ApiUsage.completion_tokens), 0)).where(ApiUsage.ts >= since), ApiUsage.user_id)
    n, pt, ct = db.execute(base).one()
    month_tokens = db.scalar(scoped(select(func.coalesce(func.sum(tok), 0)).where(ApiUsage.ts >= month), ApiUsage.user_id)) or 0
    day = func.date_trunc("day", ApiUsage.ts)
    daily = [{"day": d.date().isoformat(), "prompt": int(a or 0), "completion": int(b or 0), "requests": r}
             for d, a, b, r in db.execute(scoped(select(day, func.sum(ApiUsage.prompt_tokens), func.sum(ApiUsage.completion_tokens),
                                                        func.count()).where(ApiUsage.ts >= since), ApiUsage.user_id)
                                          .group_by(day).order_by(day))]
    by_model = [{"model": m or "—", "tokens": int(t or 0), "requests": r}
                for m, t, r in db.execute(scoped(select(ApiUsage.model, func.sum(tok), func.count()).where(ApiUsage.ts >= since),
                                                 ApiUsage.user_id).group_by(ApiUsage.model).order_by(func.sum(tok).desc()).limit(12))]
    by_source = [{"source": s, "tokens": int(t or 0), "requests": r}
                 for s, t, r in db.execute(scoped(select(ApiUsage.source, func.sum(tok), func.count()).where(ApiUsage.ts >= since),
                                                  ApiUsage.user_id).group_by(ApiUsage.source))]
    # chats per project: tokens recorded on the assistant messages
    mtok = (cast(Message.metrics["prompt_tokens"].astext, Integer) + cast(Message.metrics["completion_tokens"].astext, Integer))
    pq = (select(Conversation.project_id, func.coalesce(func.sum(mtok), 0), func.count(Message.id))
          .join(Conversation, Conversation.id == Message.conversation_id)
          .where(Message.role == "assistant", Message.created_at >= since, Message.metrics["prompt_tokens"].astext.is_not(None)))
    pq = scoped(pq, Conversation.owner_id).group_by(Conversation.project_id)
    names = {p.id: p.name for p in db.scalars(select(Project))}
    by_project = sorted([{"project": names.get(pid, "Sin proyecto") if pid else "Sin proyecto", "tokens": int(t or 0), "answers": c}
                         for pid, t, c in db.execute(pq)], key=lambda x: -x["tokens"])[:12]
    secs = func.extract("epoch", Job.finished_at - Job.started_at)
    jq = scoped(select(Job.type, func.count(), func.coalesce(func.sum(secs), 0)).where(
        Job.created_at >= since, Job.status == JobStatus.completed, Job.started_at.is_not(None)), Job.owner_id).group_by(Job.type)
    jobs: dict[str, dict] = {}
    for t, c, s in db.execute(jq):
        g = JOB_GROUPS.get(t, "Otros")
        jobs.setdefault(g, {"group": g, "count": 0, "minutes": 0.0})
        jobs[g]["count"] += c
        jobs[g]["minutes"] += float(s or 0) / 60
    out = {"days": days, "totals": {"requests": n, "prompt_tokens": int(pt), "completion_tokens": int(ct),
                                    "month_tokens": int(month_tokens)},
           "daily": daily, "by_model": by_model, "by_source": by_source, "by_project": by_project,
           "jobs": sorted(({**v, "minutes": round(v["minutes"], 1)} for v in jobs.values()), key=lambda x: -x["minutes"])}
    if user_ids is not None and len(user_ids) == 1:
        u = db.get(User, user_ids[0])
        pol = storage.effective_policy(db, u)
        out["storage"] = {"used_bytes": storage.user_usage_bytes(db, u.id), "quota_gb": pol.storage_quota_gb}
        out["quota"] = {"monthly_tokens": pol.monthly_token_quota}
    if user_ids is None:
        rows = db.execute(select(ApiUsage.user_id, func.count(), func.coalesce(func.sum(tok), 0)).where(ApiUsage.ts >= since)
                          .group_by(ApiUsage.user_id).order_by(func.sum(tok).desc()).limit(50)).all()
        users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r[0] for r in rows if r[0]])))}
        out["users"] = [{"user_id": str(uid) if uid else None, "username": users[uid].username if uid in users else "—",
                         "display_name": users[uid].display_name if uid in users else "—", "requests": r, "tokens": int(t)}
                        for uid, r, t in rows]
    return out
