"""Optional Tailscale API integration: per-user device-share invites for the Spark (access option A).

Without an API key the administrator pastes a share link from the Tailscale admin console (Machines → spark → Share)
into the settings and it is included in invitation e-mails. With an API key (Settings → Access), each invitation
creates a device invite for the person's e-mail through the Tailscale API.
"""
from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from ..settings_store import get_setting

API = "https://api.tailscale.com/api/v2"


class TailscaleError(Exception):
    pass


def share_link_for(db: Session, email: str | None) -> tuple[str | None, str]:
    """Return (url, source). Never raises: invitation flows must not fail because Tailscale is unreachable."""
    key = get_setting(db, "tailscale_api_key")
    device = get_setting(db, "tailscale_device_id")
    if key and device and email:
        try:
            return create_device_invite(key, device, email), "api"
        except TailscaleError:
            pass
    url = get_setting(db, "tailscale_share_url")
    return (url, "static") if url else (None, "none")


def create_device_invite(key: str, device_id: str, email: str) -> str:
    try:
        r = httpx.post(f"{API}/device/{device_id}/device-invites", headers={"Authorization": f"Bearer {key}"},
                       json=[{"email": email, "allowExitNode": False, "multiUse": False}], timeout=20)
    except httpx.HTTPError as e:
        raise TailscaleError(f"Tailscale API unreachable: {e}") from e
    if r.status_code >= 300:
        raise TailscaleError(f"Tailscale API {r.status_code}: {r.text[:300]}")
    data = r.json()
    inv = data[0] if isinstance(data, list) and data else data
    url = (inv or {}).get("inviteUrl")
    if not url:
        raise TailscaleError("Tailscale API returned no inviteUrl")
    return url


def check(key: str, device_id: str) -> dict:
    """Validate the API key and device id (used by the settings page)."""
    try:
        r = httpx.get(f"{API}/device/{device_id}", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as e:
        raise TailscaleError(f"Tailscale API unreachable: {e}") from e
    if r.status_code != 200:
        raise TailscaleError(f"Tailscale API {r.status_code}: {r.text[:300]}")
    d = r.json()
    return {"name": d.get("name"), "hostname": d.get("hostname"), "addresses": d.get("addresses")}
