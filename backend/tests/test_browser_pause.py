"""Isolated page browser (client + fallback in the page reader), the filtering proxy's address rules, plan phases, pause and
«only the conclusion» export. Pure logic with fake network."""
import importlib.util
import pathlib

import httpx
import pytest

from app.routers.playground import conclusion_of
from app.services import browser, turns, web
from app.services.agent import Runner


def _proxy():
    path = pathlib.Path(__file__).resolve().parents[2] / "infrastructure" / "webproxy" / "proxy.py"
    if not path.exists():
        pytest.skip("infrastructure/ is not next to backend/ here")
    spec = importlib.util.spec_from_file_location("sah_webproxy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "172.17.0.2", "169.254.169.254", "100.100.100.100",
                                "::1", "fe80::1", "fd00::1", "::ffff:10.0.0.1", "2002:c0a8:0101::1", "64:ff9b::a00:1", "224.0.0.1",
                                "198.18.0.1", "0.0.0.0"])
def test_proxy_refuses_private_addresses(ip):
    assert not _proxy().public(ip)


@pytest.mark.parametrize("ip", ["1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_proxy_allows_public_addresses(ip):
    assert _proxy().public(ip)


def test_proxy_refuses_odd_ports():
    import asyncio
    px = _proxy()
    with pytest.raises(px.Refused):
        asyncio.run(px.resolve("example.com", 5432))


def test_blocked_page_detection():
    assert browser.looks_blocked("Just a moment...")
    assert browser.looks_blocked("x" * 600, status=403)
    assert not browser.looks_blocked("Artículo largo " * 100)


def test_render_refuses_private_urls_before_asking_the_browser(monkeypatch):
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append(1))
    with pytest.raises(web.WebError):
        browser.render("http://127.0.0.1:8000/admin")
    assert not called


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


def test_render_returns_page_and_screenshot(monkeypatch):
    monkeypatch.setattr(web, "_check_url", lambda u: None)
    monkeypatch.setattr(browser, "_check_url", lambda u: None)
    import base64
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"ok": True, "final_url": "https://a.org/x", "status": 200, "title": "T",
                        "text": "hola " * 80, "links": [], "images": [], "screenshot_b64": base64.b64encode(b"JPG").decode()}))
    d = browser.render("https://a.org/x", screenshot=True)
    assert d["title"] == "T" and d["screenshot"] == b"JPG" and d["status"] == 200


def test_render_reports_proxy_block_and_busy(monkeypatch):
    monkeypatch.setattr(browser, "_check_url", lambda u: None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"ok": True, "text": "Bloqueado: 10.0.0.1 no es una dirección pública"}))
    with pytest.raises(web.WebError, match="no permitida"):
        browser.render("https://a.org/")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(429, {}))
    with pytest.raises(web.WebError, match="ocupado"):
        browser.render("https://a.org/")


def test_render_when_browser_is_down(monkeypatch):
    monkeypatch.setattr(browser, "_check_url", lambda u: None)

    def boom(*a, **k):
        raise httpx.ConnectError("x")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(web.WebError, match="no está disponible"):
        browser.render("https://a.org/")


def test_page_reader_falls_back_to_the_browser_for_js_pages(monkeypatch):
    shell = {"url": "https://spa.org/", "status": 200, "content_type": "text/html", "body": b"<html><body><div id=app></div></body></html>"}
    monkeypatch.setattr(web, "_check_url", lambda u: ("https://spa.org/", "spa.org", 443))
    monkeypatch.setattr(web, "fetch", lambda url, **k: shell)
    monkeypatch.setattr(browser, "available", lambda: True)
    monkeypatch.setattr(browser, "render", lambda url, **k: {"final_url": url, "status": 200, "title": "SPA", "text": "Contenido real " * 40})
    p = web.read_page("https://spa.org/")
    assert p["via"] == "browser" and "Contenido real" in p["text"] and "navegador aislado" in p["note"]


def test_page_reader_skips_the_browser_when_it_is_down(monkeypatch):
    shell = {"url": "https://spa.org/", "status": 200, "content_type": "text/html", "body": b"<html><body></body></html>"}
    monkeypatch.setattr(web, "_check_url", lambda u: ("https://spa.org/", "spa.org", 443))
    monkeypatch.setattr(web, "fetch", lambda url, **k: shell)
    monkeypatch.setattr(browser, "available", lambda: False)
    monkeypatch.setattr(web, "_archive_page", lambda u: (None, ""))
    with pytest.raises(web.WebError):
        web.read_page("https://spa.org/")


def test_update_plan_opens_a_phase_once_per_current_step():
    r = Runner.__new__(Runner)
    import threading
    r.lock = threading.RLock()
    r.parts, r.plan, r.plan_part, r.emit = [], [], None, lambda ev: None
    r._update_plan({"items": [{"content": "Buscar fuentes", "status": "in_progress"}, {"content": "Redactar", "status": "pending"}]})
    r._update_plan({"items": [{"content": "Buscar fuentes", "status": "in_progress"}, {"content": "Redactar", "status": "pending"}]})
    r._update_plan({"items": [{"content": "Buscar fuentes", "status": "completed"}, {"content": "Redactar", "status": "in_progress"}]})
    phases = [p["title"] for p in r.parts if p["type"] == "phase"]
    assert phases == ["Buscar fuentes", "Redactar"]


def test_pause_flag(monkeypatch):
    store = {}

    class R:
        def set(self, k, v, ex=None, nx=False):
            store[k] = v
            return True

        def exists(self, k):
            return k in store
    monkeypatch.setattr(turns, "get_redis", lambda: R())
    chk = turns.pause_checker("c1")
    assert not chk()
    turns.pause("c1")
    chk2 = turns.pause_checker("c1")
    assert chk2()


def test_conclusion_of_takes_the_last_comment_only_after_the_steps():
    m = {"role": "assistant", "content": "Paso uno hecho.\n\nCONCLUSIÓN FINAL", "parts": [
        {"type": "say", "text": "Paso uno hecho."}, {"type": "tool", "name": "ws_run"}, {"type": "say", "text": "CONCLUSIÓN FINAL"}]}
    assert conclusion_of(m) == "CONCLUSIÓN FINAL"
    m2 = {**m, "parts": m["parts"][:2]}  # ends on a step: nothing to cut
    assert conclusion_of(m2) == m["content"]
    assert conclusion_of({"role": "user", "content": "hola", "parts": None}) == "hola"


def test_thin_page_with_scripts_is_rendered_but_plain_short_pages_are_not(monkeypatch):
    frame = {"url": "https://spa.org/", "status": 200, "content_type": "text/html",
             "body": b"<html><head><script src=a.js></script></head><body><h1>Quotes</h1><p>Login</p></body></html>"}
    monkeypatch.setattr(web, "_check_url", lambda u: ("https://spa.org/", "spa.org", 443))
    monkeypatch.setattr(web, "fetch", lambda url, **k: frame)
    monkeypatch.setattr(browser, "available", lambda: True)
    calls = []
    monkeypatch.setattr(browser, "render", lambda url, **k: calls.append(url) or {"final_url": url, "status": 200, "title": "Q", "text": "Cita " * 200})
    assert web.read_page("https://spa.org/")["via"] == "browser"
    plain = {**frame, "body": b"<html><body><h1>Hola</h1><p>Una pagina corta.</p></body></html>"}
    monkeypatch.setattr(web, "fetch", lambda url, **k: plain)
    calls.clear()
    assert web.read_page("https://spa.org/")["via"] == "direct" and not calls
