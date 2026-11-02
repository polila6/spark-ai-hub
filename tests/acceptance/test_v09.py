"""0.9.0: main model switch, chat message actions (edit/regenerate/resume) and workspace terminal (multi-line, stop)."""
import json
import os
import time

import httpx
import pytest

from client import Hub
from test_cloud import PW, chat, new_conv, text_of


@pytest.fixture(scope="module")
def alice() -> Hub:
    return Hub(os.environ.get("SAH_TEST_USER", "alice-test"), os.environ.get("SAH_TEST_USER_PASSWORD", PW))


def main_alias(h: Hub) -> str:
    st = h.get("/api/v1/models/main").json()
    assert st["model"] and st["model"]["state"] == "loaded", st
    return st["alias"]


def stream(h: Hub, path: str, body: dict) -> list[dict]:
    evs = []
    with h.c.stream("POST", path, headers={"X-CSRF-Token": h.csrf}, json=body, timeout=httpx.Timeout(60, read=1800)) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
    assert not [e for e in evs if e["type"] == "error"], evs
    return evs


def test_main_model_is_loaded_and_researchers_cannot_change_it(alice):
    main_alias(alice)
    assert alice.post("/api/v1/models/main", json={"model_id": "00000000-0000-0000-0000-000000000000"}).status_code == 403


def test_edit_regenerate_and_resume_from_a_message(alice):
    model = main_alias(alice)
    cid = new_conv(alice, model_alias=model)
    evs = chat(alice, cid, "Responde solo con la palabra: manzana", model=model, thinking=False, max_tokens=50)
    first = next(e["message_id"] for e in evs if e["type"] == "user_message")
    chat(alice, cid, "Ahora responde solo con: pera", model=model, thinking=False, max_tokens=50)
    msgs = alice.get(f"/api/v1/playground/conversations/{cid}").json()["messages"]
    assert len(msgs) == 4
    # edit the first message: everything after it is replaced by a new answer
    evs = stream(alice, f"/api/v1/playground/conversations/{cid}/messages/{first}/regenerate",
                 {"content": "Responde solo con la palabra: naranja", "model_alias": model, "thinking": False, "max_tokens": 50})
    assert "naranja" in text_of(evs).lower()
    msgs = alice.get(f"/api/v1/playground/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and "naranja" in msgs[0]["content"]
    # resume from the user message: the answer is deleted
    r = alice.post(f"/api/v1/playground/conversations/{cid}/messages/{first}/truncate").json()
    assert r["ok"] and r["deleted"] == 1
    assert len(alice.get(f"/api/v1/playground/conversations/{cid}").json()["messages"]) == 1
    alice.delete(f"/api/v1/playground/conversations/{cid}")


def test_workspace_terminal_multiline_and_stop(alice):
    ws = alice.post("/api/v1/workspaces", json={"name": f"v09-{int(time.time())}"}).json()
    try:
        r = alice.post(f"/api/v1/workspaces/{ws['id']}/run", json={"command": "for i in 1 2 3; do\r\n  echo linea$i\r\ndone\ncat <<EOF\nfin\nEOF"}).json()
        assert r["exit_code"] == 0 and "linea3" in r["stdout"] and "fin" in r["stdout"], r
        bg = alice.post(f"/api/v1/workspaces/{ws['id']}/run", json={"command": "sleep 120; echo nunca", "background": True}).json()
        rid = bg.get("run_id") or bg.get("id")
        time.sleep(4)
        assert alice.post(f"/api/v1/workspaces/{ws['id']}/runs/{rid}/stop").status_code == 200
        t0 = time.time()
        while time.time() - t0 < 60:
            run = next(x for x in alice.get(f"/api/v1/workspaces/{ws['id']}/runs").json()["runs"] if x["id"] == rid)
            if run.get("status") not in ("running", "queued"):
                break
            time.sleep(2)
        assert "nunca" not in (run.get("stdout") or "") and time.time() - t0 < 60, run
    finally:
        alice.delete(f"/api/v1/workspaces/{ws['id']}")
