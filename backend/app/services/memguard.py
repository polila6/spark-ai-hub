"""Memory safety for big model loads.

On the Spark the GPU memory IS the system memory, and loading a large vLLM model peaks well above what it holds afterwards
(a 70 GB model was seen at 117 of 121 GB used while loading). Anything else running then (a big download writing to disk,
the robot server's text encoder, an idle model not yet freed) can push the machine over the edge: the NVIDIA driver cannot
allocate, the kernel's OOM killer cannot see GPU memory, tasks block for minutes and the whole box stops answering.
This module is the hub's answer, in three parts:

  1. before a heavy load: pause the running Hugging Face downloads and free the robot server's memory, evict idle models
     (the scheduler already does), then require REAL free memory (MemAvailable) for the reservation plus a safety margin;
     refuse with a clear message instead of trying;
  2. downloads wait for a heavy load in progress (and for the first minute after the worker starts, so the resident model
     loads first after a reboot) and resume by themselves afterwards;
  3. a load that died for lack of memory is not retried automatically (it would just repeat the crisis every ten minutes).

The decisions are pure functions and unit tested; the rest talks to the database, Redis and the robot server.
"""
from __future__ import annotations

import time
from collections.abc import Callable

HEAVY_GB = 40.0  # a reservation at least this big (or a third of the machine) is a heavy load
HEAVY_SHARE = 0.33
DEFAULT_MARGIN_GB = 8.0  # real free memory required on top of the reservation: the OS, the desktop and the load's own peak
HEADROOM_WAIT_S = 120.0  # how long to wait for evicted models and stopped containers to give their memory back
QUIET_MAX_WAIT_S = 30 * 60.0
BOOT_GRACE_S = 60.0
OOM_TTL_S = 7 * 24 * 3600
HELD_KEY = "sah:memguard:held"
OOM_KEY = "sah:oom:{}"
_STARTED = time.monotonic()


# ------------------------------------------------------------------ decisions (pure)


def is_heavy(reservation_gb: float, total_gb: float) -> bool:
    return reservation_gb >= HEAVY_GB or (total_gb > 0 and reservation_gb >= HEAVY_SHARE * total_gb)


def headroom_verdict(available_gb: float, reservation_gb: float, margin_gb: float) -> tuple[bool, str]:
    """(enough, message). The message is what the person reads when the load is refused."""
    need = reservation_gb + margin_gb
    if available_gb >= need:
        return True, f"{available_gb:.0f} GB free for {reservation_gb:.0f} GB (+{margin_gb:.0f} GB margin)"
    return False, (f"only {available_gb:.0f} GB of memory are really free and the load needs about {need:.0f} GB "
                   f"({reservation_gb:.0f} GB for the model + {margin_gb:.0f} GB margin). Something else is holding memory "
                   "(a running download, the robot server, another model or a desktop session): stop it and try again")


def is_oom_failure(message: str, oom_killed=None, exit_code=None) -> bool:
    low = (message or "").lower()
    return bool(oom_killed) or exit_code == 137 or "oom=true" in low or "code 137" in low or "out of memory" in low


def wait_for_headroom(need_gb: float, margin_gb: float, probe: Callable[[], float], timeout_s: float = HEADROOM_WAIT_S,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[bool, str]:
    """Poll the free memory until it is enough or the time is up (evicted models free theirs a few seconds later)."""
    end = clock() + timeout_s
    while True:
        ok, msg = headroom_verdict(probe(), need_gb, margin_gb)
        if ok or clock() >= end:
            return ok, msg
        sleep(3)


# ------------------------------------------------------------------ the parts that touch the system


def margin_gb(db) -> float:
    from ..settings_store import get_setting

    try:
        return max(0.0, float(get_setting(db, "load_safety_gb") or DEFAULT_MARGIN_GB))
    except (TypeError, ValueError):
        return DEFAULT_MARGIN_GB


def pause_downloads() -> list[str]:
    """Pause the running Hugging Face downloads (their partial files are kept) and remember them to resume afterwards."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import DownloadStatus, DownloadTask
    from ..redis_client import get_redis

    ids: list[str] = []
    with SessionLocal() as db:
        for t in db.scalars(select(DownloadTask).where(DownloadTask.kind == "hf", DownloadTask.status == DownloadStatus.running)):
            t.status = DownloadStatus.paused
            ids.append(str(t.id))
        db.commit()
    if ids:
        r = get_redis()
        r.sadd(HELD_KEY, *ids)
        r.expire(HELD_KEY, 6 * 3600)
    return ids


def resume_downloads() -> int:
    """Resume what pause_downloads() paused (only if it is still paused: a person may have cancelled it meanwhile)."""
    from ..db import SessionLocal
    from ..models import DownloadStatus, DownloadTask, User
    from ..redis_client import get_redis

    r = get_redis()
    held = [x.decode() if isinstance(x, bytes) else x for x in r.smembers(HELD_KEY)]
    n = 0
    if not held:
        return 0
    from ..routers.huggingface import _start_download

    with SessionLocal() as db:
        for tid in held:
            t = db.get(DownloadTask, tid)
            if t is None or t.status != DownloadStatus.paused or t.requested_by is None:
                continue
            u = db.get(User, t.requested_by)
            if u is None:
                continue
            t.status = DownloadStatus.pending
            db.commit()
            _start_download(db, t, u)
            n += 1
    r.delete(HELD_KEY)
    return n


def free_robot_memory() -> bool:
    """The motion server keeps Kimodo and a text encoder in memory; they reload on demand, so let go of them first."""
    try:
        from . import robot

        robot.call("POST", "/_admin/unload", timeout=20, json={"model": "all"})
        return True
    except Exception:  # noqa: BLE001 — not running, or not reachable: nothing to free
        return False


def prepare_heavy_load() -> dict:
    return {"paused": pause_downloads(), "robot_freed": free_robot_memory()}


def finish_heavy_load() -> int:
    try:
        return resume_downloads()
    except Exception as e:  # noqa: BLE001 — a download that stays paused is visible and can be resumed by hand
        from ..logging_setup import log

        log.warning("memguard.resume_failed: %s", e)
        return 0


def heavy_load_in_progress() -> bool:
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import RuntimeInstance

    with SessionLocal() as db:
        return any(i.reserved_memory_gb >= HEAVY_GB for i in db.scalars(select(RuntimeInstance).where(RuntimeInstance.state == "loading")))


def downloads_should_wait() -> str | None:
    """Why a download should not start right now, or None. Boot: the resident model loads first. Then: not during a heavy load."""
    if time.monotonic() - _STARTED < BOOT_GRACE_S:
        return "waiting for the resident model to start loading (just after a restart)"
    try:
        if heavy_load_in_progress():
            return "waiting for a large model to finish loading"
    except Exception:  # noqa: BLE001
        return None
    return None


def flag_oom(model_id) -> None:
    from ..redis_client import get_redis

    get_redis().set(OOM_KEY.format(model_id), "1", ex=OOM_TTL_S)


def oom_flagged(model_id) -> bool:
    from ..redis_client import get_redis

    return bool(get_redis().get(OOM_KEY.format(model_id)))


def clear_oom(model_id) -> None:
    from ..redis_client import get_redis

    get_redis().delete(OOM_KEY.format(model_id))
