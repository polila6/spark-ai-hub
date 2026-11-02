"""Background chat turns: a turn keeps running when the browser leaves the page.

Events of the running turn are appended to a Redis list; any page (or tab, or the Android app) can attach to the
conversation and replay them from the start, then follow live. Cancelling is explicit (stop button), never implied by
a closed connection. Keys expire, so a crashed backend leaves nothing behind.

Messages sent while a turn runs are not refused: the router stores them and pushes their ids to the turn's inbox
(`:inbox`), which the agent loop reads before every model step. The inbox only accepts messages while the `:accept`
flag exists; the agent closes it atomically when it is about to finish (`close_inbox`), so a message is either injected
into the running turn, answered by a follow-up turn, or (inbox closed) answered by a new turn of its own.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from ..redis_client import get_redis

TTL_S = 2 * 3600  # refreshed by the heartbeat: a turn may run for hours (workspace tasks) as long as its thread lives
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
            r.delete(_k(cid, ":accept"))
            if info.get("user_id"):
                r.srem(f"sah:turns:user:{info['user_id']}", str(cid))
    return info


def heartbeat(cid, user_id=None):
    """Start a daemon thread that keeps the turn's heartbeat alive; call the returned function to stop it.

    It also keeps every key of the turn from expiring (event log, inbox, the "chats working" set), so turns longer
    than TTL_S keep working and stay visible."""
    import threading

    stop = threading.Event()
    r = get_redis()

    def loop():
        while not stop.is_set():
            try:
                r.set(_k(cid, ":beat"), "1", ex=BEAT_TTL_S)
                r.expire(_k(cid), TTL_S)
                r.expire(_k(cid, ":ev"), TTL_S)
                r.expire(_k(cid, ":accept"), TTL_S)  # no-op once the inbox is closed
                r.expire(_k(cid, ":inbox"), TTL_S)
                if user_id is not None:
                    r.expire(f"sah:turns:user:{user_id}", TTL_S)
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


def start(cid, user_id, title: str, accept_messages: bool = True) -> str:
    r = get_redis()
    tid = uuid.uuid4().hex
    info = {"id": tid, "conversation_id": str(cid), "user_id": str(user_id), "title": title[:120], "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat()}
    if not r.set(_k(cid), json.dumps(info), ex=TTL_S, nx=True):
        cur = active(cid)  # also closes a dead turn, so it does not block the chat
        if cur and cur.get("status") == "running":
            raise RuntimeError("busy")
        r.set(_k(cid), json.dumps(info), ex=TTL_S)
    r.delete(_k(cid, ":ev"), _k(cid, ":cancel"), _k(cid, ":inbox"))
    if accept_messages:
        r.set(_k(cid, ":accept"), "1", ex=TTL_S)
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
    r.delete(_k(cid, ":beat"), _k(cid, ":accept"), _k(cid, ":inbox"))
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
    r.delete(_k(cid, ":accept"), _k(cid, ":inbox"))
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


# ------------------------------------------------------------------ inbox (messages sent while the turn runs)

_QUEUE_LUA = """
if redis.call('exists', KEYS[1]) == 1 then
  redis.call('rpush', KEYS[2], ARGV[1])
  redis.call('expire', KEYS[2], ARGV[2])
  return 1
end
return 0
"""
_CLOSE_LUA = """
local items = redis.call('lrange', KEYS[2], 0, -1)
if #items == 0 or ARGV[1] == '1' then redis.call('del', KEYS[1]) end
redis.call('del', KEYS[2])
return items
"""


def queue_message(cid, message_id: int) -> bool:
    """Hand a stored user message to the running turn. False = the turn is not accepting messages (none running, or
    it is finishing): the caller answers the message with a turn of its own."""
    return bool(get_redis().eval(_QUEUE_LUA, 2, _k(cid, ":accept"), _k(cid, ":inbox"), int(message_id), TTL_S))


def take_inbox(cid) -> list[int]:
    """Pop every queued message id (oldest first); the inbox stays open."""
    pipe = get_redis().pipeline(transaction=True)
    pipe.lrange(_k(cid, ":inbox"), 0, -1)
    pipe.delete(_k(cid, ":inbox"))
    raw, _ = pipe.execute()
    return inbox_order(raw)


def close_inbox(cid, final: bool = False) -> list[int]:
    """The agent is about to finish: atomically pop the queued ids and, when there are none (or `final`), stop
    accepting new ones. A non-empty result means the turn must go on with those messages."""
    raw = get_redis().eval(_CLOSE_LUA, 2, _k(cid, ":accept"), _k(cid, ":inbox"), "1" if final else "0")
    return inbox_order(raw)


def inbox_order(raw) -> list[int]:
    """Queued ids as ints, in arrival order, without duplicates (pure)."""
    out: list[int] = []
    for x in raw or []:
        try:
            i = int(x.decode() if isinstance(x, bytes) else x)
        except (TypeError, ValueError):
            continue
        if i not in out:
            out.append(i)
    return out


def followup_needed(leftover: list[int], status: str, cancelled: bool) -> bool:
    """Messages queued but never injected are answered by an automatic follow-up turn, unless the user pressed stop
    (they stay in the chat, unanswered, and can be answered with "regenerate")."""
    return bool(leftover) and not cancelled and status in ("done", "error")


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


def rewind_plan(msgs: list[tuple[int, str]], mid: int, summary_upto: int | None, edited: bool = False) -> dict:
    """What rewinding a chat to message `mid` changes (pure; the router applies it).

    `msgs` are the conversation's (id, role) in order. Every message after `mid` is deleted; `edited` means the text of
    `mid` itself changes too. The compaction summary covers the messages with id <= summary_upto, so it is stale (and must
    be dropped; the next turn re-compacts if the history is still too long) when it covers any changed message.
    """
    ids = [i for i, _ in msgs]
    if mid not in ids:
        raise KeyError(mid)
    delete = [i for i in ids if i > mid]
    first_changed = mid if edited else (delete[0] if delete else None)
    stale = summary_upto is not None and first_changed is not None and summary_upto >= first_changed
    return {"delete": delete, "clear_summary": stale, "role": dict(msgs)[mid]}
