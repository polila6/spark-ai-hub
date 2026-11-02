"""GET /mcp (Streamable HTTP optional SSE stream): authenticated, idle, closed cleanly with a reconnection hint."""
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.routers import mcp_server


def client(monkeypatch, authorised=True):
    def auth(request):
        if authorised:
            return uuid.uuid4(), None
        return None, JSONResponse(status_code=401, content={"error": "no key"}, headers={"WWW-Authenticate": "Bearer"})

    monkeypatch.setattr(mcp_server, "_authenticate", auth)
    app = FastAPI()
    app.include_router(mcp_server.router)
    return TestClient(app)


def test_get_requires_api_key(monkeypatch):
    r = client(monkeypatch, authorised=False).get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 401 and "WWW-Authenticate" in r.headers


def test_get_without_sse_accept_is_406(monkeypatch):
    assert client(monkeypatch).get("/mcp", headers={"Accept": "application/json"}).status_code == 406


def test_get_opens_idle_stream_that_ends_with_retry_hint(monkeypatch):
    monkeypatch.setattr(mcp_server, "SSE_MAX_S", 2.5)
    monkeypatch.setattr(mcp_server, "SSE_KEEPALIVE_S", 1)
    c = client(monkeypatch)
    with c.stream("GET", "/mcp", headers={"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-06-18"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert body.startswith("retry: 5000\n\n") and ": keep-alive\n\n" in body and "data:" not in body
    assert mcp_server._sse_streams == 0
    with c.stream("GET", "/mcp", headers={"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-11-25"}) as r:
        first = next(r.iter_text())
    assert first.startswith("id: ") and "retry: 5000" in first and "data:\n\n" in first  # 2025-11-25 priming event


def test_too_many_streams_falls_back_to_405(monkeypatch):
    monkeypatch.setattr(mcp_server, "_sse_streams", mcp_server.SSE_MAX_STREAMS)
    r = client(monkeypatch).get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405 and r.headers["allow"] == "POST"
