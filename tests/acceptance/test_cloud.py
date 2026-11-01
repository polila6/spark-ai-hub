"""Acceptance tests for v0.2: accounts (sign-up, manager), personal cloud (files, knowledge, projects, memory), the
multimodal chat agent (documents, data, image generation/editing), model requests, and harness access (OpenAI with
tools, Anthropic /v1/messages, MCP with the official client). Real models, no mocks.

Accounts: alice-test / bob-test (researchers) and carol-test (manager), password PW. No admin login is needed.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import time
import uuid

import httpx
import pytest

from client import BASE, Hub

PW = os.environ["SAH_TEST_PASSWORD"]  # password for the test accounts the suite creates (alice-test, bob-test…)
SECRET = f"ORQUIDEA-{uuid.uuid4().hex[:6].upper()}"
FAST = "qwen3-4b-instruct"  # small tool-calling model: leaves memory for the image model
GENERAL = "qwen3.5-35b-a3b-fp8"  # always-loaded general model (vision, tools, sub-agents)


def make_pdf(lines: list[str]) -> bytes:
    """Minimal valid one-page PDF with Helvetica text (no external dependency)."""
    text = "BT /F1 14 Tf 72 740 Td 18 TL " + " ".join(f"({ln.replace('(', '').replace(')', '')}) Tj T*" for ln in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(text)} >>\nstream\n{text}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


@pytest.fixture(scope="module")
def ra():
    return Hub("alice-test", PW)


@pytest.fixture(scope="module")
def rb():
    return Hub("bob-test", PW)


@pytest.fixture(scope="module")
def mgr():
    return Hub("carol-test", PW)


@pytest.fixture(scope="module")
def key(ra):
    k = ra.post("/api/v1/api-keys", json={"name": "pytest-cloud", "expires_in_days": 1, "scopes": ["inference"]}).json()
    yield k["key"]
    ra.delete(f"/api/v1/api-keys/{k['id']}")


@pytest.fixture(scope="module")
def kb_doc(ra):
    """Upload the knowledge-base PDF holding SECRET (old test files removed first) and wait until indexed."""
    for f in ra.get("/api/v1/files?folder=Pruebas").json():
        ra.delete(f"/api/v1/files/{f['id']}")
    pdf = make_pdf(["Informe interno del laboratorio.", f"La palabra clave del proyecto es {SECRET}.",
                    "El presupuesto aprobado es de 48.250 euros."])
    r = ra.post("/api/v1/files", files={"file": ("informe.pdf", pdf, "application/pdf")}, data={"folder": "Pruebas"})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    wait(lambda: [k for k in next(x for x in ra.get("/api/v1/files?folder=Pruebas").json() if x["id"] == fid)["knowledge"]
                  if k["status"] == "ready"], timeout=900)
    return fid


def chat(h: Hub, cid: str, content: str, model: str = FAST, **kw) -> list[dict]:
    """Send a chat message and return all SSE events."""
    evs = []
    with h.c.stream("POST", f"/api/v1/playground/conversations/{cid}/messages", headers={"X-CSRF-Token": h.csrf},
                    json={"content": content, "model_alias": model, "max_tokens": 1024, "temperature": 0.2, **kw},
                    timeout=httpx.Timeout(60, read=1800)) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
    errs = [e for e in evs if e["type"] == "error"]
    assert not errs, errs
    return evs


def text_of(evs) -> str:
    return "".join(e["content"] for e in evs if e["type"] == "delta")


def new_conv(h: Hub, **kw) -> str:
    return h.post("/api/v1/playground/conversations", json=kw).json()["id"]


def wait(fn, timeout=600, every=3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError


# ------------------------------------------------------------------ accounts


def test_signup_pending_then_manager_approves(mgr):
    anon = Hub()
    info = anon.get("/api/v1/auth/signup-info").json()
    assert info["mode"] == "approval" and info["terms"]["version"] >= 1
    name = f"signup-{uuid.uuid4().hex[:6]}"
    r = anon.post("/api/v1/auth/signup", json={"username": name, "email": f"{name}@example.org", "display_name": "Sign Up",
                                               "password": PW, "reason": "pytest", "accept_terms": True})
    assert r.status_code == 200 and r.json()["status"] == "pending", r.text
    r = anon.post("/api/v1/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_pending"
    pend = mgr.get("/api/v1/users?status=pending").json()
    u = next(x for x in pend if x["username"] == name)
    notes = mgr.get("/api/v1/notifications").json()
    assert any(n["kind"] == "signup" and name in n["title"] for n in notes["items"])
    # a manager cannot grant elevated roles
    assert mgr.post(f"/api/v1/users/{u['id']}/approve", json={"role": "admin"}).status_code == 403
    assert mgr.post(f"/api/v1/users/{u['id']}/approve", json={"role": "researcher"}).status_code == 200
    h = Hub(name, PW)
    me = h.get("/api/v1/auth/me").json()
    assert me["user"]["role"] == "researcher" and me["needs_terms"] is False
    mgr.delete(f"/api/v1/users/{u['id']}")


def test_manager_invites_but_cannot_touch_admin_settings(mgr):
    name = f"inv-{uuid.uuid4().hex[:6]}"
    r = mgr.post("/api/v1/users", json={"username": name, "email": f"{name}@example.org", "role": "researcher"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invitation_url"].startswith(BASE) and body["email_status"] in ("skipped", "mail_disabled", "pending")
    assert mgr.post("/api/v1/users", json={"username": name + "x", "role": "manager"}).status_code == 403
    assert mgr.get("/api/v1/admin/settings").status_code == 403
    assert mgr.get("/api/v1/users/policies").status_code == 403
    mgr.delete(f"/api/v1/users/{body['user']['id']}")


# ------------------------------------------------------------------ files, knowledge, chat with documents


def test_files_knowledge_and_hybrid_search(ra, kb_doc):
    f = next(x for x in ra.get("/api/v1/files?folder=Pruebas").json() if x["id"] == kb_doc)
    assert f["text_status"] == "ready" and f["knowledge"][0]["chunks"] >= 1
    res = ra.post("/api/v1/knowledge/search", json={"query": "presupuesto aprobado"}).json()
    assert res["mode"] == "hybrid", res["mode"]
    assert any(SECRET in x["text"] for x in res["results"])
    bad = ra.post("/api/v1/files", files={"file": ("x.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "type_mismatch"


def test_chat_reads_attached_pdf_and_searches_knowledge(ra, kb_doc):
    cid = new_conv(ra)
    pdf = make_pdf(["Acta de la reunion.", "La reunion de seguimiento sera el 14 de marzo en el aula 3B."])
    a = ra.post(f"/api/v1/playground/conversations/{cid}/attachments", files={"file": ("acta.pdf", pdf, "application/pdf")}).json()
    assert a["text_status"] == "ready"
    evs = chat(ra, cid, "¿En qué aula será la reunión de seguimiento? Responde solo el aula.", attachments=[a["id"]])
    assert "3B" in text_of(evs)
    evs = chat(ra, new_conv(ra), "Busca en mis documentos: ¿cuál es la palabra clave del proyecto?")
    assert any(e["type"] == "tool_result" and e["name"] == "search_knowledge" for e in evs)
    assert SECRET in text_of(evs)


def test_chat_queries_data_file_with_sql(ra):
    cid = new_conv(ra)
    csv = "mes,ventas\nenero,120\nfebrero,80\nmarzo,100\nenero,30\n".encode()
    a = ra.post(f"/api/v1/playground/conversations/{cid}/attachments", files={"file": ("ventas.csv", csv, "text/csv")}).json()
    assert a["kind"] == "data"
    evs = chat(ra, cid, "¿Cuántas ventas hubo en enero en total? Usa la herramienta de consulta.", attachments=[a["id"]])
    q = [e for e in evs if e["type"] == "tool_result" and e["name"] == "query_data"]
    assert q and q[-1]["ok"], [e for e in evs if e["type"].startswith("tool")]
    assert "150" in text_of(evs)


def test_memory_saved_and_used_in_new_chat(ra):
    ra.delete("/api/v1/memories")
    chat(ra, new_conv(ra), "Recuerda que mi color favorito es el turquesa.")
    mems = ra.get("/api/v1/memories").json()["items"]
    assert any("turquesa" in m["content"].lower() for m in mems), mems
    assert "turquesa" in text_of(chat(ra, new_conv(ra), "¿Cuál es mi color favorito? Una palabra.")).lower()
    hits = ra.get("/api/v1/playground/search", params={"q": "turquesa"}).json()
    assert hits and "turquesa" in hits[0]["snippet"].lower()


def test_project_instructions_documents_and_sharing(ra, rb):
    p = ra.post("/api/v1/projects", json={"name": "Proyecto pytest", "instructions": "Termina SIEMPRE tus respuestas con la palabra FIN."}).json()
    doc = make_pdf(["Ficha del prototipo.", "El prototipo Kestrel pesa 2,4 kilogramos."])
    f = ra.post("/api/v1/files", files={"file": ("ficha.pdf", doc, "application/pdf")}, data={"project_id": p["id"]}).json()
    wait(lambda: any(k["status"] == "ready" for k in next(x for x in ra.get(f"/api/v1/files?project_id={p['id']}").json() if x["id"] == f["id"])["knowledge"]), 900)
    evs = chat(ra, new_conv(ra, project_id=p["id"]), "¿Cuánto pesa el prototipo Kestrel según los documentos del proyecto?")
    out = text_of(evs)
    assert "2,4" in out or "2.4" in out
    assert out.strip().rstrip(".").upper().endswith("FIN")
    assert rb.get(f"/api/v1/projects/{p['id']}").status_code == 404  # private until shared
    ra.post(f"/api/v1/projects/{p['id']}/shares", json={"username": "bob-test", "permission": "read"})
    assert rb.get(f"/api/v1/projects/{p['id']}").json()["permission"] == "read"
    assert rb.patch(f"/api/v1/projects/{p['id']}", json={"instructions": "x"}).status_code == 403
    res = rb.post("/api/v1/knowledge/search", json={"query": "prototipo Kestrel"}).json()
    assert any("Kestrel" in r["text"] for r in res["results"])
    ra.delete(f"/api/v1/projects/{p['id']}?delete_files=true")
    assert not any("Kestrel" in r["text"] for r in rb.post("/api/v1/knowledge/search", json={"query": "prototipo Kestrel"}).json()["results"])


# ------------------------------------------------------------------ images from the chat


def test_chat_generates_and_edits_images(ra):
    cid = new_conv(ra)
    evs = chat(ra, cid, "Genera una imagen de un faro rojo sobre un acantilado al atardecer.")
    imgs = [e for e in evs if e["type"] == "tool_result" and e.get("image")]
    assert imgs and imgs[0]["ok"], [e for e in evs if e["type"].startswith("tool")]
    img = imgs[0]["image"]
    data = ra.get(img["url"]).content
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    print("generated", img["workflow"], img["seconds"], "s")
    evs = chat(ra, cid, f"Edita {img['ref']}: haz que el faro sea azul y añade estrellas en el cielo.")
    ed = [e for e in evs if e["type"] == "tool_result" and e["name"] == "edit_image"]
    assert ed and ed[0]["ok"] and ed[0]["image"]["edited_from"] == img["ref"], ed
    print("edited", ed[0]["image"]["workflow"], ed[0]["image"]["seconds"], "s")
    # direct image mode with an uploaded image (no LLM involved)
    a = ra.post(f"/api/v1/playground/conversations/{cid}/attachments", files={"file": ("faro.png", data, "image/png")}).json()
    evs = chat(ra, cid, "make it a pencil sketch", mode="image", attachments=[a["id"]])
    assert any(e["type"] == "tool_result" and e["name"] == "edit_image" and e["ok"] for e in evs)
    conv = ra.get(f"/api/v1/playground/conversations/{cid}").json()
    assert sum(1 for m in conv["messages"] for p in (m["parts"] or []) if p.get("image")) == 3


# ------------------------------------------------------------------ model requests


def test_model_request_review_flow(ra, mgr):
    r = ra.post("/api/v1/huggingface/install", json={"repo_id": "Qwen/Qwen3-0.6B-GGUF", "reason": "pytest request", "accept_license": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "awaiting_admin_approval"
    tid = r.json()["download_task_id"]
    pend = mgr.get("/api/v1/downloads?pending=true").json()
    t = next(x for x in pend if x["id"] == tid)
    assert t["request_reason"] == "pytest request" and t["requested_by_name"]
    assert mgr.post(f"/api/v1/downloads/{tid}/reject", json={"comment": "duplicado en pytest"}).status_code == 200
    mine = next(x for x in ra.get("/api/v1/downloads").json() if x["id"] == tid)
    assert mine["status"] == "rejected" and mine["review_comment"] == "duplicado en pytest"
    assert any("no aprobada" in n["title"] for n in ra.get("/api/v1/notifications").json()["items"])


# ------------------------------------------------------------------ harness: OpenAI tools, Anthropic, MCP


def test_anthropic_messages_api_with_tools(key):
    import anthropic

    c = anthropic.Anthropic(base_url=BASE, api_key=key, timeout=1800)
    m = c.messages.create(model=FAST, max_tokens=64, messages=[{"role": "user", "content": "Di solo: hola"}])
    assert m.content[0].type == "text" and "hola" in m.content[0].text.lower()
    tools = [{"name": "get_weather", "description": "Weather for a city", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
    with c.messages.stream(model=FAST, max_tokens=256, tools=tools,
                           messages=[{"role": "user", "content": "¿Qué tiempo hace en Valencia? Usa la herramienta."}]) as s:
        final = s.get_final_message()
    tu = [b for b in final.content if b.type == "tool_use"]
    assert final.stop_reason == "tool_use" and tu and tu[0].input.get("city", "").lower().startswith("valen")
    m2 = c.messages.create(model=FAST, max_tokens=128, tools=tools, messages=[
        {"role": "user", "content": "¿Qué tiempo hace en Valencia?"},
        {"role": "assistant", "content": [b.model_dump() for b in final.content]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu[0].id, "content": "Soleado, 24 grados"}]}])
    assert "24" in m2.content[-1].text


def test_mcp_server_with_official_client(key, ra, kb_doc):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(f"{BASE}/mcp", headers={"Authorization": f"Bearer {key}"}) as (rd, wr, _):
            async with ClientSession(rd, wr) as s:
                init = await s.initialize()
                assert init.serverInfo.name == "spark-ai-hub"
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"search_knowledge", "list_files", "read_file", "query_data", "save_memory", "generate_image"} <= names
                res = await s.call_tool("search_knowledge", {"query": "palabra clave del proyecto"})
                assert not res.isError and SECRET in res.content[0].text
                files = await s.call_tool("list_files", {"folder": "Pruebas"})
                assert kb_doc in [f["file_id"] for f in json.loads(files.content[0].text)]
                txt = await s.call_tool("read_file", {"file_id": kb_doc})
                assert SECRET in txt.content[0].text
                bad = await s.call_tool("read_file", {"file_id": str(uuid.uuid4())})
                assert bad.isError

    asyncio.run(run())
    bad = httpx.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=30)
    assert bad.status_code == 401


def test_openai_gateway_vision_on_general_model(key):
    """The general (always loaded) multimodal model answers about an image sent through /v1, as OpenCode would."""
    from openai import OpenAI
    import base64

    from PIL import Image

    im = Image.new("RGB", (256, 256), (220, 20, 20))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    c = OpenAI(base_url=f"{BASE}/v1", api_key=key, timeout=1800)
    r = c.chat.completions.create(model="qwen3.5-35b-a3b-fp8", max_tokens=300, extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                                  messages=[{"role": "user", "content": [{"type": "text", "text": "¿De qué color es esta imagen? Una palabra."},
                                                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}]}])
    assert "roj" in r.choices[0].message.content.lower()


def test_account_export(ra):
    j = ra.wait_job(ra.post("/api/v1/account/export").json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    art = j["result"]["artifact"]
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(ra.get(art["url"]).content))
    names = z.namelist()
    assert "account.json" in names and any(n.startswith("chats/") for n in names) and any(n.startswith("files/") for n in names)


# ------------------------------------------------------------------ background turns and memory panel (v0.2.1)


def _first_events(h: Hub, cid: str, n: int, **body):
    """Start a turn and drop the connection after n events (like leaving the page)."""
    got = []
    with h.c.stream("POST", f"/api/v1/playground/conversations/{cid}/messages", headers={"X-CSRF-Token": h.csrf},
                    json={"model_alias": FAST, "max_tokens": 400, **body}, timeout=httpx.Timeout(60, read=600)) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                got.append(json.loads(line[6:]))
                if len(got) >= n:
                    break
    return got


def test_chat_keeps_running_after_leaving_and_can_be_reattached(ra):
    cid = new_conv(ra)
    _first_events(ra, cid, 1, content="Escribe una lista numerada de 15 inventos importantes, uno por línea.")
    time.sleep(1)
    assert any(t["conversation_id"] == cid for t in ra.get("/api/v1/playground/active").json())
    conv = ra.get(f"/api/v1/playground/conversations/{cid}").json()
    assert conv["active_turn"] and conv["active_turn"]["status"] == "running"
    evs = []
    with ra.c.stream("GET", f"/api/v1/playground/conversations/{cid}/stream", timeout=httpx.Timeout(60, read=900)) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
    assert evs[0]["seq"] == 1 and evs[-1]["type"] == "done", evs[-1]  # replayed from the start, followed to the end
    msgs = wait(lambda: [m for m in ra.get(f"/api/v1/playground/conversations/{cid}").json()["messages"] if m["role"] == "assistant"], 60, 1)
    assert "15" in msgs[-1]["content"] or msgs[-1]["content"].count("\n") >= 10
    assert not any(t["conversation_id"] == cid for t in ra.get("/api/v1/playground/active").json())
    # a second message while one is running is refused
    _first_events(ra, cid, 1, content="Cuenta del 1 al 300 en palabras, separadas por comas.")
    r = ra.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": "x", "model_alias": FAST})
    assert r.status_code == 409
    # explicit cancel stops it
    assert ra.post(f"/api/v1/playground/conversations/{cid}/cancel").status_code == 200
    wait(lambda: not any(t["conversation_id"] == cid for t in ra.get("/api/v1/playground/active").json()), 120, 1)
    last = ra.get(f"/api/v1/playground/conversations/{cid}").json()["messages"][-1]
    assert last["role"] == "assistant" and last["metrics"].get("cancelled")


def test_memory_panel_and_load_plan(ra):
    mem = ra.get("/api/v1/system/memory").json()
    assert mem["schedulable_gb"] > 50 and mem["idle_timeout_min"] > 0 and isinstance(mem["instances"], list)
    for i in mem["instances"]:
        assert {"name", "state", "reserved_gb", "kind", "pinned", "unload_in_seconds"} <= set(i)
    models = ra.get("/api/v1/models").json()
    m = next(x for x in models if x["alias"] == "qwen3-8b-fp8")
    plan = ra.get(f"/api/v1/models/{m['id']}/load-plan").json()
    assert plan["need_gb"] > 10 and "evict" in plan and "fits" in plan


# ------------------------------------------------------------------ v0.3: web, skills, agents, context


def test_chat_searches_the_web_and_reads_sources(ra):
    cid = new_conv(ra)
    evs = chat(ra, cid, "Busca en internet qué es SearXNG y dime en una frase para qué sirve, citando la fuente.",
               model=GENERAL, thinking=False, max_tokens=1500)
    names = [e["name"] for e in evs if e["type"] == "tool_result"]
    assert "web_search" in names, names
    res = next(e for e in evs if e["type"] == "tool_result" and e["name"] == "web_search")
    assert res["ok"] and any("searx" in s["url"].lower() for s in res["sources"]), res
    assert "http" in text_of(evs)  # cites a URL


def test_web_toggle_off_hides_internet_tools(ra):
    cid = new_conv(ra)
    evs = chat(ra, cid, "Busca en internet el tiempo de hoy en Valencia.", model=GENERAL, thinking=False, web=False,
               max_tokens=400)
    assert not [e for e in evs if e["type"] == "tool_start" and e["name"] in ("web_search", "fetch_url")]


def test_parallel_subagents_report_back(ra):
    cid = new_conv(ra)
    evs = chat(ra, cid, "Lanza dos agentes 'revisor' en paralelo con delegate_task: uno que critique la frase "
                        "'La Tierra es plana' y otro la frase 'El agua hierve a 50 ºC a nivel del mar'. Luego resume "
                        "en dos líneas.", model=GENERAL, thinking=False, max_tokens=1500)
    starts = [e for e in evs if e["type"] == "tool_start" and e["name"] == "delegate_task"]
    results = [e for e in evs if e["type"] == "tool_result" and e["name"] == "delegate_task"]
    assert len(starts) >= 2 and len(results) >= 2, [e["type"] for e in evs][:40]
    assert all(r["ok"] and r["agent"]["report"] for r in results)
    # both started before either finished: they ran in parallel
    first_result = min(evs.index(r) for r in results)
    assert sum(1 for s in starts if evs.index(s) < first_result) >= 2


def test_skill_install_from_github_and_use_in_chat(ra):
    r = ra.post("/api/v1/skills/install", json={"source": "anthropics/skills/skills/internal-comms"})
    assert r.status_code == 200, r.text
    sk = r.json()
    assert sk["name"] == "internal-comms" and sk["files"], sk
    assert ra.get(f"/api/v1/skills/{sk['id']}").json()["content"]
    bad = ra.post("/api/v1/skills/install", json={"source": "http://127.0.0.1:8000/SKILL.md"})
    assert bad.status_code == 422
    cid = new_conv(ra)
    evs = chat(ra, cid, "Usa la skill internal-comms para redactar un aviso interno breve: el Spark estará en "
                        "mantenimiento el viernes de 9 a 11.", model=GENERAL, thinking=False, max_tokens=1200)
    assert any(e["type"] == "tool_result" and e["name"] == "use_skill" and e["ok"] for e in evs)
    assert ra.delete(f"/api/v1/skills/{sk['id']}").status_code == 200


def test_custom_agent_crud(ra):
    r = ra.post("/api/v1/agents", json={"name": "Revisor Normativa", "description": "normativa", "instructions": "Revisa normas.",
                                         "tools": ["web_search", "fetch_url"]})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["name"] == "revisor-normativa"
    assert ra.post("/api/v1/agents", json={"name": "x", "instructions": "y", "tools": ["generate_image"]}).status_code == 422
    assert ra.post("/api/v1/agents", json={"name": "z", "instructions": "y", "visibility": "public"}).status_code == 403
    names = [x["name"] for x in ra.get("/api/v1/agents").json()["agents"]]
    assert "investigador" in names and "revisor-normativa" in names
    assert ra.delete(f"/api/v1/agents/{a['id']}").status_code == 200


def test_context_window_auto_compaction_keeps_facts(ra):
    cid = new_conv(ra)
    filler = " ".join(["Texto de relleno sobre tolerancias y materiales poliméricos."] * 90)
    for fact in ["El código del proyecto es K-7781.", "La responsable es Marta Ibáñez.", "La entrega es el 14 de marzo.",
                 "El laboratorio está en el edificio 8G."]:
        chat(ra, cid, f"Anota: {fact} {filler} Responde solo 'anotado'.", model=GENERAL, thinking=False,
             tools=False, context_limit=6144, max_tokens=1024)
    conv = ra.get(f"/api/v1/playground/conversations/{cid}").json()
    assert conv["summary"] and conv["summary"]["compactions"] >= 1, conv.get("summary")
    evs = chat(ra, cid, "¿Cuál es el código del proyecto y quién es la responsable?", model=GENERAL, thinking=False,
               tools=False, context_limit=6144, max_tokens=300)
    ans = text_of(evs)
    assert "K-7781" in ans and "Marta" in ans, ans
    done = next(e for e in evs if e["type"] == "done")
    assert done["metrics"]["context_limit"] == 6144 and done["metrics"]["context_used"] <= 6144
    info = ra.get(f"/api/v1/playground/conversations/{cid}/context", params={"context_limit": 6144}).json()
    assert info["summary"] and info["messages_in_context"] < info["messages"]
    r = ra.post(f"/api/v1/playground/conversations/{cid}/compact", json={"context_limit": 6144})
    assert r.status_code == 200 and r.json()["ok"]
    assert ra.delete(f"/api/v1/playground/conversations/{cid}/summary").status_code == 200
    assert ra.get(f"/api/v1/playground/conversations/{cid}").json()["summary"] is None
