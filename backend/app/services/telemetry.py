"""Host telemetry for DGX Spark (unified memory).

Reads host /proc and /sys mounted read-only at /host/proc and /host/sys, plus nvidia-smi
(NVIDIA_DRIVER_CAPABILITIES=utility). Unsupported values are returned as None — never 0.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

PROC = Path(os.environ.get("SAH_HOST_PROC", "/host/proc"))
SYS = Path(os.environ.get("SAH_HOST_SYS", "/host/sys"))
if not PROC.exists():
    PROC = Path("/proc")
if not SYS.exists():
    SYS = Path("/sys")

_prev: dict = {}


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


def cpu_times() -> tuple[int, int] | None:
    txt = _read(PROC / "stat")
    if not txt:
        return None
    parts = txt.splitlines()[0].split()[1:]
    vals = list(map(int, parts))
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def meminfo() -> dict:
    out = {}
    txt = _read(PROC / "meminfo") or ""
    for line in txt.splitlines():
        k, _, rest = line.partition(":")
        v = rest.strip().split()
        if v:
            out[k] = int(v[0]) * 1024
    return out


def psi(resource: str) -> dict | None:
    txt = _read(PROC / "pressure" / resource)
    if not txt:
        return None
    res = {}
    for line in txt.splitlines():
        kind, *kv = line.split()
        res[kind] = {k: float(v) for k, v in (x.split("=") for x in kv) if k.startswith("avg")}
    return res


def thermal() -> dict:
    zones = []
    for z in sorted(SYS.glob("class/thermal/thermal_zone*")):
        t = _read(z / "temp")
        typ = (_read(z / "type") or "").strip()
        if t and t.strip().lstrip("-").isdigit():
            zones.append({"zone": z.name, "type": typ, "celsius": int(t) / 1000})
    nvme = []
    for h in SYS.glob("class/hwmon/hwmon*"):
        if (_read(h / "name") or "").strip() == "nvme":
            for inp in sorted(h.glob("temp*_input")):
                lab = _read(h / inp.name.replace("_input", "_label"))
                v = _read(inp)
                if v:
                    nvme.append({"label": (lab or inp.name).strip(), "celsius": int(v) / 1000})
    cpu_max = max((z["celsius"] for z in zones), default=None)
    return {"zones": zones, "cpu_max_c": cpu_max, "nvme": nvme}


def disk_io() -> dict | None:
    txt = _read(PROC / "diskstats")
    if not txt:
        return None
    rd = wr = 0
    for line in txt.splitlines():
        f = line.split()
        if len(f) > 9 and f[2].startswith("nvme") and "p" not in f[2][4:]:
            rd += int(f[5]) * 512
            wr += int(f[9]) * 512
    return {"read_bytes": rd, "write_bytes": wr}


def net_io() -> dict | None:
    # /proc/1/net/dev = host network namespace when the host procfs is mounted; else own namespace.
    txt = _read(PROC / "1" / "net" / "dev") or _read(PROC / "net" / "dev")
    if not txt:
        return None
    rx = tx = 0
    per = {}
    for line in txt.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "br-", "docker")):
            continue
        f = rest.split()
        per[name] = {"rx": int(f[0]), "tx": int(f[8])}
        rx += int(f[0])
        tx += int(f[8])
    return {"rx_bytes": rx, "tx_bytes": tx, "interfaces": per}


def gpu() -> dict:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return {"available": False, "reason": "nvidia-smi not available in container"}
    q = "name,utilization.gpu,temperature.gpu,power.draw,clocks.sm,memory.used,memory.total,driver_version,pstate"
    try:
        r = subprocess.run([smi, f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"available": False, "reason": str(e)}
    if r.returncode != 0 or "failed to initialize nvml" in (r.stdout + r.stderr).lower():
        # nvidia-smi writes «Failed to initialize NVML: Unknown Error» to stdout: do not show an empty reason
        msg = (r.stderr.strip() or r.stdout.strip())[:200] or "nvidia-smi failed"
        # inside a container this means the container lost its access to the GPU (it works on the host): a restart gets it back
        return {"available": False, "reason": msg, "nvml_lost": "nvml" in msg.lower()}
    vals = [v.strip() for v in r.stdout.strip().splitlines()[0].split(",")]

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None  # "[N/A]" / "Not Supported" → unavailable, not zero

    data = {
        "available": True,
        "name": vals[0],
        "utilization_percent": num(vals[1]),
        "temperature_c": num(vals[2]),
        "power_w": num(vals[3]),
        "sm_clock_mhz": num(vals[4]),
        "memory_used_mib": num(vals[5]),
        "memory_total_mib": num(vals[6]),
        "driver_version": vals[7],
        "pstate": vals[8],
        "unified_memory": num(vals[6]) is None,
    }
    try:
        pr = subprocess.run([smi, "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=8)
        procs = []
        for line in pr.stdout.strip().splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 3:
                procs.append({"pid": f[0], "name": f[1], "used_memory_mib": num(f[2])})
        data["compute_processes"] = procs
    except (subprocess.TimeoutExpired, OSError):
        data["compute_processes"] = None
    return data


def disk_usage(paths: list[str]) -> list[dict]:
    seen = set()
    out = []
    for p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        key = st.f_fsid
        if key in seen:
            continue
        seen.add(key)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        out.append({"path": p, "total_bytes": total, "free_bytes": free, "used_bytes": total - st.f_bfree * st.f_frsize,
                    "used_percent": round(100 * (1 - free / total), 2) if total else None})
    return out


def snapshot(data_root: str) -> dict:
    """One measurement. Rates are computed against the previous call in this process."""
    now = time.time()
    m = meminfo()
    ct = cpu_times()
    dio = disk_io()
    nio = net_io()
    snap: dict = {"ts": now}
    if ct:
        prev = _prev.get("cpu")
        if prev:
            dt, di = ct[0] - prev[0], ct[1] - prev[1]
            snap["cpu_percent"] = round(100 * (1 - di / dt), 2) if dt > 0 else None
        else:
            snap["cpu_percent"] = None
        _prev["cpu"] = ct
    load = _read(PROC / "loadavg")
    snap["load_avg"] = [float(x) for x in load.split()[:3]] if load else None
    total = m.get("MemTotal")
    avail = m.get("MemAvailable")
    snap["memory"] = {
        "total_bytes": total,
        "available_bytes": avail,
        "used_bytes": (total - avail) if total and avail else None,
        "cached_bytes": m.get("Cached"),
        "swap_total_bytes": m.get("SwapTotal"),
        "swap_used_bytes": (m.get("SwapTotal", 0) - m.get("SwapFree", 0)) if "SwapTotal" in m else None,
        "used_percent": round(100 * (1 - avail / total), 2) if total and avail else None,
    }
    snap["pressure"] = {"memory": psi("memory"), "cpu": psi("cpu"), "io": psi("io")}
    for key, cur, fields in (("disk_io", dio, ("read_bytes", "write_bytes")), ("net_io", nio, ("rx_bytes", "tx_bytes"))):
        if cur is None:
            snap[key] = None
            continue
        prev = _prev.get(key)
        rates = {}
        if prev:
            dt = now - prev[0]
            for f in fields:
                rates[f.replace("_bytes", "_bps")] = max(0.0, (cur[f] - prev[1][f]) / dt) if dt > 0 else None
        else:
            rates = {f.replace("_bytes", "_bps"): None for f in fields}
        _prev[key] = (now, cur)
        snap[key] = rates
    snap["thermal"] = thermal()
    snap["gpu"] = gpu()
    snap["disks"] = disk_usage([data_root, "/"])
    return snap
