"""First-run setup and branding: nothing about any organisation is built in; it comes from the setup or the environment."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import settings_store as S


def _fake_db(monkeypatch, values: dict, env_org: str = ""):
    @contextmanager
    def session():
        yield MagicMock()

    monkeypatch.setattr("app.db.SessionLocal", session)
    monkeypatch.setattr(S, "get_setting", lambda db, k: values.get(k, S.DEFAULTS.get(k)))
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(org_name=env_org))
    S.forget_branding()


def test_a_fresh_install_names_no_organisation_and_speaks_spanish(monkeypatch):
    _fake_db(monkeypatch, {})
    assert S.branding() == {"org_name": "", "default_language": "es"}


def test_the_organisation_chosen_in_the_setup_wins_over_the_environment(monkeypatch):
    _fake_db(monkeypatch, {"org_name": "Robotics Lab", "default_language": "en"}, env_org="From env")
    assert S.branding() == {"org_name": "Robotics Lab", "default_language": "en"}
    _fake_db(monkeypatch, {}, env_org="From env")
    assert S.branding()["org_name"] == "From env"


def test_an_unknown_default_language_falls_back_to_spanish(monkeypatch):
    _fake_db(monkeypatch, {"default_language": "fr"})
    assert S.branding()["default_language"] == "es"


def test_a_broken_database_never_breaks_a_prompt_or_an_email(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.SessionLocal", boom)
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(org_name="Env Org"))
    S.forget_branding()
    assert S.branding()["org_name"] == "Env Org"


def test_setup_is_offered_only_to_a_brand_new_hub(monkeypatch):
    from app.routers import admin

    db = MagicMock()
    monkeypatch.setattr(admin, "get_setting", lambda d, k: False)
    db.scalar.return_value = 1
    assert admin._needs_setup(db) is True
    db.scalar.return_value = 5  # a hub that already has people: never interrupt it
    assert admin._needs_setup(db) is False
    db.scalar.return_value = 1
    monkeypatch.setattr(admin, "get_setting", lambda d, k: True)  # done or skipped once
    assert admin._needs_setup(db) is False


def test_finishing_the_setup_stores_the_choices_and_marks_it_done(monkeypatch):
    from app.routers import admin

    saved = {}
    monkeypatch.setattr(admin, "set_setting", lambda db, k, v, uid: saved.__setitem__(k, v))
    monkeypatch.setattr(admin, "audit", lambda *a, **k: None)
    monkeypatch.setattr(admin, "branding", lambda: {"org_name": saved.get("org_name", ""), "default_language": saved.get("default_language", "es")})
    p = SimpleNamespace(user=SimpleNamespace(id="u1"))
    out = admin.finish_setup(admin.SetupIn(default_language="en", org_name="  Example Lab  "), MagicMock(), p, MagicMock())
    assert saved == {"default_language": "en", "org_name": "Example Lab", "setup_completed": True}
    assert out["ok"] and out["default_language"] == "en"
    saved.clear()
    admin.finish_setup(admin.SetupIn(), MagicMock(), p, MagicMock())  # skipping changes nothing but is remembered
    assert saved == {"setup_completed": True}
