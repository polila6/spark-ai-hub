"""0.5.0: code sandbox, canvas, deep research, voice, scheduled tasks, connectors, external sign-in, usage, MTP."""
import time

import pytest

from client import Hub
from test_cloud import GENERAL, PW, chat, new_conv, text_of, wait


@pytest.fixture(scope="module")
def alice() -> Hub:
    """These tests only need a researcher: reuse the existing test account (no admin login needed)."""
    return Hub("alice-test", PW)


def test_chat_runs_code_and_returns_files(alice):
    cid = new_conv(alice, model_alias=GENERAL)
    evs = chat(alice, cid, "Con código: calcula la suma de los cuadrados de 1 a 100 y guarda un gráfico de barras de "
                           "los 10 primeros cuadrados como cuadrados.png. Dime el resultado exacto.", model=GENERAL,
               max_tokens=2048, thinking=False)
    runs = [e for e in evs if e["type"] == "tool_result" and e["name"] == "run_code"]
    assert runs and any(r["ok"] for r in runs), runs
    files = [f for r in runs for f in (r.get("code") or {}).get("files", [])]
    assert any(f["kind"] == "image" for f in files), files
    assert "338350" in text_of(evs).replace(".", "").replace(" ", "").replace("\\,", "").replace(",", "")
    img = next(f for f in files if f["kind"] == "image")
    assert alice.get(img["url"]).content[:8] == b"\x89PNG\r\n\x1a\n"


def test_sandbox_has_no_network_and_is_read_only(alice):
    cid = new_conv(alice, model_alias=GENERAL)
    evs = chat(alice, cid, "Ejecuta exactamente este código Python con run_code y dime su salida literal:\n```python\n"
                           "import urllib.request, os\ntry:\n    urllib.request.urlopen('https://example.org', timeout=4); print('NET=open')\n"
                           "except Exception: print('NET=closed')\ntry:\n    open('/usr/x','w'); print('ROOT=writable')\n"
                           "except Exception: print('ROOT=readonly')\nprint('UID=', os.getuid())\n```", model=GENERAL,
               max_tokens=1024, thinking=False)
    out = "".join((e.get("code") or {}).get("stdout", "") for e in evs if e["type"] == "tool_result" and e["name"] == "run_code")
    assert "NET=closed" in out and "ROOT=readonly" in out and "UID= 1000" in out, out


def test_canvas_crud_versions_and_export(alice):
    d = alice.post("/api/v1/canvas", json={"title": "Informe prueba", "content": "# Informe\n\nTexto **negrita**.\n\n| a | b |\n|---|---|\n| 1 | 2 |"}).json()
    d2 = alice.put(f"/api/v1/canvas/{d['id']}", json={"content": "# Informe\n\nVersión 2.", "base_version": 1}).json()
    assert d2["version"] == 2
    assert alice.put(f"/api/v1/canvas/{d['id']}", json={"content": "x", "base_version": 1}).status_code == 409
    for fmt, magic in (("docx", b"PK\x03\x04"), ("pdf", b"%PDF-"), ("md", b"# Inf")):
        r = alice.post(f"/api/v1/canvas/{d['id']}/export", json={"format": fmt})
        assert r.status_code == 200 and r.content.startswith(magic), (fmt, r.status_code, r.content[:40])
    r = alice.post(f"/api/v1/canvas/{d['id']}/restore/1").json()
    assert r["version"] == 3 and "negrita" in r["content"]
    assert alice.delete(f"/api/v1/canvas/{d['id']}").json()["ok"]


def test_canvas_written_and_edited_by_the_assistant(alice):
    cid = new_conv(alice, model_alias=GENERAL)
    evs = chat(alice, cid, "Escribe en el lienzo un aviso breve (3 frases) de cierre del laboratorio el viernes.",
               model=GENERAL, max_tokens=3000, thinking=False)
    c = next(e["canvas"] for e in evs if e["type"] == "tool_result" and e.get("canvas"))
    evs = chat(alice, cid, "En el lienzo: añade al final la línea «Gracias por vuestra comprensión.»", model=GENERAL,
               max_tokens=3000, thinking=False)
    d = alice.get(f"/api/v1/canvas/{c['id']}").json()
    assert d["version"] >= 2 and "comprensión" in d["content"]


def test_scheduled_task_runs_and_notifies(alice):
    t = alice.post("/api/v1/tasks", json={"name": "acc-task", "prompt": "Responde solo con la palabra PIÑA.",
                                          "schedule": {"freq": "weekly", "weekday": 2, "time": "06:00"},
                                          "timezone": "Europe/Madrid", "options": {"web": False}}).json()
    assert t["next_run_at"] and "miércoles" in t["schedule_text"]
    alice.post(f"/api/v1/tasks/{t['id']}/run")
    done = wait(lambda: next((x for x in alice.get("/api/v1/tasks").json()["tasks"]
                              if x["id"] == t["id"] and x["last_status"] in ("ok", "error")), None), timeout=300)
    assert done["last_status"] == "ok", done
    conv = alice.get(f"/api/v1/playground/conversations/{done['conversation_id']}").json()
    assert "PIÑA" in conv["messages"][-1]["content"].upper()
    alice.delete(f"/api/v1/tasks/{t['id']}")


def test_connectors_refuse_internal_addresses_and_sync_github(alice):
    for cfg, kind in (({"url": "http://backend:8000/"}, "webdav"), ({"url": "http://127.0.0.1:8080/"}, "webdav"),
                      ({"host": "100.100.100.100", "share": "x"}, "smb")):
        c = alice.post("/api/v1/connectors", json={"kind": kind, "name": "bad", "config": cfg, "schedule": "manual"}).json()
        r = alice.post(f"/api/v1/connectors/{c['id']}/test").json()
        assert r["ok"] is False, (cfg, r)
        alice.delete(f"/api/v1/connectors/{c['id']}")
    c = alice.post("/api/v1/connectors", json={"kind": "github", "name": "acc-gh", "schedule": "manual",
                                               "config": {"repo": "anthropics/skills", "path": "skills/internal-comms"}}).json()
    assert alice.post(f"/api/v1/connectors/{c['id']}/test").json()["importable"] >= 1
    alice.post(f"/api/v1/connectors/{c['id']}/sync")
    done = wait(lambda: next((x for x in alice.get("/api/v1/connectors").json()["connectors"]
                              if x["id"] == c["id"] and x["last_sync_at"] and x["status"] != "syncing"), None), timeout=300)
    assert done["status"] == "ok" and done["stats"]["added"] >= 1, done
    alice.delete(f"/api/v1/connectors/{c['id']}")


def test_voice_round_trip(alice):
    st = alice.get("/api/v1/voice").json()
    if not (st["dictation"] and st["read_aloud"]):
        pytest.skip("audio models not installed")
    r = alice.post("/api/v1/voice/speak", json={"text": "Hola, buenos días a todo el equipo.", "lang": "es"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    t = alice.c.post("/api/v1/voice/transcribe", headers={"X-CSRF-Token": alice.csrf},
                     files={"audio": ("voz.wav", r.content, "audio/wav")}, data={"language": "spanish"}, timeout=300).json()
    assert "buenos" in t["text"].lower(), t


def test_usage_and_external_sign_in_guards(alice):
    u = alice.get("/api/v1/usage?days=7").json()
    assert {"totals", "daily", "by_model", "jobs", "storage"} <= set(u)
    assert alice.get("/api/v1/usage?scope=all").status_code == 403
    anon = Hub()
    assert isinstance(anon.get("/api/v1/auth/oidc/providers").json(), list)
    assert anon.get("/api/v1/auth/oidc/nope/start").status_code == 404
    r = anon.c.get("/api/v1/auth/oidc/google/callback?code=x&state=forged", follow_redirects=False)
    assert "sso_error=state" in r.text


def test_general_model_long_context_and_mtp(alice):
    m = next(x for x in alice.get("/api/v1/models").json() if x["alias"] == GENERAL)
    ctx = alice.get(f"/api/v1/models/{m['id']}/context").json()
    assert ctx["current"] >= 131072 and ctx["mtp_available"] and ctx["speculative_tokens"] >= 1
    assert any(p["tokens"] == 262144 for p in ctx["presets"])


@pytest.mark.slow
def test_deep_research_writes_cited_report(alice):
    cid = new_conv(alice, model_alias=GENERAL)
    t0 = time.time()
    evs = chat(alice, cid, "Investiga a fondo (rápido, 3 líneas) qué es el estándar USB4 v2 y su velocidad máxima.",
               model=GENERAL, max_tokens=3000, research=True, thinking=False)
    r = next(e for e in evs if e["type"] == "tool_result" and e["name"] == "deep_research")
    d = alice.get(f"/api/v1/canvas/{r['canvas']['id']}").json()
    assert r["ok"] and len(r["sources"]) >= 3 and "[1]" in d["content"] and time.time() - t0 < 1200


def test_workspace_files_terminal_and_assistant(alice):
    ws = alice.post("/api/v1/workspaces", json={"name": f"acc-{int(time.time())}"}).json()
    wid = ws["id"]
    alice.c.post(f"/api/v1/workspaces/{wid}/upload", headers={"X-CSRF-Token": alice.csrf},
                 files={"file": ("ventas.csv", b"mes,importe\nenero,10\nenero,5\nfebrero,7\n", "text/csv")}, data={"folder": "datos"})
    r = alice.post(f"/api/v1/workspaces/{wid}/run", json={"command": "ln -s /run/secrets x && wc -l datos/ventas.csv"}).json()
    assert r["exit_code"] == 0 and "4 datos/ventas.csv" in r["stdout"], r
    assert alice.get(f"/api/v1/workspaces/{wid}/file", params={"path": "x"}).status_code == 422
    cid = alice.post(f"/api/v1/workspaces/{wid}/conversation").json()["conversation_id"]
    evs = chat(alice, cid, "Con datos/ventas.csv crea resumen.xlsx con el total por mes y compruébalo.", model=GENERAL,
               max_tokens=4096, thinking=False, workspace_id=wid)
    assert any(e["type"] == "tool_result" and e["name"] == "ws_run" for e in evs)
    t = alice.get(f"/api/v1/workspaces/{wid}/table", params={"path": "resumen.xlsx"}).json()
    flat = " ".join(" ".join(r) for s in t["sheets"] for r in s["rows"])
    assert "15" in flat and "7" in flat, flat
    assert alice.delete(f"/api/v1/workspaces/{wid}").json()["ok"]
