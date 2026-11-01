"""Background chat turns: a turn keeps running when the browser leaves the page.

Events of the running turn are appended to a Redis list; any page (or tab, or the Android app) can attach to the
conversation and replay them from the start, then follow live. Cancelling is explicit (stop button), never implied by
a closed connection. Keys expire, so a crashed backend leaves nothing behind.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from ..redis_client import get_redis

TTL_S = 2 * 3600
DONE_KEEP_S = 600  # finished turns stay replayable for 10 min (late tabs see the end, then reload from the DB)
BEAT_EVERY_S = 15  # the process running a turn refreshes a heartbeat key while its thread is alive
BEAT_TTL_S = 75    # no heartbeat for this long = the process died (backend restart, crash): the turn is dead


def _k(cid, suffix: str = "") -> str:
    return f"sah:turn:{cid}{suffix}"


def active(cid) -> dict | None:
    """Turn info; a "running" turn whose process stopped beating is closed as interrupted here."""
    r = get_redis()
    raw = r.get(_k(cid))
    if not raw:
        return None
    info = json.loads(raw)
    if info.get("status") == "running" and not r.exists(_k(cid, ":beat")):
        started = datetime.fromisoformat(info["started_at"])
        if (datetime.now(timezone.utc) - started).total_seconds() > BEAT_TTL_S:
            info.update(status="interrupted", finished_at=datetime.now(timezone.utc).isoformat())
            r.set(_k(cid), json.dumps(info), ex=DONE_KEEP_S)
            r.rpush(_k(cid, ":ev"), json.dumps({"type": "error", "message": "Esta respuesta se interrumpió (el servidor "
                                                "se reinició). Vuelve a enviar el mensaje."}, ensure_ascii=False))
            r.expire(_k(cid, ":ev"), DONE_KEEP_S)
            if info.get("user_id"):
                r.srem(f"sah:turns:user:{info['user_id']}", str(cid))
    return info


def heartbeat(cid):
    """Start a daemon thread that keeps the turn's heartbeat alive; call the returned function to stop it."""
    import threading

    stop = threading.Event()
    r = get_redis()

    def loop():
        while not stop.is_set():
            try:
                r.set(_k(cid, ":beat"), "1", ex=BEAT_TTL_S)
            except Exception:  # noqa: BLE001  (Redis hiccup: the next beat retries)
                pass
            stop.wait(BEAT_EVERY_S)

    threading.Thread(target=loop, daemon=True, name=f"beat-{str(cid)[:8]}").start()

    def done():
        stop.set()
        try:
            r.delete(_k(cid, ":beat"))
        except Exception:  # noqa: BLE001
            pass
    return done


def start(cid, user_id, title: str) -> str:
    r = get_redis()
    tid = uuid.uuid4().hex
    info = {"id": tid, "conversation_id": str(cid), "user_id": str(user_id), "title": title[:120], "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat()}
    if not r.set(_k(cid), json.dumps(info), ex=TTL_S, nx=True):
        cur = active(cid)  # also closes a dead turn, so it does not block the chat
        if cur and cur.get("status") == "running":
            raise RuntimeError("busy")
        r.set(_k(cid), json.dumps(info), ex=TTL_S)
    r.delete(_k(cid, ":ev"), _k(cid, ":cancel"))
    r.set(_k(cid, ":beat"), "1", ex=BEAT_TTL_S)
    r.sadd(f"sah:turns:user:{user_id}", str(cid))
    r.expire(f"sah:turns:user:{user_id}", TTL_S)
    return tid


def emit(cid, ev: dict) -> None:
    r = get_redis()
    r.rpush(_k(cid, ":ev"), json.dumps(ev, ensure_ascii=False, default=str))
    r.expire(_k(cid, ":ev"), TTL_S)
    if ev.get("type") in ("status", "tool_start", "tool_result", "done", "error"):
        cur = active(cid)
        if cur:
            cur["last"] = ev.get("label") or ev.get("status") or ev.get("type")
            r.set(_k(cid), json.dumps(cur), ex=TTL_S)


def finish(cid, user_id, status: str) -> None:
    r = get_redis()
    r.delete(_k(cid, ":beat"))
    cur = active(cid) or {}
    cur.update(status=status, finished_at=datetime.now(timezone.utc).isoformat())
    r.set(_k(cid), json.dumps(cur), ex=DONE_KEEP_S)
    r.expire(_k(cid, ":ev"), DONE_KEEP_S)
    r.srem(f"sah:turns:user:{user_id}", str(cid))


def cancel(cid) -> None:
    get_redis().set(_k(cid, ":cancel"), "1", ex=TTL_S)


def forget(cid, user_id) -> None:
    """The conversation was deleted: stop its turn and drop it from the indicator right away."""
    r = get_redis()
    cancel(cid)
    r.srem(f"sah:turns:user:{user_id}", str(cid))
    cur = active(cid)
    if cur and cur.get("status") == "running":
        cur.update(status="cancelled", finished_at=datetime.now(timezone.utc).isoformat())
        r.set(_k(cid), json.dumps(cur), ex=DONE_KEEP_S)


def cancelled_checker(cid):
    """Cheap stop() for the agent: checks the Redis flag at most once per second."""
    state = {"t": 0.0, "v": False}

    def check() -> bool:
        if state["v"]:
            return True
        now = time.time()
        if now - state["t"] >= 1.0:
            state["t"] = now
            state["v"] = bool(get_redis().exists(_k(cid, ":cancel")))
        return state["v"]

    return check


def events(cid, start_at: int = 0, limit: int = 500) -> list[dict]:
    raw = get_redis().lrange(_k(cid, ":ev"), start_at, start_at + limit - 1)
    return [json.loads(x) for x in raw]


def running_for_user(user_id) -> list[dict]:
    r = get_redis()
    out = []
    for cid in r.smembers(f"sah:turns:user:{user_id}"):
        cid = cid.decode() if isinstance(cid, bytes) else cid
        info = active(cid)
        if info and info.get("status") == "running":
            out.append(info)
        else:
            r.srem(f"sah:turns:user:{user_id}", cid)
    return sorted(out, key=lambda x: x["started_at"])
