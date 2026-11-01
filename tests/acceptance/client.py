"""Minimal session client for acceptance tests against the real deployment (no mocks)."""
from __future__ import annotations

import os
import time

import httpx

BASE = os.environ.get("SAH_BASE_URL", "http://127.0.0.1:8080")


class Hub:
    def __init__(self, username: str | None = None, password: str | None = None, base: str = BASE):
        self.c = httpx.Client(base_url=base, timeout=httpx.Timeout(60, read=1800), follow_redirects=False)
        self.csrf = ""
        if username:
            self.login(username, password)

    def login(self, username, password):
        r = self.c.post("/api/v1/auth/login", json={"username": username, "password": password})
        r.raise_for_status()
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def req(self, method, path, **kw):
        h = kw.pop("headers", {})
        if method.upper() not in ("GET", "HEAD"):
            h["X-CSRF-Token"] = self.csrf
        return self.c.request(method, path, headers=h, **kw)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, **kw):
        return self.req("POST", path, **kw)

    def patch(self, path, **kw):
        return self.req("PATCH", path, **kw)

    def put(self, path, **kw):
        return self.req("PUT", path, **kw)

    def delete(self, path, **kw):
        return self.req("DELETE", path, **kw)

    def wait_job(self, job_id, timeout=3600, poll=3, verbose=True):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            j = self.get(f"/api/v1/jobs/{job_id}").json()
            msg = (j["status"], round(j["progress"], 2), j.get("message"))
            if verbose and msg != last:
                print(f"  [{int(time.time() - t0):>5}s] {j['type']} {msg}", flush=True)
                last = msg
            if j["status"] in ("completed", "failed", "cancelled", "interrupted"):
                return j
            time.sleep(poll)
        raise TimeoutError(job_id)


def admin() -> Hub:
    pw = open("/srv/spark-ai-hub/config/secrets/admin_password").read().strip()
    return Hub("admin", os.environ.get("SAH_ADMIN_PASSWORD", pw))
