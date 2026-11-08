"""Bigger web tools: many pages at once, API calls, crawling, snapshots, saving what was fetched.
Run: docker compose run --rm --no-deps backend pytest -q tests/test_web_tools.py"""
import json
import threading
from types import SimpleNamespace

import pytest

from app.services import agent as agent_mod
from app.services import web, web_tools as wt


def test_clean_headers_drops_dangerous_ones_and_header_injection():
    out = wt.clean_headers({"Host": "evil", "Cookie": "a=b", "Content-Length": "9", "Authorization": "Bearer x", "X-Ok": "1",
                            "Bad\r\nName": "v", "X-Split": "a\r\nInjected: 1"})
    assert out == {"Authorization": "Bearer x", "X-Ok": "1"}


def test_request_builds_query_json_body_and_refuses_other_methods(monkeypatch):
    seen = {}

    def fake_fetch(url, **kw):
        seen.update(url=url, **kw)
        return {"url": url, "status": 200, "content_type": "application/json", "body": b"{}", "headers": {}}
    monkeypatch.setattr(web, "fetch", fake_fetch)
    wt.request("https://api.example.org/v1/items?a=1", "get", {"q": "x y", "tags": ["a", "b"]})
    assert seen["url"] == "https://api.example.org/v1/items?a=1&q=x+y&tags=a%2Cb" and seen["method"] == "GET" and seen["content"] is None
    wt.request("https://api.example.org/", "POST", None, {"X-Key": "k", "Cookie": "no"}, {"n": 1})
    assert seen["method"] == "POST" and json.loads(seen["content"]) == {"n": 1}
    assert seen["extra_headers"]["Content-Type"] == "application/json" and "Cookie" not in seen["extra_headers"]
    wt.request("https://api.example.org/", "POST", None, None, None, {"a": "b"})
    assert seen["content"] == b"a=b"
    with pytest.raises(wt.ToolError, match="GET y POST"):
        wt.request("https://api.example.org/", "DELETE")
    with pytest.raises(wt.ToolError, match="200 KB"):
        wt.request("https://api.example.org/", "POST", None, None, {"x": "y" * 300_000})


def test_request_turns_network_refusals_into_tool_errors(monkeypatch):
    def boom(*a, **k):
        raise web.URLRefused("Dirección no permitida: es una dirección privada o interna")
    monkeypatch.setattr(web, "fetch", boom)
    with pytest.raises(wt.ToolError, match="privada"):
        wt.request("http://192.168.1.1/")


def test_render_body_pretty_prints_json_and_truncates():
    short = wt.render_body(b'{"a": [1, 2]}', "application/json", 1000)
    assert '"a": [' in short and "\n" in short
    big = wt.render_body(json.dumps({"rows": list(range(5000))}).encode(), "application/json", 300)
    assert len(big) < 600 and "save_to" in big
    assert wt.render_body(b"a,b\n1,2", "text/csv", 100) == "a,b\n1,2"


def test_links_of_skips_assets_fragments_and_javascript():
    html = (b'<a href="/a">A</a><a href="b.html#x">B</a><a href="#top">t</a><a href="mailto:x@y.z">m</a>'
            b'<a href="/img/p.PNG">i</a><a href="javascript:void(0)">j</a><a href="https://other.org/c">C</a><a href="/a">dup</a>')
    assert wt.links_of(html, "https://site.org/dir/") == ["https://site.org/a", "https://site.org/dir/b.html", "https://other.org/c"]


def test_excerpt_picks_the_relevant_paragraphs():
    text = "\n\n".join([f"parrafo {i} sin nada" for i in range(50)] + ["la tasa de paro fue del 11,2 % en octubre"])
    out = wt.excerpt(text, "tasa de paro", 120)
    assert "tasa de paro" in out and len(out) <= 200
    assert wt.excerpt("corto", None, 100) == "corto"
    assert wt.excerpt("x" * 300, None, 100).endswith("[... recortado]")


def test_slug_for():
    assert wt.slug_for("https://site.org/docs/intro/") == "docs-intro"
    assert wt.slug_for("https://site.org/") == "index"
    assert wt.slug_for("https://site.org/a b?id=5&x=y") == "a-b-id-5-x-y"


SITE = {
    "https://site.org/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private\n"),
    "https://site.org/": (200, "text/html", b'<html><title>Home</title><body><p>' + b"home " * 60 + b'</p><a href="/docs/a">a</a><a href="/docs/b">b</a>'
                                              b'<a href="/private/x">p</a><a href="/blog/z">z</a><a href="https://other.org/o">o</a></body></html>'),
    "https://site.org/docs/a": (200, "text/html", b'<html><title>A</title><body><p>' + b"alfa " * 60 + b'</p><a href="/docs/c">c</a></body></html>'),
    "https://site.org/docs/b": (200, "text/html", b'<html><title>B</title><body><p>' + b"beta " * 60 + b'</p></body></html>'),
    "https://site.org/docs/c": (200, "text/html", b'<html><title>C</title><body><p>' + b"gamma " * 60 + b'</p></body></html>'),
    "https://site.org/blog/z": (200, "text/html", b'<html><title>Z</title><body><p>' + b"zeta " * 60 + b'</p></body></html>'),
}


def _fake_site(monkeypatch):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        st, ct, body = SITE.get(url, (404, "text/html", b"nope"))
        return {"url": url, "status": st, "content_type": ct, "body": body, "headers": {}}
    monkeypatch.setattr(web, "fetch", fake_fetch)
    monkeypatch.setattr(web, "_check_url", lambda u: ("site.org", "1.2.3.4", 443))
    monkeypatch.setattr(wt.time, "sleep", lambda s: None)
    return calls


def test_crawl_honours_robots_pattern_depth_and_same_host(monkeypatch):
    calls = _fake_site(monkeypatch)
    out = wt.crawl("https://site.org/", max_pages=10, depth=2, pattern=r"/docs/")
    urls = [p["url"] for p in out["pages"]]
    assert urls == ["https://site.org/", "https://site.org/docs/a", "https://site.org/docs/b", "https://site.org/docs/c"]
    assert "https://site.org/private/x" not in calls and "https://other.org/o" not in calls and "https://site.org/blog/z" not in calls
    assert out["skipped"]["otro dominio"] == 1 and out["skipped"]["no coincide"] >= 1


def test_crawl_depth_zero_and_max_pages(monkeypatch):
    _fake_site(monkeypatch)
    assert [p["url"] for p in wt.crawl("https://site.org/", depth=0)["pages"]] == ["https://site.org/"]
    assert len(wt.crawl("https://site.org/", max_pages=2, depth=2)["pages"]) == 2


def test_crawl_robots_blocks_a_disallowed_start_path(monkeypatch):
    _fake_site(monkeypatch)
    SITE["https://site.org/private/x"] = (200, "text/html", b"<html><body>secret</body></html>")
    out = wt.crawl("https://site.org/private/x", depth=0)
    assert out["pages"] == [] and out["skipped"]["robots.txt"] == 1


# ---------------------------------------------------------------- agent tools

class _Svc:
    def __init__(self):
        self.files = {}

    def write(self, db, ws, path, data):
        self.files[path] = data
        return {"path": path, "size": len(data)}


class _Db:
    def commit(self):
        pass


def _runner(monkeypatch):
    r = object.__new__(agent_mod.Runner)
    r.workspace, r.project_id = SimpleNamespace(id="w", name="ws"), None
    r.emit, r.stop = (lambda e: None), (lambda: False)
    svc = _Svc()
    r._ws_call = lambda fn: fn(_Db(), SimpleNamespace(), svc)
    return r, svc


def test_web_search_passes_site_filetype_page_and_limit(monkeypatch):
    r, _ = _runner(monkeypatch)
    seen = {}

    def fake_search(db, q, cat, lang, limit, tr, page):
        seen.update(q=q, limit=limit, page=page)
        return [{"title": "T", "url": "https://x.org/a.pdf", "snippet": "s", "published": None}]

    class FS:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(agent_mod, "SessionLocal", FS)
    monkeypatch.setattr(agent_mod.web, "search", fake_search)
    res = r.tool_web_search("c", {"query": "paro registrado", "site": "https://www.sepe.es/algo", "filetype": ".PDF", "page": 2, "limit": 50})
    assert seen == {"q": "paro registrado site:www.sepe.es filetype:pdf", "limit": 20, "page": 2}
    assert "página 2" in res["summary"] and "fetch_urls" in res["content"]


def test_fetch_url_saves_full_text_and_shows_an_excerpt(monkeypatch):
    r, svc = _runner(monkeypatch)
    long = "x " * 20000
    monkeypatch.setattr(agent_mod.web, "read_page", lambda u: {"url": u, "title": "Gran página", "text": long, "content_type": "text/html"})
    res = r.tool_fetch_url("c", {"url": "https://a.org/p", "save_to": "web/p.md"})
    assert svc.files["web/p.md"].startswith(b"# Gran p") and len(svc.files["web/p.md"]) > 35000
    assert res["workspace"]["changed"] == ["web/p.md"] and len(res["content"]) < len(long) and "guardado" in res["content"]


def test_fetch_url_as_of_uses_the_archive(monkeypatch):
    r, _ = _runner(monkeypatch)
    monkeypatch.setattr(agent_mod.web, "wayback_page", lambda u, d: {"url": u, "title": "Antes", "text": "[Nota: copia] viejo", "content_type": "text/html",
                                                                  "via": "archive", "note": "[Nota: copia]"})
    monkeypatch.setattr(agent_mod.web, "read_page", lambda u: pytest.fail("must read the snapshot, not the live page"))
    res = r.tool_fetch_url("c", {"url": "https://a.org/p", "as_of": "2025-03-01"})
    assert "Internet Archive" in res["summary"]


def test_fetch_urls_reads_in_parallel_reports_failures_and_saves(monkeypatch):
    r, svc = _runner(monkeypatch)
    monkeypatch.setattr(wt, "read_many", lambda urls: [
        {"url": urls[0], "ok": True, "title": "Uno", "text": "contenido uno " * 50, "final_url": urls[0]},
        {"url": urls[1], "ok": False, "error": "403 Filtered"}])
    res = r.tool_fetch_urls("c", {"urls": ["https://a.org/1", "https://a.org/2"], "query": "uno", "save_dir": "web/"})
    assert res["summary"].startswith("1/2") and "✗ https://a.org/2" in res["content"]
    assert list(svc.files) == ["web/1.md"] and res["workspace"]["changed"] == ["web/1.md"]
    monkeypatch.setattr(wt, "read_many", lambda urls: [{"url": u, "ok": False, "error": "x"} for u in urls])
    with pytest.raises(agent_mod.AgentError, match="ninguna"):
        r.tool_fetch_urls("c", {"urls": "https://a.org/1 https://a.org/2"})
    with pytest.raises(agent_mod.AgentError, match="lista"):
        r.tool_fetch_urls("c", {"urls": []})


def test_http_request_returns_data_saves_raw_and_reports_errors(monkeypatch):
    r, svc = _runner(monkeypatch)
    body = json.dumps({"rows": [{"a": 1}]}).encode()
    monkeypatch.setattr(wt, "request", lambda *a, **k: {"url": "https://api.org/d", "status": 200, "content_type": "application/json", "body": body})
    res = r.tool_http_request("c", {"url": "https://api.org/d", "save_to": "data/d.json"})
    assert svc.files["data/d.json"] == body and '"rows"' in res["content"] and "guardada" in res["content"]
    monkeypatch.setattr(wt, "request", lambda *a, **k: {"url": "https://api.org/d", "status": 404, "content_type": "text/html", "body": b"nope"})
    res = r.tool_http_request("c", {"url": "https://api.org/d", "save_to": "data/e.json"})
    assert res["ok"] is False and "404" in res["summary"] and "data/e.json" not in svc.files  # nothing saved for an error
    monkeypatch.setattr(wt, "request", lambda *a, **k: (_ for _ in ()).throw(wt.ToolError("Puerto 22 no permitido")))
    with pytest.raises(agent_mod.AgentError, match="Puerto"):
        r.tool_http_request("c", {"url": "http://x.org:22/"})


def test_crawl_site_summarises_and_saves_pages(monkeypatch):
    r, svc = _runner(monkeypatch)
    pages = [{"url": f"https://s.org/p{i}", "title": f"P{i}", "text": "texto " * 100, "depth": min(i, 1)} for i in range(3)]
    monkeypatch.setattr(wt, "crawl", lambda *a, **k: {"pages": pages, "skipped": {"robots.txt": 2}, "queued": 5, "seconds": 3.2})
    res = r.tool_crawl_site("c", {"url": "https://s.org/", "save_dir": "web/s"})
    assert sorted(svc.files) == ["web/s/p0.md", "web/s/p1.md", "web/s/p2.md"] and "robots.txt 2" in res["summary"]
    monkeypatch.setattr(wt, "crawl", lambda *a, **k: {"pages": [], "skipped": {}, "queued": 0, "seconds": 1})
    with pytest.raises(agent_mod.AgentError, match="ninguna página"):
        r.tool_crawl_site("c", {"url": "https://s.org/"})


def test_api_keys_in_headers_never_reach_the_history():
    out = agent_mod.public_args("http_request", {"url": "https://a.org", "headers": {"Authorization": "Bearer secret", "X-API-Key": "k", "Accept": "text/csv"}})
    assert out["headers"] == {"Authorization": "«oculto»", "X-API-Key": "«oculto»", "Accept": "text/csv"}
    other = {"query": "x"}
    assert agent_mod.public_args("web_search", other) is other


def test_new_web_tools_exist_are_core_for_small_models_where_needed_and_sub_agent_tools():
    assert {"fetch_urls", "http_request", "crawl_site"} <= agent_mod.SUB_AGENT_TOOLS
    assert "fetch_urls" in agent_mod.CORE_TOOLS
    names = {t["function"]["name"] for t in agent_mod.WEB_TOOLS}
    assert {"web_search", "fetch_url", "fetch_urls", "http_request", "crawl_site", "search_images", "download_file", "view_image"} <= names
    for t in agent_mod.WEB_TOOLS:  # every spec is valid JSON-schema-ish and its method exists
        assert hasattr(agent_mod.Runner, "tool_" + t["function"]["name"]) and t["function"]["parameters"]["type"] == "object"


def _sub_runner(monkeypatch, steps):
    r = object.__new__(agent_mod.Runner)
    r.agents = {"escritor": {"description": "x", "instructions": "y", "tools": ["fetch_url", "ws_write"], "skills": []},
                "lector": {"description": "x", "instructions": "y", "tools": ["fetch_url"], "skills": []}}
    r.t = SimpleNamespace(lang="es", model_id="m", user_id=None)
    r.workspace, r.project_id, r.flags = SimpleNamespace(id="w", name="ws"), None, {"web": True, "docs": True, "skills": True, "agents": True, "code": True}
    r.ws_claims, r.ws_claims_lock = {}, threading.Lock()
    r.stop, r.emit, r.small = (lambda: False), (lambda e: None), False
    r.image_meta, r.image_failures, r._vision_cache = {}, 0, False
    r._all_specs, r._fit = (lambda: [{"function": {"name": "fetch_url"}}, {"function": {"name": "ws_write"}}]), (lambda *a, **k: None)
    seq = iter(steps)
    r._llm_step = lambda messages, specs, max_out, thinking=False: (*next(seq), None)
    r.executed = []
    r._exec = lambda cid, name, args: r.executed.append((name, args)) or {"ok": True, "summary": "ok", "content": "ok"}
    monkeypatch.setattr(agent_mod.scheduler, "ensure_loaded", lambda *a, **k: SimpleNamespace(id="i"))
    monkeypatch.setattr(agent_mod.scheduler, "begin_request", lambda *a: None)
    monkeypatch.setattr(agent_mod.scheduler, "end_request", lambda *a: None)
    return r


def _call(name, args):
    return {0: {"id": "t1", "name": name, "args": json.dumps(args)}}


def test_a_sub_agent_may_save_fetched_pages_only_inside_its_write_zone(monkeypatch):
    r = _sub_runner(monkeypatch, [("", _call("fetch_url", {"url": "https://a.org", "save_to": "src/ok.md"})),
                                  ("", _call("fetch_url", {"url": "https://a.org", "save_to": "docs/no.md"})), ("fin", {})])
    r.tool_delegate_task("c", {"agent": "escritor", "task": "lee y guarda", "write_paths": ["src"]})
    assert [a.get("save_to") for n, a in r.executed] == ["src/ok.md"]  # the second save is refused before it runs


def test_a_read_only_sub_agent_cannot_save_at_all(monkeypatch):
    r = _sub_runner(monkeypatch, [("", _call("fetch_url", {"url": "https://a.org", "save_to": "x.md"})),
                                  ("", _call("fetch_url", {"url": "https://a.org"})), ("fin", {})])
    r.tool_delegate_task("c", {"agent": "lector", "task": "lee"})
    assert [a.get("save_to") for n, a in r.executed] == [None]  # reading is fine, saving is refused
