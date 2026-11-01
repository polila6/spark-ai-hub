"""Deployment acceptance tests — run against the real Spark AI Hub over Tailscale HTTPS.

    .venv-tests/bin/pytest -v tests/acceptance -x

No mocks: every assertion talks to the live services. Tests that need an installed model pick
the first registered one and skip with a reason when none exists.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid

import httpx
import pytest

from client import BASE, Hub, admin

PW = os.environ["SAH_TEST_PASSWORD"]  # password for the test accounts the suite creates (alice-test, bob-test…)


def _make_researcher(a: Hub, name: str) -> Hub:
    r = a.post("/api/v1/users", json={"username": name, "display_name": name.title(), "role": "researcher"})
    if r.status_code == 409:
        uid = next(u["id"] for u in a.get("/api/v1/users").json() if u["username"] == name)
        link = a.post(f"/api/v1/users/{uid}/reset-link").json()["reset_url"]
    else:
        r.raise_for_status()
        link = r.json()["invitation_url"]
    token = link.rsplit("/", 1)[1]
    h = Hub()
    r = h.c.post("/api/v1/auth/invitation/accept", json={"token": token, "password": PW})
    r.raise_for_status()
    return Hub(name, PW)




def _chat_model(h: Hub, backend: str | None = None) -> dict:
    ms = [m for m in h.get("/api/v1/models").json() if "chat" in m["capabilities"] and m["status"] in ("installed", "ready")
          and m["visibility"] == "public" and (backend is None or m["backend"] == backend)]
    if not ms:
        pytest.skip("no public chat model installed")
    return ms[0]


# ---------------------------------------------------------------- 1-3 access & login
def test_01_web_ui_loads_over_tailscale():
    r = httpx.get(f"{BASE}/login", timeout=20)
    assert r.status_code == 200 and "Spark AI Hub" in r.text
    assert r.headers.get("x-frame-options") == "DENY"


def test_02_admin_login(adm):
    me = adm.get("/api/v1/auth/me").json()["user"]
    assert me["role"] == "admin"


def test_03_researcher_login(alice):
    assert alice.get("/api/v1/auth/me").json()["user"]["role"] == "researcher"


def test_03b_bad_password_rejected():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}, timeout=20)
    assert r.status_code == 401


def test_03c_no_public_registration():
    r = httpx.post(f"{BASE}/api/v1/users", json={"username": "evil"}, timeout=20)
    assert r.status_code == 401


def test_03d_csrf_required(alice):
    r = alice.c.post("/api/v1/playground/conversations", json={"title": "x"})  # no CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


# ---------------------------------------------------------------- 4 cross-user isolation
def test_04_researcher_cannot_access_other_private_data(alice, bob):
    c = alice.post("/api/v1/playground/conversations", json={"title": "secret-alice"}).json()
    assert bob.get(f"/api/v1/playground/conversations/{c['id']}").status_code == 404
    assert all(x["id"] != c["id"] for x in bob.get("/api/v1/playground/conversations").json()["mine"])
    files = {"file": ("d.csv", b"x1,x2,y\n1,2,3\n4,5,6\n", "text/csv")}
    d = alice.post("/api/v1/datasets", files=files, data={"name": "alice-private"})
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    assert bob.get(f"/api/v1/datasets/{did}").status_code == 404
    assert bob.delete(f"/api/v1/datasets/{did}").status_code == 404
    assert all(x["id"] != did for x in bob.get("/api/v1/datasets").json())
    up = alice.post("/api/v1/media/uploads?kind=image", files={"file": ("p.png", _png(), "image/png")})
    assert up.status_code == 200, up.text
    assert bob.get(f"/api/v1/media/artifacts/{up.json()['id']}/file").status_code == 404
    jobs = bob.get("/api/v1/jobs", params={"all_users": True}).json()
    assert all(j["owner_id"] == bob.get("/api/v1/auth/me").json()["user"]["id"] for j in jobs["items"])


def _png() -> bytes:
    import struct
    import zlib

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 8 for _ in range(8))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_04b_upload_validation(alice):
    r = alice.post("/api/v1/media/uploads?kind=image", files={"file": ("x.png", b"not an image at all", "image/png")})
    assert r.status_code == 422
    r = alice.post("/api/v1/science/models", data={"name": "evil", "adapter": "linear_regression", "config_json": "{}"},
                   files={"files": ("model.pt", b"\x80\x04K\x01.", "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "executable_format"


# ---------------------------------------------------------------- 5 monitoring
def test_05_metrics_are_real(adm):
    m = adm.get("/api/v1/metrics/current").json()
    assert m["measured"] is True and m["memory"]["total_bytes"] > 100e9
    assert m["gpu"]["available"] and m["gpu"]["name"] == "NVIDIA GB10"
    assert m["gpu"]["memory_used_mib"] is None  # UMA: reported as unavailable, never 0
    assert m["cpu_percent"] is not None and m["thermal"]["zones"]


# ---------------------------------------------------------------- 6 HF search
def test_06_hf_search_real(alice):
    r = alice.get("/api/v1/huggingface/search", params={"q": "whisper", "limit": 5})
    assert r.status_code == 200 and any("whisper" in m["id"].lower() for m in r.json())


# ---------------------------------------------------------------- 7-8 model registered
def test_07_08_models_downloaded_and_registered(adm):
    ms = adm.get("/api/v1/models").json()
    hf = [m for m in ms if m["source"] == "hf" and m["status"] in ("installed", "ready")]
    assert hf, "expected at least one installed HF model"
    d = adm.get(f"/api/v1/models/{hf[0]['id']}").json()
    assert d["revision"] and len(d["revision"]) == 40
    chk = adm.post(f"/api/v1/models/{hf[0]['id']}/check").json()
    assert chk["ok"] is True


# ---------------------------------------------------------------- 9-13 inference + API
@pytest.fixture(scope="session")
def alice_key(alice):
    r = alice.post("/api/v1/api-keys", json={"name": "pytest", "expires_in_days": 1, "scopes": ["inference"]})
    r.raise_for_status()
    return r.json()


def test_09_10_11_chat_via_openai_sdk(alice_key):
    from openai import OpenAI

    cl = OpenAI(base_url=f"{BASE}/v1", api_key=alice_key["key"], timeout=1800)
    models = [m.id for m in cl.models.list().data]
    assert models
    target = "qwen3-4b-instruct" if "qwen3-4b-instruct" in models else models[0]
    out = cl.chat.completions.create(model=target, messages=[{"role": "user", "content": "Reply with the word: pong"}],
                                     max_tokens=16, temperature=0)
    assert out.choices[0].message.content and out.usage.completion_tokens > 0
    chunks = [c for c in cl.chat.completions.create(model=target, messages=[{"role": "user", "content": "Count 1 to 5"}],
                                                    max_tokens=32, stream=True)]
    assert any(c.choices and c.choices[0].delta.content for c in chunks)


def test_12_api_key_restrictions(alice, alice_key):
    base = f"{BASE}/v1"
    assert httpx.get(f"{base}/models", timeout=20).status_code == 401
    assert httpx.get(f"{base}/models", headers={"Authorization": "Bearer sah-invalid"}, timeout=20).status_code == 401
    # inference key cannot use session-only/admin endpoints
    h = {"Authorization": f"Bearer {alice_key['key']}"}
    assert httpx.get(f"{BASE}/api/v1/admin/settings", headers=h, timeout=20).status_code == 401
    assert httpx.get(f"{BASE}/api/v1/jobs", headers=h, timeout=20).status_code == 403  # lacks 'jobs' scope
    k = alice.post("/api/v1/api-keys", json={"name": "revoke-me", "scopes": ["inference"]}).json()
    hk = {"Authorization": f"Bearer {k['key']}"}
    assert httpx.get(f"{base}/models", headers=hk, timeout=20).status_code == 200
    alice.delete(f"/api/v1/api-keys/{k['id']}")
    assert httpx.get(f"{base}/models", headers=hk, timeout=20).status_code == 401
    unknown = httpx.post(f"{base}/chat/completions", headers=h, json={"model": "nope", "messages": []}, timeout=20)
    assert unknown.status_code in (400, 404)


def test_12b_rate_limit(alice):
    k = alice.post("/api/v1/api-keys", json={"name": "rl", "scopes": ["inference"], "rpm_limit": 3}).json()
    hk = {"Authorization": f"Bearer {k['key']}"}
    codes = [httpx.get(f"{BASE}/v1/models", headers=hk, timeout=20).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]
    alice.delete(f"/api/v1/api-keys/{k['id']}")


def test_13_unload_model(adm):
    m = _chat_model(adm, "ollama")
    adm.post(f"/api/v1/models/{m['id']}/load").raise_for_status()
    assert any(i["model"] == m["alias"] for i in adm.get("/api/v1/system/status").json()["runtime_instances"])
    r = adm.post(f"/api/v1/models/{m['id']}/unload")
    assert r.status_code == 200 and r.json()["was_loaded"]
    assert not any(i["model"] == m["alias"] for i in adm.get("/api/v1/system/status").json()["runtime_instances"])


# ---------------------------------------------------------------- 14-15 jobs
def _drain(h: Hub) -> None:
    for j in h.get("/api/v1/jobs", params={"status": "queued,preparing,running", "limit": 200}).json()["items"]:
        h.post(f"/api/v1/jobs/{j['id']}/cancel")
    for _ in range(30):
        if not h.get("/api/v1/jobs", params={"status": "queued,preparing,running"}).json()["total"]:
            return
        time.sleep(1)


def test_14_15_jobs_queue_inspect_cancel(alice):
    _drain(alice)
    ids = [alice.post("/api/v1/jobs/test", json={"seconds": 60}).json()["id"] for _ in range(3)]  # limit = 2 concurrent
    try:
        time.sleep(8)
        st = [alice.get(f"/api/v1/jobs/{i}").json() for i in ids]
        assert sum(s["status"] == "running" for s in st) == 2
        waiting = [s for s in st if s["status"] == "queued"]
        assert len(waiting) == 1 and "concurrency" in (waiting[0]["message"] or "")
        assert alice.post(f"/api/v1/jobs/{waiting[0]['id']}/cancel").json()["status"] == "cancelled"
        running = next(s for s in st if s["status"] == "running")
        alice.post(f"/api/v1/jobs/{running['id']}/cancel")
        assert alice.wait_job(running["id"], timeout=60, verbose=False)["status"] == "cancelled"
        ev = alice.get(f"/api/v1/jobs/{running['id']}/events").json()
        assert any("Cancellation requested" in e["message"] for e in ev)
    finally:
        _drain(alice)


def test_14b_reservation_blocks_oversized_job(alice):
    _drain(alice)
    ids = [alice.post("/api/v1/jobs/test", json={"seconds": 40, "memory_gb": 64}).json()["id"] for _ in range(2)]
    try:
        time.sleep(8)
        st = [alice.get(f"/api/v1/jobs/{i}").json() for i in ids]
        # 64 + 64 GB exceed the schedulable budget (~117 GB): one runs, the other waits with the reason.
        assert sum(s["status"] == "running" for s in st) == 1
        waiting = next(s for s in st if s["status"] == "queued")
        assert "resources" in (waiting["message"] or "").lower(), waiting["message"]
    finally:
        _drain(alice)


# ---------------------------------------------------------------- 22-24 security / exposure
def test_22_unauthorised_admin_endpoints(alice):
    for method, path in [("GET", "/api/v1/users"), ("GET", "/api/v1/admin/settings"), ("PUT", "/api/v1/admin/settings"),
                         ("GET", "/api/v1/admin/audit"), ("POST", "/api/v1/admin/backups"), ("POST", "/api/v1/engines/comfyui/start"),
                         ("GET", "/api/v1/admin/diagnostics"), ("POST", f"/api/v1/downloads/{uuid.uuid4()}/approve")]:
        r = alice.req(method, path, json={})
        assert r.status_code in (403, 404), (method, path, r.status_code)


def _host_addresses() -> list[str]:
    """This machine's LAN/public and Tailscale addresses (or SAH_EXPOSURE_IPS=ip1,ip2)."""
    ips = [ip for ip in os.environ.get("SAH_EXPOSURE_IPS", "").split(",") if ip]
    if ips:
        return ips
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        ips.append(s.getsockname()[0])
    finally:
        s.close()
    try:
        ips.append(subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=5).split()[0])
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return ips


def test_23_internal_services_not_exposed():
    targets = [(ip, p) for ip in _host_addresses() for p in (5432, 6379, 11434, 8000, 9000, 8188, 8080, 3000)]
    open_ports = []
    for host, port in targets:
        s = socket.socket()
        s.settimeout(1.5)
        try:
            if s.connect_ex((host, port)) == 0:
                open_ports.append((host, port))
        finally:
            s.close()
    assert open_ports == []


def test_24_health_reflects_status(adm):
    h = httpx.get(f"{BASE}/api/v1/system/health", timeout=20).json()
    assert h["status"] == "ok" and h["checks"] == {"database": "ok", "redis": "ok"}
    cs = adm.get("/api/v1/system/containers").json()["containers"]
    core = {c["service"]: c for c in cs if c.get("service")}
    for svc in ("backend", "worker", "monitor", "postgres", "redis", "proxy", "frontend", "engine-manager", "ollama"):
        assert core[svc]["state"] == "running" and core[svc]["health"] == "healthy", svc
