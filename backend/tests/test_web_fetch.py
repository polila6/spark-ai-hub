"""Web fetching robustness: browser headers, retries, block detection, fallbacks and SSRF guards (no network)."""
import time
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import web

CF_CHALLENGE = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
                b"<script>window._cf_chl_opt={}</script></body></html>")
PAGE = b"<html><head><title>Hola</title></head><body><main><h1>Titular</h1><p>" + b"Texto largo. " * 60 + b"</p></main></body></html>"


def resp(status=200, body=PAGE, ct="text/html; charset=utf-8", url="https://example.org/", headers=None):
    return {"url": url, "status": status, "content_type": ct, "headers": headers or {}, "body": body}


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(web, "_public_ip", lambda host, port: "93.184.215.14")


# ------------------------------------------------------------------ headers and cookies


def test_browser_headers_look_like_a_real_browser():
    h = web.build_headers("chrome", "elpais.com")
    assert h["Host"] == "elpais.com" and "Chrome/" in h["User-Agent"] and "SparkAIHub" not in h["User-Agent"]
    assert h["Accept"].startswith("text/html") and h["Accept-Language"].startswith("es")
    assert h["Sec-Fetch-Mode"] == "navigate" and h["Sec-Fetch-Dest"] == "document" and h["Upgrade-Insecure-Requests"] == "1"
    assert "br" not in h["Accept-Encoding"] and "gzip" in h["Accept-Encoding"]  # only what httpx can decode here
    f = web.build_headers("firefox", "example.org", 8443)
    assert "Firefox/" in f["User-Agent"] and f["Host"] == "example.org:8443"


def test_api_requests_are_not_navigations_and_carry_cookies():
    h = web.build_headers("chrome", "api.github.com", accept="application/vnd.github+json", cookie="a=1")
    assert h["Accept"] == "application/vnd.github+json" and h["Cookie"] == "a=1"
    assert "Upgrade-Insecure-Requests" not in h and h["Sec-Fetch-Mode"] == "cors"
    b = web.build_headers("bot", "es.wikipedia.org", accept="application/json")
    assert "SparkAIHub" in b["User-Agent"] and "Sec-Fetch-Mode" not in b


def test_profile_order():
    assert web.profiles_for("es.wikipedia.org") == ("bot", "chrome")  # Wikimedia blocks fake browsers
    assert web.profiles_for("elpais.com") == ("chrome", "bot")         # elpais blocks bots


def test_cookie_jar_scopes_cookies_to_their_domain():
    jar = web.CookieJar()
    jar.store("www.example.org", ["sid=abc; Path=/; HttpOnly", "pref=1; Domain=.example.org",
                                  "evil=1; Domain=other.com", "bad cookie ;;; ="])
    assert jar.header("www.example.org") == "sid=abc; pref=1"
    assert jar.header("shop.example.org") == "pref=1" and jar.header("other.com") is None
    jar.store("www.example.org", ["sid=; Max-Age=0"])
    assert jar.header("www.example.org") == "pref=1"


# ------------------------------------------------------------------ retry decision


def test_retry_delay_backoff_and_retry_after():
    for attempt, (lo, hi) in enumerate([(0.8, 1.2), (1.6, 2.0), (3.2, 3.6)]):
        assert lo <= web.retry_delay(429, {}, attempt) <= hi
    assert web.retry_delay(503, {"retry-after": "3"}, 0) == 3
    assert web.retry_delay(429, {"retry-after": "120"}, 0) is None  # too long: give up and use another source
    soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=4), usegmt=True)
    assert 2 <= web.retry_delay(503, {"retry-after": soon}, 0) <= 4.5
    for status in (200, 301, 403, 404, 405):
        assert web.retry_delay(status, {}, 0) is None


def test_fetch_retries_5xx_then_returns(monkeypatch):
    calls = []
    answers = [resp(503), resp(502), resp(200)]
    monkeypatch.setattr(web, "_fetch_once", lambda *a, **k: calls.append(a[0]) or answers[len(calls) - 1])
    monkeypatch.setattr(web.time, "sleep", lambda s: None)
    assert web.fetch("https://example.org/")["status"] == 200 and len(calls) == 3


def test_fetch_does_not_retry_long_retry_after_or_challenges(monkeypatch):
    calls = []
    monkeypatch.setattr(web, "_fetch_once", lambda *a, **k: calls.append(1) or resp(429, headers={"retry-after": "600"}))
    monkeypatch.setattr(web.time, "sleep", lambda s: pytest.fail("should not wait"))
    assert web.fetch("https://example.org/")["status"] == 429 and len(calls) == 1
    calls.clear()
    monkeypatch.setattr(web, "_fetch_once", lambda *a, **k: calls.append(1) or resp(503, CF_CHALLENGE))
    assert web.fetch("https://example.org/")["status"] == 503 and len(calls) == 1


def test_fetch_network_error_message_is_clear(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(web, "_fetch_once", boom)
    with pytest.raises(web.WebError, match="tardó demasiado"):
        web.fetch("https://example.org/")


# ------------------------------------------------------------------ block detection


def test_block_detection():
    assert web.is_challenge(CF_CHALLENGE)
    assert web.is_challenge(b"<html><head><title>Comprobando tu navegador - reCAPTCHA</title></head></html>")
    assert not web.is_challenge(PAGE)
    big_normal = PAGE + b"<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>" + b"x" * 50_000
    assert not web.is_challenge(big_normal)
    article = b"<html><head><title>CAPTCHA - Wikipedia</title></head><body>" + b"<p>Un CAPTCHA es...</p>" * 8000
    assert not web.is_challenge(article)
    assert "anti-bots" in web.blocked_reason(resp(403, CF_CHALLENGE))
    assert "anti-bots" in web.blocked_reason(resp(200, CF_CHALLENGE))
    assert "405" in web.blocked_reason(resp(405, b""))
    assert "429" in web.blocked_reason(resp(429, b""))
    assert "no existe" in web.blocked_reason(resp(404, b"not found"))
    assert web.blocked_reason(resp(200)) is None


# ------------------------------------------------------------------ SSRF guards stay in place


def test_internal_addresses_still_refused():
    for url in ["http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://100.100.100.100/",
                "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://[fd7a:115c:a1e0::1]/",
                "ftp://example.com/", "http://user:pw@example.com/", "http://example.com:5432/", "http://example.com:99999/"]:
        with pytest.raises(web.URLRefused):
            web._check_url(url)


def test_refused_url_gets_no_retries_nor_archive(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda *a, **k: pytest.fail("must not fetch anything"))
    for url in ["http://169.254.169.254/latest/meta-data", "http://localhost:8000/", "file:///etc/passwd"]:
        with pytest.raises(web.URLRefused):
            web.read_page(url)


class _FakeStream:
    def __init__(self, status, headers, body=b""):
        self.status_code, self.headers, self._body = status, httpx.Headers(headers), body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    seen: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None, extensions=None):
        _FakeClient.seen.append((url, headers["Host"], extensions["sni_hostname"]))
        return _FakeStream(302, {"location": "http://127.0.0.1:80/admin", "set-cookie": "a=1"})


def test_redirect_to_internal_address_is_refused(monkeypatch):
    real = web._public_ip
    monkeypatch.setattr(web, "_public_ip", lambda host, port: "93.184.215.14" if host == "example.org" else real(host, port))
    monkeypatch.setattr(web.httpx, "Client", _FakeClient)
    _FakeClient.seen = []
    with pytest.raises(web.URLRefused):
        web.fetch("https://example.org/start", retries=0)
    # the first hop went to the checked IP with the real Host/SNI, and the redirect was never followed
    assert _FakeClient.seen == [("https://93.184.215.14/start", "example.org", "example.org")]


# ------------------------------------------------------------------ fallbacks


def test_archive_url():
    assert web.archive_url("https://www.ox.ac.uk/a?b=1") == "https://web.archive.org/web/2id_/https://www.ox.ac.uk/a?b=1"


def test_blocked_page_falls_back_to_internet_archive(monkeypatch, public_dns):
    monkeypatch.setattr(web, "_archive_pause_until", 0.0)
    calls = []

    def fake_fetch(url, **k):
        calls.append((url, k.get("profile")))
        if url.startswith("https://web.archive.org/"):
            return resp(200, PAGE, url="https://web.archive.org/web/20261027201112id_/https://www.ox.ac.uk/")
        return resp(403, CF_CHALLENGE, url=url)

    monkeypatch.setattr(web, "fetch", fake_fetch)
    p = web.read_page("https://www.ox.ac.uk/")
    assert [c[1] for c in calls[:2]] == ["chrome", "bot"]  # second header profile tried before the archive
    assert calls[2][0] == "https://web.archive.org/web/2id_/https://www.ox.ac.uk/"
    assert p["via"] == "archive" and p["url"] == "https://www.ox.ac.uk/" and "Titular" in p["text"]
    assert "Internet Archive el 27/10/2026" in p["text"] and "anti-bots" in p["note"]
    assert p["archive_url"] == "https://web.archive.org/web/20261027201112/https://www.ox.ac.uk/"


def test_failure_message_tells_the_model_what_to_do(monkeypatch, public_dns):
    monkeypatch.setattr(web, "_archive_pause_until", 0.0)
    monkeypatch.setattr(web, "fetch", lambda url, **k: resp(404, b"<html>nope</html>", url=url))
    with pytest.raises(web.WebError) as e:
        web.read_page("https://www.example.org/missing")
    msg = str(e.value)
    assert "HTTP 404" in msg and "Internet Archive" in msg and "otro resultado" in msg


def test_archive_backs_off_after_refusal(monkeypatch, public_dns):
    monkeypatch.setattr(web, "_archive_pause_until", 0.0)

    def refuse(url, **k):
        raise web.WebError("No se pudo abrir web.archive.org: x") from httpx.ConnectError("refused")

    monkeypatch.setattr(web, "fetch", refuse)
    assert web._archive_page("https://example.org/")[0] is None
    assert web._archive_pause_until > time.monotonic()
    monkeypatch.setattr(web, "fetch", lambda *a, **k: pytest.fail("archive should be paused"))
    assert "no está disponible" in web._archive_page("https://example.org/")[1]


def test_paper_ids():
    assert web.paper_ids("https://pubmed.ncbi.nlm.nih.gov/33301246/") == {"pmid": "33301246"}
    assert web.paper_ids("https://pmc.ncbi.nlm.nih.gov/articles/PMC7745181/") == {"pmcid": "PMC7745181"}
    assert web.paper_ids("https://europepmc.org/article/MED/33301246") == {"pmid": "33301246"}
    assert web.paper_ids("https://doi.org/10.1038/nature14539") == {"doi": "10.1038/nature14539"}
    assert web.paper_ids("https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201904981") == {"doi": "10.1002/anie.201904981"}
    assert web.paper_ids("https://link.springer.com/content/pdf/10.1007/s10462-020-09825-6.pdf") == {"doi": "10.1007/s10462-020-09825-6"}
    assert web.paper_ids("https://www.sciencedirect.com/science/article/pii/S0893608014002135") == {"pii": "S0893608014002135"}
    assert web.paper_ids("https://www.cell.com/cell/fulltext/S0092-8674(20)30262-2") == {"pii": "S0092867420302622"}
    assert web.paper_ids("https://www.jstor.org/stable/2280095") == {"doi": "10.2307/2280095"}
    assert web.paper_ids("https://elpais.com/espana/2026-10-30/x.html") == {}
    meta = b'<meta name="citation_doi" content="10.1109/5.726791">'
    assert web.paper_ids("https://ieeexplore.ieee.org/document/726791", meta) == {"doi": "10.1109/5.726791"}


def test_blocked_paper_page_returns_bibliographic_record(monkeypatch, public_dns):
    import json

    epmc = {"resultList": {"result": [{"title": "Safety and Efficacy.", "authorString": "Polack FP, Thomas SJ",
                                       "pubYear": "2020", "pmid": "33301246", "doi": "10.1056/NEJMoa2034577",
                                       "abstractText": "<h4>Background</h4>Severe acute respiratory syndrome...",
                                       "journalInfo": {"journal": {"title": "NEJM"}}}]}}
    openalex = {"title": "Safety and Efficacy", "publication_year": 2020, "cited_by_count": 9000,
                "authorships": [{"author": {"display_name": "Fernando P. Polack"}}],
                "primary_location": {"source": {"display_name": "New England Journal of Medicine"}},
                "best_oa_location": {"pdf_url": "https://www.nejm.org/doi/pdf/10.1056/NEJMoa2034577"}}

    def fake_fetch(url, **k):
        if "ebi.ac.uk" in url:
            assert "EXT_ID%3A33301246" in url
            return resp(200, json.dumps(epmc).encode(), "application/json", url)
        if "api.openalex.org" in url:
            assert url.endswith("doi:10.1056/NEJMoa2034577")
            return resp(200, json.dumps(openalex).encode(), "application/json", url)
        return resp(403, b"", url=url)

    monkeypatch.setattr(web, "fetch", fake_fetch)
    monkeypatch.setattr(web, "_archive_page", lambda url: pytest.fail("the record is enough"))
    p = web.read_page("https://pubmed.ncbi.nlm.nih.gov/33301246/")
    assert p["via"] == "europepmc" and p["url"] == "https://pubmed.ncbi.nlm.nih.gov/33301246/"
    assert "Severe acute respiratory syndrome" in p["text"] and "https://doi.org/10.1056/NEJMoa2034577" in p["text"]
    assert "no el texto completo" in p["text"] and "nejm.org/doi/pdf" in p["text"]


def test_openalex_abstract_is_rebuilt():
    assert web._inverted_abstract({"Deep": [0], "learning": [1, 3], "is": [2]}) == "Deep learning is learning"


# ------------------------------------------------------------------ text extraction


def test_home_page_with_many_articles_keeps_all_of_them():
    html = b"<html><body>" + b"".join(b"<article><h2>Noticia %d</h2><p>Resumen %d</p></article>" % (i, i)
                                      for i in range(20)) + b"</body></html>"
    _, text = web.html_to_text(html)
    assert "Noticia 0" in text and "Noticia 19" in text


def test_header_charset_is_honoured_and_utf8_bodies_survive_wrong_headers():
    latin = "<html><head><meta charset='utf-8'><title>Última hora</title></head><body><p>España</p></body></html>"
    title, text = web.html_to_text(latin.encode("latin-1"), encoding="iso8859-1")
    assert title == "Última hora" and "España" in text
    title, _ = web.html_to_text(latin.encode("utf-8"), encoding="iso8859-1")
    assert title == "Última hora"


def test_js_only_page_uses_meta_description():
    html = (b"<html><head><title>App</title><meta name='description' content='Portal de datos abiertos con "
            b"miles de conjuntos de datos del Gobierno de Espa\xc3\xb1a'></head><body><div id='root'></div></body></html>")
    _, text = web.html_to_text(html)
    assert "conjuntos de datos" in text


def test_js_only_page_is_not_archived(monkeypatch, public_dns):
    monkeypatch.setattr(web, "fetch", lambda url, **k: resp(200, b"<html><body><div id=root></div></body></html>", url=url))
    monkeypatch.setattr(web, "_archive_page", lambda url: pytest.fail("the copy would be the same empty shell"))
    with pytest.raises(web.WebError, match="JavaScript"):
        web.read_page("https://www.reddit.com/r/spain/")


# ------------------------------------------------------------------ search


class _R:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_search_falls_back_to_general_engines_and_reports_failures(monkeypatch):
    monkeypatch.setattr(web, "get_setting", lambda db, k: True if k == "web_search_enabled" else None)
    asked = []

    def fake_get(url, params=None, **k):
        asked.append(params["engines"])
        if params["engines"] == web.ENGINES["news"]:
            return _R({"results": [], "unresponsive_engines": [["google news", "access denied"]]})
        return _R({"results": [{"url": "https://a.es/x", "title": "A", "content": "c", "engine": "bing"}]})

    monkeypatch.setattr(web.httpx, "get", fake_get)
    res = web.search(None, "elecciones", "news")
    assert asked == [web.ENGINES["news"], web.ENGINES["web"]] and res[0]["url"] == "https://a.es/x"

    monkeypatch.setattr(web.httpx, "get", lambda url, params=None, **k: _R(
        {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["bing", "timeout"]]}))
    with pytest.raises(web.WebError, match="duckduckgo: CAPTCHA"):
        web.search(None, "algo", "web")
