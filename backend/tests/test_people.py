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


def test_consent_needs_the_new_acceptance_unless_the_institution_authorises_it():
    nobody = SimpleNamespace(content_access_accepted_at=None, terms_accepted_at=NOW, terms_version=1)
    assert not people.consented(nobody) and not people.consented(nobody, "consent")
    assert people.consented(SimpleNamespace(content_access_accepted_at=NOW), "consent")
    assert people.consented(nobody, "institutional")


def test_the_reason_is_required_only_in_consent_mode():
    for bad in ("", "   ", "porque si"):
        assert not people.reason_ok("consent", bad)
        assert people.reason_ok("institutional", bad)
    assert people.reason_ok("consent", "soporte pedido por el usuario")


def test_a_person_sees_who_consulted_what_and_why():
    event = SimpleNamespace(ts=NOW, actor_name="admin", details={"kind": "file", "what": "a.pdf", "reason": "soporte pedido por ti"}, ip="1.2.3.4")
    assert people.access_public(event) == {"ts": NOW, "by": "admin", "kind": "file", "what": "a.pdf", "reason": "soporte pedido por ti"}


def test_the_notice_to_everyone_says_what_changes_and_that_it_is_logged_and_visible():
    text = people.NOTICE_BODY
    assert "ES:" in text and "EN:" in text and "Ajustes → Privacidad" in text and "Settings → Privacy" in text
    assert "registrada" in text and "logged" in text


def test_the_terms_tell_the_truth_in_each_mode():
    from app.routers.auth import _default_terms
    consent, institutional = _default_terms("consent"), _default_terms("institutional")
    assert "Un administrador puede consultarlos" in consent["es"] and "indicando un motivo" in consent["es"]
    assert "8. " in consent["es"] and "8. " in consent["en"]
    for lang in ("es", "en"):
        text = institutional[lang]
        assert "no leen contenido" not in text and "do not read content" not in text
        assert "8. " not in text and "{" not in text
    assert "normativa de uso de los sistemas de información" in institutional["es"] and "Ajustes → Privacidad" in institutional["es"]
    assert "under your institution's rules" in institutional["en"] and "Settings → Privacy" in institutional["en"]


def test_switching_to_institutional_tells_every_active_person_once(monkeypatch):
    told, saved, audited = [], {}, []
    users = [SimpleNamespace(id=i) for i in range(3)]

    class Db:
        def scalars(self, _q):
            return users

        def commit(self):
            pass

    monkeypatch.setattr(people, "get_setting", lambda db, k: saved.get(k, "consent"))
    monkeypatch.setattr(people, "set_setting", lambda db, k, v, uid=None: saved.__setitem__(k, v))
    monkeypatch.setattr(people, "audit", lambda db, action, **kw: audited.append((action, kw["details"])))
    from app.services import notify
    monkeypatch.setattr(notify, "notify", lambda db, uid, kind, title, body, link=None: told.append((uid, kind, link)))
    out = people.switch_mode(Db(), "institutional", actor=None, via="test")
    assert out == {"mode": "institutional", "notified": 3} and [t[0] for t in told] == [0, 1, 2] and told[0][1:] == ("policy", "/settings")
    assert audited[-1][1] == {"from": "consent", "to": "institutional", "notified": 3, "via": "test"}
    again = people.switch_mode(Db(), "institutional")
    assert again["notified"] == 0 and len(told) == 3, "a second switch must not tell everyone again"
    back = people.switch_mode(Db(), "consent")
    assert back["notified"] == 0 and saved["content_access_mode"] == "consent"
    import pytest
    with pytest.raises(ValueError):
        people.switch_mode(Db(), "covert")


def test_the_terms_endpoint_reads_the_mode_and_serves_text(monkeypatch):
    """Caught while testing: terms_info used `people` without importing it, so /auth/me and /auth/terms failed for everyone."""
    from app.routers import auth
    monkeypatch.setattr(auth, "get_setting", lambda db, key: {"terms_version": 3, "terms_text": None}.get(key))
    monkeypatch.setattr(auth.people, "content_mode", lambda db: "institutional")
    info = auth.terms_info(None)
    assert info["version"] == 3 and "normativa de uso" in info["text"]["es"] and info["text"]["en"]
    monkeypatch.setattr(auth.people, "content_mode", lambda db: "consent")
    assert "Un administrador puede consultarlos" in auth.terms_info(None)["text"]["es"]
    assert auth.terms_cover_content(None) is True
    monkeypatch.setattr(auth.people, "content_mode", lambda db: "institutional")
    assert auth.terms_cover_content(None) is False
