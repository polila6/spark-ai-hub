"""Following Tailscale changes: the host's sync script and the hub's live address (pure logic)."""
import importlib.util
import json
from pathlib import Path

import pytest

from app import config

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tailnet-sync.py"
if not SCRIPT.exists():  # the backend image only ships backend/
    pytest.skip("scripts/ not available here", allow_module_level=True)
spec = importlib.util.spec_from_file_location("tailnet_sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

STATUS = {"BackendState": "Running", "CurrentTailnet": {"Name": "team@example.com"}, "CertDomains": None,
          "Self": {"DNSName": "spark.tailabc.ts.net.", "TailscaleIPs": ["100.66.0.5", "fd7a::1"], "KeyExpiry": "2027-05-02T11:16:42Z"}}


def test_status_is_parsed_only_when_logged_in():
    me = sync.parse_status(json.dumps(STATUS))
    assert me["dns"] == "spark.tailabc.ts.net" and me["ipv4"] == "100.66.0.5" and me["ipv6"] == "fd7a::1"
    assert me["account"] == "team@example.com" and me["https"] is False and me["key_expiry"].startswith("2027")
    assert sync.parse_status(json.dumps({**STATUS, "BackendState": "NeedsLogin"})) is None  # keep the last known address
    assert sync.parse_status("not json") is None
    never = {**STATUS, "Self": {**STATUS["Self"], "KeyExpiry": "0001-01-01T00:00:00Z"}}
    assert sync.parse_status(json.dumps(never))["key_expiry"] is None


def test_custom_addresses_are_never_rewritten():
    assert sync.is_managed(None) and sync.is_managed("https://a.old.ts.net") and sync.is_managed("http://localhost:8080")
    assert not sync.is_managed("https://hub.example.org")


def test_env_is_updated_in_place():
    text = "A=1\nSAH_PUBLIC_BASE_URL=https://a.old.ts.net\nB=2\n"
    assert sync.env_url(text) == "https://a.old.ts.net"
    assert sync.with_env_url(text, "https://b.new.ts.net") == "A=1\nSAH_PUBLIC_BASE_URL=https://b.new.ts.net\nB=2\n"
    assert sync.with_env_url("A=1\n", "https://b.new.ts.net").endswith("SAH_PUBLIC_BASE_URL=https://b.new.ts.net\n")


def test_serve_check_needs_our_target_on_this_name():
    good = {"Web": {"spark.tailabc.ts.net:443": {"Handlers": {"/": {"Proxy": sync.TARGET}}}}}
    assert sync.serve_ok(json.dumps(good), "spark.tailabc.ts.net")
    assert not sync.serve_ok(json.dumps(good), "spark.other.ts.net")  # a config left from the previous tailnet
    assert not sync.serve_ok("{}", "spark.tailabc.ts.net")


def test_serve_problems_are_told_apart():
    assert sync.serve_problem("Access denied: serve config denied\nUse 'sudo tailscale serve'") == ("needs_operator", None)
    code, url = sync.serve_problem("Serve is not enabled on your tailnet.\nTo enable, visit:\n  https://login.tailscale.com/f/serve?node=x\n")
    assert code == "https_disabled" and url == "https://login.tailscale.com/f/serve?node=x"
    assert sync.serve_problem("boom")[0] == "error"


def test_sunshine_only_moves_between_tailscale_addresses():
    conf = "# c\nbind_address = 100.64.1.2\nupnp = disabled\n"
    assert sync.sunshine_rebind(conf, "100.66.0.5") == "# c\nbind_address = 100.66.0.5\nupnp = disabled\n"
    assert sync.sunshine_rebind(conf, "100.64.1.2") is None  # already right
    assert sync.sunshine_rebind(conf, None) is None  # never left without a value
    assert sync.sunshine_rebind("upnp = disabled\n", "100.66.0.5") is None  # no bind_address: a deliberate choice
    assert sync.sunshine_rebind("bind_address = 192.168.1.5\n", "100.66.0.5") is None  # not a Tailscale address


def test_public_url_follows_tailscale_but_a_custom_one_wins(tmp_path, monkeypatch):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "tailnet.json").write_text(json.dumps({"url": "https://b.new.ts.net"}))
    monkeypatch.setattr(config, "_tailnet", (0.0, {}))

    class S:
        data_root = tmp_path
        public_base_url = "https://a.old.ts.net"

    monkeypatch.setattr(config, "get_settings", lambda: S)
    assert config.public_base_url() == "https://b.new.ts.net"
    S.public_base_url = "https://hub.example.org"
    assert config.public_base_url() == "https://hub.example.org"
    S.public_base_url = "https://a.old.ts.net"
    (tmp_path / "run" / "tailnet.json").unlink()
    assert config.public_base_url() == "https://a.old.ts.net"  # not installed: the configured value
