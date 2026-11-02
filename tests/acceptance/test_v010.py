"""0.10.0: run-until-done with a plan, messages added to a running task, thinking answers, MCP GET stream."""
import json
import os
import threading
import time

import httpx
import pytest

from client import Hub
from test_cloud import PW, new_conv
from test_v09 import main_alias, stream


@pytest.fixture(scope="module")
def alice() -> Hub:
    return Hub(os.environ.get("SAH_TEST_USER", "alice-test"), os.environ.get("SAH_TEST_USER_PASSWORD", PW))


def test_thinking_answer_is_complete(alice):
    model = main_alias(alice)
    cid = new_conv(alice, model_alias=model)
    evs = stream(alice, f"/api/v1/playground/conversations/{cid}/messages",
                 {"content": "Piensa con calma: ¿cuántos números primos hay entre 1 y 100? Da la lista y el total.",
                  "model_alias": model, "thinking": True, "max_tokens": 4000})
    done = next(e for e in evs if e["type"] == "done")
    text = "".join(e.get("content", "") for e in evs if e["type"] == "delta")
    assert "25" in text and any(e["type"] == "reasoning" for e in evs), (done.get("metrics"), text[-300:])
    alice.delete(f"/api/v1/playground/conversations/{cid}")


def test_plan_and_message_added_to_running_task(alice):
    model = main_alias(alice)
    ws = alice.post("/api/v1/workspaces", json={"name": f"v010-{int(time.time())}"}).json()
    try:
        cid = alice.post(f"/api/v1/workspaces/{ws['id']}/conversation", json={}).json()["conversation_id"]
        evs: list[dict] = []

        def run():  # events are collected as they arrive, so the test can act while the task runs
            body = {"content": "Haz un plan con update_plan y síguelo: 1) crea datos.csv con 5 filas (nombre,edad), "
                               "2) escribe resumen.py que calcule la edad media, 3) ejecútalo y comprueba el resultado.",
                    "model_alias": model, "tools": True, "thinking": False, "max_tokens": 4000, "workspace_id": ws["id"]}
            with alice.c.stream("POST", f"/api/v1/playground/conversations/{cid}/messages", headers={"X-CSRF-Token": alice.csrf},
                                json=body, timeout=httpx.Timeout(60, read=1800)) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        evs.append(json.loads(line[6:]))

        t = threading.Thread(target=run)
        t.start()
        t0 = time.time()
        while time.time() - t0 < 300 and not any(e["type"] == "tool_start" for e in evs):
            time.sleep(1)
        r = alice.post(f"/api/v1/playground/conversations/{cid}/messages",
                       json={"content": "Añade también la edad máxima al resumen.", "model_alias": model,
                             "tools": True, "thinking": False, "workspace_id": ws["id"]})
        assert r.status_code == 202 and r.json()["queued"], (r.status_code, r.text[:300])
        t.join(900)
        types = {e["type"] for e in evs}
        assert "plan" in types and "step" in types, types
        assert any(e["type"] == "user_injected" for e in evs) or any(e.get("followup") for e in evs if e["type"] == "done"), types
        if any(e.get("followup") for e in evs if e["type"] == "done"):  # answered by a follow-up turn instead
            time.sleep(4)
            with alice.c.stream("GET", f"/api/v1/playground/conversations/{cid}/stream", timeout=httpx.Timeout(60, read=900)) as s:
                for line in s.iter_lines():
                    if line.startswith("data: ") and json.loads(line[6:])["type"] in ("done", "gone"):
                        break
        out = alice.post(f"/api/v1/workspaces/{ws['id']}/run", json={"command": "python resumen.py"}).json()
        assert out["exit_code"] == 0 and any(w in out["stdout"].lower() for w in ("máx", "max")), out
    finally:
        alice.delete(f"/api/v1/workspaces/{ws['id']}")


def test_mcp_get_opens_an_authenticated_stream(alice):
    url = str(alice.c.base_url.join("/mcp"))
    assert httpx.get(url, headers={"Accept": "text/event-stream"}, timeout=10).status_code == 401
    key = alice.post("/api/v1/api-keys", json={"name": f"v010-mcp-{int(time.time())}", "scopes": ["inference"]}).json()
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(10, read=20),
                          headers={"Accept": "text/event-stream", "Authorization": f"Bearer {key['key']}"}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    finally:
        alice.delete(f"/api/v1/api-keys/{key['id']}")
