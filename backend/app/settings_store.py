"""Administrator-editable system settings persisted in PostgreSQL, with typed defaults."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import SystemSetting
from .security import decrypt_str, encrypt_str

DEFAULTS: dict[str, Any] = {
    "default_language": "es",
    "default_model": None,
    "scheduler_mode": "hybrid",  # persistent | on_demand | hybrid
    "model_idle_timeout_min": 15,
    "on_demand_idle_timeout_min": 2,
    "preferred_resident_model": None,
    "pending_main_model": None,  # {model_id, alias, ...}: becomes the main model when its install finishes
    "system_reserve_gb": 14.0,
    "max_concurrent_gpu_jobs": 1,
    "max_concurrent_video_jobs": 1,
    "interactive_preempts_batch": True,
    "storage_min_free_gb": 200,
    "storage_warn_percent": 85,
    "log_request_content": False,
    "log_retention_days": 30,
    "metrics_retention_days": 14,
    "job_retention_days": 90,
    "queue_wait_alert_min": 60,
    "temperature_alert_c": 90,
    "backup_schedule": "daily",  # off | daily | weekly
    "backup_hour": 3,
    "backup_include_user_data": False,
    "backup_external_path": None,  # optional off-host mount inside /backups-external
    "backup_keep": 7,
    "features": {"vllm": True, "comfyui": True, "video": True, "audio": True, "scientific": True},
    "api_default_rpm": 60,
    # Accounts and access
    "signup_mode": "approval",  # approval (anyone can request an account) | open | closed (invitation only)
    "signup_allowed_domains": [],  # empty = any e-mail domain
    "terms_version": 1,
    "terms_text": None,  # markdown shown at first login; None = built-in default text
    "tailscale_share_url": None,  # optional multi-use device-share link pasted from the Tailscale console
    "tailscale_device_id": None,  # node id used with the optional Tailscale API key to create per-user invites
    # Outgoing e-mail (typically your organisation's SMTP server, port 587 STARTTLS with your mail user name)
    "mail_enabled": False,
    "smtp_host": None,
    "smtp_port": 587,
    "smtp_security": "starttls",  # starttls | ssl | none
    "smtp_username": None,
    "smtp_from": None,
    "smtp_from_name": "Spark AI Hub",
    # Personal cloud
    "embedding_model": None,  # alias of the embeddings model used by the knowledge base (None = first available)
    "chat_image_model": "auto",  # auto | qwen-image | sd15
    "chat_default_model": None,
    "harness_default_model": None,  # model used for "claude-*" names on /v1/messages (Claude Code)
    "harness_small_model": None,
    # Chat agent: web search, skills, sub-agents
    "web_search_enabled": True,  # web_search/fetch_url tools (queries leave the Spark through the private SearXNG)
    "searxng_url": "http://searxng:8080",
    "skills_enabled": True,
    "skills_auto_install": True,  # the model may install skills from the internet without asking first
    "skill_sources": ["anthropics/skills"],  # GitHub repos (owner/repo[/path]) offered as the skill catalog
    "agents_enabled": True,
    "max_parallel_agents": 3,
    "code_exec_enabled": True,  # run_code tool: sandbox container without network (engines/sandbox)
    "code_exec_timeout_s": 120,
    "code_exec_memory_mb": 4096,
    "code_exec_per_user": 2,  # sandbox programs one user may run at once (chat + workspaces)
    "workspace_timeout_s": 600,  # time limit of a command in a workspace (max 900)
    "workspace_max_gb": 10,  # size limit of each workspace
    "workspace_packages_enabled": True,  # pip/npm installs in workspaces through the PyPI/npm-only package proxy
    "voice_enabled": True,  # chat dictation (speech-to-text) and read-aloud (text-to-speech) with local models
    "voice_asr_model": None,  # alias; None = first installed speech-recognition model
    "voice_tts_model": None,  # alias; None = the voice matching the language
    "connector_private_hosts": [],
    # Sign in with Google / Microsoft / OpenID Connect (secrets in SECRET_KEYS)
    "oidc_google_client_id": None,
    "oidc_microsoft_client_id": None,
    "oidc_microsoft_tenant": "organizations",  # a tenant id/domain limits sign-in to one organisation
    "oidc_generic_name": None,
    "oidc_generic_issuer": None,
    "oidc_generic_client_id": None,  # private hosts/networks knowledge connectors may reach (e.g. "192.168.1.0/24", "nas.lab")
}

SECRET_KEYS = {"hf_token", "smtp_password", "tailscale_api_key", "oidc_google_client_secret", "oidc_microsoft_client_secret",
               "oidc_generic_client_secret"}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(SystemSetting, key)
    if row is None:
        return DEFAULTS.get(key)
    if row.is_secret:
        return decrypt_str(row.value) if isinstance(row.value, str) else None
    return row.value


def all_settings(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(SystemSetting).all():
        if row.is_secret:
            out[row.key] = "__set__" if row.value else None
        else:
            out[row.key] = row.value
    for k in SECRET_KEYS:
        out.setdefault(k, None)
    return out


def set_setting(db: Session, key: str, value: Any, user_id=None) -> None:
    if key not in DEFAULTS and key not in SECRET_KEYS:
        raise KeyError(key)
    secret = key in SECRET_KEYS
    stored = encrypt_str(value) if (secret and value) else (None if secret else value)
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=stored, is_secret=secret, updated_by=user_id))
    else:
        row.value = stored
        row.is_secret = secret
        row.updated_by = user_id
    db.commit()
