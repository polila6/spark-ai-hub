"""The users panel: who counts as active, and that nothing private reaches administrators."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import JobStatus
from app.services import people

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def test_presence_follows_the_last_activity():
    assert people.presence(NOW - timedelta(minutes=9), True, NOW) == "online"
    assert people.presence(NOW - timedelta(minutes=11), True, NOW) == "active"
    assert people.presence(NOW - timedelta(days=29), True, NOW) == "active"
    assert people.presence(NOW - timedelta(days=31), True, NOW) == "inactive"
    assert people.presence(None, True, NOW) == "never"
    assert people.presence(NOW, False, NOW) == "disabled"


def test_latest_ignores_what_never_happened():
    assert people.latest(None, NOW, NOW - timedelta(days=1)) == NOW
    assert people.latest(None, None) is None


def test_a_job_never_shows_its_title_or_params():
    job = SimpleNamespace(id="1", type="file_process", status=JobStatus.running, progress=0.5, created_at=NOW, started_at=NOW, finished_at=None,
                          title="Procesar informe-medico.pdf", params={"file_id": "x"}, message="m", result={"a": 1}, error="e")
    out = people.job_public(job)
    assert "informe-medico.pdf" not in str(out) and not {"title", "params", "message", "result", "error"} & set(out)


def test_a_request_never_shows_its_content():
    row = SimpleNamespace(ts=NOW, source="api", endpoint="/v1/chat", model="m", status_code=200, prompt_tokens=1, completion_tokens=2,
                          latency_ms=3.0, content={"prompt": "secreto"}, request_id="r", api_key_id="k")
    out = people.request_public(row)
    assert "secreto" not in str(out) and "content" not in out and out["prompt_tokens"] == 1
