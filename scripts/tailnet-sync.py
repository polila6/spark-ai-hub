#!/usr/bin/env python3
"""Keep this machine in step with its Tailscale identity (address, tailnet name, account).

Runs every minute from a systemd user timer (`scripts/sah.sh tailnet-install`). When the identity changes it:
  * publishes the new address in <data>/run/tailnet.json, which the hub reads live (no restart): invitations, e-mails,
    MCP links and sign-in callbacks follow it, and managers are notified;
  * updates SAH_PUBLIC_BASE_URL in .env (used on the next start and by the Android build);
  * re-creates Tailscale Serve (HTTPS on the tailnet only, never Funnel), which is lost when the account changes;
  * moves Sunshine's bind_address to the new Tailscale IP, if Sunshine is bound to one;
  * rebuilds the Android app with the new default address, when the build tools are installed.

A custom SAH_PUBLIC_BASE_URL (not *.ts.net, not localhost) is respected: the address is then left alone. Nothing is
done while Tailscale is logged out, so a temporary disconnection does not change anything.

  tailnet-sync.py            one pass
  tailnet-sync.py --status   print the current state
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = "http://127.0.0.1:8080"
SERVE_RETRY_S = 120  # a failing `tailscale serve` is retried every 2 min, not on every pass
SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"


def run(*cmd: str, timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:  # `tailscale serve` waits forever when HTTPS is off, printing how to enable it
        out = e.stdout or b""
        err = e.stderr or b""
        return 124, (out.decode() if isinstance(out, bytes) else out) + (err.decode() if isinstance(err, bytes) else err)
    except OSError as e:
        return 127, str(e)


# ------------------------------------------------------------------ pure helpers (unit tested)


def parse_status(raw: str) -> dict | None:
    """{dns, ipv4, ipv6, account} of a logged-in node, else None (logged out, stopped, invalid output)."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    me = d.get("Self") or {}
    dns = (me.get("DNSName") or "").rstrip(".")
    if d.get("BackendState") != "Running" or not dns:
        return None
    ips = me.get("TailscaleIPs") or []
    return {"dns": dns, "ipv4": next((i for i in ips if ":" not in i), None), "ipv6": next((i for i in ips if ":" in i), None),
            "account": (d.get("CurrentTailnet") or {}).get("Name"), "https": bool(d.get("CertDomains")),
            "key_expiry": None if me.get("KeyExpiry", "").startswith("0001") else me.get("KeyExpiry")}


def env_url(text: str) -> str | None:
    m = re.search(r"^SAH_PUBLIC_BASE_URL=([^\s#]*)", text, re.M)
    return m.group(1) if m else None


def is_managed(url: str | None) -> bool:
    """Only addresses this script owns are rewritten: unset, local, or a Tailscale name."""
    return not url or url.endswith(".ts.net") or "localhost" in url or "127.0.0.1" in url


def with_env_url(text: str, url: str) -> str:
    line = f"SAH_PUBLIC_BASE_URL={url}"
    if re.search(r"^SAH_PUBLIC_BASE_URL=", text, re.M):
        return re.sub(r"^SAH_PUBLIC_BASE_URL=.*$", line, text, flags=re.M)
    return text.rstrip("\n") + f"\n{line}\n"


def serve_ok(raw: str, dns: str) -> bool:
    try:
        web = json.loads(raw).get("Web") or {}
    except ValueError:
        return False
    handlers = (web.get(f"{dns}:443") or {}).get("Handlers") or {}
    return (handlers.get("/") or {}).get("Proxy") == TARGET


def serve_problem(output: str) -> tuple[str, str | None]:
    """Why `tailscale serve` failed: (code, URL to fix it if the CLI printed one)."""
    url = re.search(r"https://login\.tailscale\.com/\S+", output)
    low = output.lower()
    if "access denied" in low or "use 'sudo" in low or "--operator" in low or "permission denied" in low:
        return "needs_operator", None
    if url or "not enabled" in low or "https" in low and "enable" in low:
        return "https_disabled", url.group(0) if url else None
    return "error", None


def sunshine_rebind(conf: str, ip: str | None) -> str | None:
    """New sunshine.conf when it is bound to an outdated Tailscale address; None if there is nothing to do.
    A missing bind_address or one outside Tailscale's range is a deliberate choice and is never touched."""
    m = re.search(r"^(\s*bind_address\s*=\s*)(\S+)\s*$", conf, re.M)
    if not m or not ip or m.group(2) == ip:
        return None
    try:
        if ipaddress.ip_address(m.group(2)) not in ipaddress.ip_network("100.64.0.0/10"):
            return None
    except ValueError:
        return None
    return conf[:m.start()] + m.group(1) + ip + conf[m.end():]


# ------------------------------------------------------------------ actions


def data_root(env_text: str) -> Path:
    m = re.search(r"^SAH_DATA_ROOT=([^\s#]+)", env_text, re.M)
    return Path(os.environ.get("SAH_DATA_ROOT") or (m.group(1) if m else "/srv/spark-ai-hub"))


def ensure_serve(dns: str, prev: dict) -> dict:
    code, raw = run("tailscale", "serve", "status", "--json")
    if code == 0 and serve_ok(raw, dns):
        return {"serve": "ok", "serve_url": None, "serve_tried_at": prev.get("serve_tried_at")}
    now = time.time()
    if prev.get("dns") == dns and prev.get("serve") not in (None, "ok") and now - (prev.get("serve_tried_at") or 0) < SERVE_RETRY_S:
        return {k: prev.get(k) for k in ("serve", "serve_url", "serve_tried_at")}
    code, out = run("tailscale", "serve", "--bg", "--https=443", TARGET)
    if code == 0:
        print(f"Tailscale Serve: https://{dns} -> {TARGET}")
        return {"serve": "ok", "serve_url": None, "serve_tried_at": now}
    problem, url = serve_problem(out)
    print(f"Tailscale Serve not configured: {problem}")
    return {"serve": problem, "serve_url": url, "serve_tried_at": now}


def update_sunshine(ip: str | None) -> bool:
    path = Path.home() / ".config/sunshine/sunshine.conf"
    try:
        new = sunshine_rebind(path.read_text(), ip)
    except OSError:
        return False
    if new is None:
        return False
    path.write_text(new)
    run("systemctl", "--user", "try-restart", SUNSHINE_UNIT, timeout=40)  # streams on the old address are already dead
    print(f"Sunshine moved to {ip}")
    return True


def rebuild_apk(root: Path) -> bool:
    tools = root / "cache" / "android-build"
    if not (tools / "android-tools").is_dir() or not (tools / "sdk").is_dir():
        return False
    env = {**os.environ, "ANDROID_TOOLS": str(tools / "android-tools"), "ANDROID_SDK": str(tools / "sdk")}
    try:
        p = subprocess.run(["bash", str(REPO / "android" / "build.sh")], env=env, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return False
    print(f"Android app rebuilt: {p.returncode == 0}")
    return p.returncode == 0


def main() -> int:
    env_path = REPO / ".env"
    env_text = env_path.read_text() if env_path.exists() else ""
    state_path = data_root(env_text) / "run" / "tailnet.json"
    if "--status" in sys.argv:
        print(state_path.read_text() if state_path.exists() else "{}")
        return 0
    current = env_url(env_text)
    if not is_managed(current):
        return 0
    code, raw = run("tailscale", "status", "--json")
    me = parse_status(raw) if code == 0 else None
    if me is None:  # logged out or stopped: keep the last known address
        return 0
    try:
        prev = json.loads(state_path.read_text())
    except (OSError, ValueError):
        prev = {}
    url = f"https://{me['dns']}"
    state = {**me, "url": url, "updated_at": prev.get("updated_at"), **ensure_serve(me["dns"], prev)}
    update_sunshine(me["ipv4"])
    if current != url:
        env_path.write_text(with_env_url(env_text, url))
        print(f"SAH_PUBLIC_BASE_URL -> {url}")
    identity = ("dns", "ipv4", "account")
    if any(prev.get(k) != state.get(k) for k in identity):
        state["updated_at"] = time.time()
        print(f"Tailscale identity: {url} ({me['ipv4']}, {me['account']})")
    state["apk_url"] = prev.get("apk_url")
    if state["apk_url"] != url and rebuild_apk(state_path.parent.parent):
        state["apk_url"] = url
    state["checked_at"] = time.time()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.chmod(0o644)
    tmp.replace(state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
