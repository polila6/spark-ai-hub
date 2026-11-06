"""Monitor process: samples real host telemetry, stores downsampled history, raises alerts.

Cost is kept low: one /proc + nvidia-smi read every 5 s, one DB row per minute.
"""
from __future__ import annotations

import os
import signal
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

import orjson
from sqlalchemy import func, select

from .config import get_settings
from .db import SessionLocal
from .logging_setup import log, setup_logging
from .models import Alert, Job, JobStatus, MetricSample
from .redis_client import get_redis, publish
from .services import orchestrator, telemetry
from .settings_store import get_setting

SAMPLE_S = 5
NVML_LOST_SAMPLES = 24  # 2 minutes without GPU readings because the container lost the GPU
NVML_MIN_UPTIME_S = 180  # a fresh start that still cannot see the GPU is not restarted again at once (no restart loop)


def should_restart_for_gpu(consecutive_lost: int, uptime_s: float, restarted_recently: bool) -> bool:
    """The monitor container can lose its access to the GPU («Failed to initialize NVML: Unknown Error», e.g. after systemd
    reloads cgroups); the host is fine and a restart gets it back. Exit (Docker restarts us) after 2 minutes of it, but never
    in a loop."""
    return consecutive_lost >= NVML_LOST_SAMPLES and uptime_s >= NVML_MIN_UPTIME_S and not restarted_recently
_stop = threading.Event()
HUB_SERVICES = {"postgres", "redis", "backend", "worker", "frontend", "proxy", "engine-manager", "ollama", "monitor"}


def utcnow():
    return datetime.now(timezone.utc)


def _avg(samples: list[dict], path: tuple) -> float | None:
    vals = []
    for s in samples:
        v = s
        for p in path:
            v = v.get(p) if isinstance(v, dict) else None
        if isinstance(v, (int, float)):
            vals.append(v)
    return round(statistics.mean(vals), 3) if vals else None


def _compact(samples: list[dict]) -> dict:
    last = samples[-1]
    return {
        "cpu_percent": _avg(samples, ("cpu_percent",)),
        "mem_used_percent": _avg(samples, ("memory", "used_percent")),
        "mem_available_gb": round((_avg(samples, ("memory", "available_bytes")) or 0) / 1e9, 2),
        "gpu_util": _avg(samples, ("gpu", "utilization_percent")),
        "gpu_temp": _avg(samples, ("gpu", "temperature_c")),
        "gpu_power": _avg(samples, ("gpu", "power_w")),
        "cpu_temp_max": _avg(samples, ("thermal", "cpu_max_c")),
        "disk_read_bps": _avg(samples, ("disk_io", "read_bps")),
        "disk_write_bps": _avg(samples, ("disk_io", "write_bps")),
        "net_rx_bps": _avg(samples, ("net_io", "rx_bps")),
        "net_tx_bps": _avg(samples, ("net_io", "tx_bps")),
        "psi_mem_some_avg10": _avg(samples, ("pressure", "memory", "some", "avg10")),
        "disk_used_percent": (last.get("disks") or [{}])[0].get("used_percent"),
    }


def _alert(db, key: str, kind: str, severity: str, message: str, active: bool) -> None:
    row = db.scalar(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))
    if active:
        if row:
            row.last_seen = utcnow()
            row.message = message
            row.severity = severity
        else:
            db.add(Alert(key=key, kind=kind, severity=severity, message=message))
            publish("sah:alerts", {"key": key, "severity": severity, "message": message})
            log.warning("alert.raised", key=key, message=message)
    elif row:
        row.resolved_at = utcnow()


def evaluate_alerts(snap: dict, containers: list[dict] | None) -> None:
    with SessionLocal() as db:
        warn_pct = float(get_setting(db, "storage_warn_percent") or 85)
        min_free = float(get_setting(db, "storage_min_free_gb") or 200) * 1e9
        temp_c = float(get_setting(db, "temperature_alert_c") or 90)
        queue_min = float(get_setting(db, "queue_wait_alert_min") or 60)
        d = (snap.get("disks") or [{}])[0]
        used_pct = d.get("used_percent") or 0
        free = d.get("free_bytes") or 0
        _alert(db, "storage", "storage", "critical" if free < min_free else "warning",
               f"Disk {used_pct:.1f}% used, {free / 1e9:.0f} GB free (threshold {warn_pct:.0f}% / {min_free / 1e9:.0f} GB)",
               used_pct > warn_pct or free < min_free)
        mem = snap.get("memory") or {}
        avail = mem.get("available_bytes") or 0
        total = mem.get("total_bytes") or 1
        psi = ((snap.get("pressure") or {}).get("memory") or {}).get("some", {}).get("avg60")
        _alert(db, "memory_pressure", "memory", "warning",
               f"Memory available {avail / 1e9:.1f} GB ({100 * avail / total:.1f}%), PSI some avg60={psi}",
               avail / total < 0.06 or (psi is not None and psi > 20))
        g = snap.get("gpu") or {}
        _alert(db, "gpu_telemetry", "gpu", "warning",
               f"GPU readings unavailable: {g.get('reason') or 'no data'}" + (" (the monitor restarts itself)" if g.get("nvml_lost") else ""),
               g.get("available") is False)
        gt = g.get("temperature_c")
        if gt is not None:  # only when telemetry is reliable
            _alert(db, "gpu_temp", "temperature", "critical", f"GPU temperature {gt:.0f} °C ≥ {temp_c:.0f} °C", gt >= temp_c)
        ct = (snap.get("thermal") or {}).get("cpu_max_c")
        if ct is not None:
            _alert(db, "cpu_temp", "temperature", "critical", f"SoC zone temperature {ct:.0f} °C ≥ {temp_c:.0f} °C",
                   ct >= temp_c)
        if containers is not None:
            for c in containers:
                svc = c.get("service")
                if svc not in HUB_SERVICES:
                    continue
                bad = c.get("state") != "running" or c.get("health") == "unhealthy"
                _alert(db, f"service:{svc}", "service", "critical",
                       f"Service {svc} is {c.get('state')} (health={c.get('health')})", bad)
        since = utcnow() - timedelta(hours=1)
        fails = db.execute(select(Job.type, func.count()).where(Job.status == JobStatus.failed, Job.finished_at > since)
                           .group_by(Job.type)).all()
        failing = {t: n for t, n in fails if n >= 3}
        for t in ("hf_download", "ollama_pull", "smoke_test", "benchmark", "image_generate", "video_generate",
                  "audio_transcribe", "audio_tts", "sci_infer", "backup", "comparison"):
            _alert(db, f"job_failures:{t}", "jobs", "warning", f"{failing.get(t, 0)} '{t}' jobs failed in the last hour",
                   t in failing)
        old = db.scalar(select(func.min(Job.created_at)).where(Job.status == JobStatus.queued))
        waiting = (utcnow() - old).total_seconds() / 60 if old else 0
        _alert(db, "queue_wait", "jobs", "warning", f"Oldest queued job waiting {waiting:.0f} min", waiting > queue_min)
        db.commit()


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level, "monitor")
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    r = get_redis()
    window: list[dict] = []
    last_store = time.time()
    last_hour = utcnow().replace(minute=0, second=0, microsecond=0)
    containers = None
    last_containers = 0.0
    telemetry.snapshot(str(s.data_root))  # prime rate counters
    log.info("monitor.start")
    started = time.time()
    gpu_lost = 0
    while not _stop.wait(SAMPLE_S):
        try:
            open("/tmp/monitor-alive", "w").write(str(time.time()))
            snap = telemetry.snapshot(str(s.data_root))
            snap["measured"] = True
            gpu_lost = gpu_lost + 1 if (snap.get("gpu") or {}).get("nvml_lost") else 0
            if should_restart_for_gpu(gpu_lost, time.time() - started, bool(r.get("sah:monitor:gpu_restart"))):
                r.set("sah:monitor:gpu_restart", "1", ex=1800)  # at most once every 30 minutes
                log.error("monitor.gpu_lost_restarting", reason=(snap.get("gpu") or {}).get("reason"))
                os._exit(75)  # restart: unless-stopped brings the container back with its GPU access
            r.set("sah:metrics:latest", orjson.dumps(snap), ex=60)
            r.lpush("sah:metrics:recent", orjson.dumps(_compact([snap]) | {"ts": snap["ts"]}))
            r.ltrim("sah:metrics:recent", 0, 719)  # 1 h at 5 s
            window.append(snap)
            if time.time() - last_containers > 30:
                try:
                    containers = orchestrator.containers()
                    r.set("sah:containers", orjson.dumps(containers), ex=120)
                except orchestrator.OrchestratorError as e:
                    containers = None
                    log.warning("monitor.containers_failed", error=str(e))
                last_containers = time.time()
            if time.time() - last_store >= 60 and window:
                with SessionLocal() as db:
                    db.add(MetricSample(resolution="1m", data=_compact(window)))
                    now = utcnow()
                    hour = now.replace(minute=0, second=0, microsecond=0)
                    if hour > last_hour:
                        rows = db.scalars(select(MetricSample).where(MetricSample.resolution == "1m",
                                                                     MetricSample.ts >= last_hour,
                                                                     MetricSample.ts < hour)).all()
                        if rows:
                            agg = {k: _avg([x.data for x in rows], (k,)) for k in rows[0].data}
                            db.add(MetricSample(ts=last_hour, resolution="1h", data=agg))
                        last_hour = hour
                    db.commit()
                evaluate_alerts(snap, containers)
                window = []
                last_store = time.time()
        except Exception as e:  # noqa: BLE001
            log.error("monitor.error", error=str(e))


if __name__ == "__main__":
    main()
