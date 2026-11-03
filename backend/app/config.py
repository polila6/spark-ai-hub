"""Application configuration loaded from environment variables and secret files."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_secret(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return p.read_text().strip() or None
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAH_", extra="ignore")

    env: str = "production"
    version: str = __import__("app").__version__  # release; SAH_VERSION is only the Docker image tag
    log_level: str = "INFO"

    # Persistent data root *inside* containers (bind-mounted from host SAH_DATA_ROOT).
    data_root: Path = Path("/data")
    host_data_root: str = "/srv/spark-ai-hub"
    # Read-only mount of a pre-existing host HF cache for "import existing models".
    import_hf_cache: Path = Path("/import/hf-cache")

    database_url: str = "postgresql+psycopg://sah:sah@postgres:5432/sah"
    database_password_file: str | None = "/run/secrets/postgres_password"
    redis_url: str = "redis://redis:6379/0"

    session_secret_file: str | None = "/run/secrets/session_secret"
    encryption_key_file: str | None = "/run/secrets/encryption_key"
    orchestrator_token_file: str | None = "/run/secrets/orchestrator_token"

    orchestrator_url: str = "http://engine-manager:9000"
    ollama_url: str = "http://ollama:11434"
    vllm_url: str = "http://sah-vllm:8000"
    comfyui_url: str = "http://sah-comfyui:8188"
    mlworker_url: str = "http://sah-mlworker:8100"

    public_base_url: str = "http://localhost:8080"
    org_name: str = ""  # shown in terms, e-mails and the assistant prompt (e.g. "Robotics Lab · Example University")
    session_ttl_hours: int = 12
    cookie_secure: bool = True

    # Bootstrap administrator (only used when the users table is empty).
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password_file: str | None = "/run/secrets/admin_password"

    # Upload limits
    max_upload_mb: int = 2048

    # Resource defaults (overridable through system settings in the DB)
    system_reserve_gb: float = Field(default=14.0, description="Memory kept for OS + control plane")

    def secret(self, name: str) -> str | None:
        return _read_secret(getattr(self, f"{name}_file"))

    @property
    def effective_database_url(self) -> str:
        pw = self.secret("database_password")
        if pw and "sah:sah@" in self.database_url:
            return self.database_url.replace("sah:sah@", f"sah:{pw}@")
        return self.database_url

    # Data subdirectories
    @property
    def models_dir(self) -> Path:
        return self.data_root / "models"

    @property
    def hf_cache_dir(self) -> Path:
        return self.data_root / "models" / "hf"

    @property
    def datasets_dir(self) -> Path:
        return self.data_root / "datasets"

    @property
    def outputs_dir(self) -> Path:
        return self.data_root / "outputs"

    @property
    def experiments_dir(self) -> Path:
        return self.data_root / "experiments"

    @property
    def benchmarks_dir(self) -> Path:
        return self.data_root / "benchmarks"

    @property
    def jobs_dir(self) -> Path:
        return self.data_root / "jobs"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def custom_models_dir(self) -> Path:
        return self.data_root / "models" / "custom"


@lru_cache
def get_settings() -> Settings:
    return Settings()


_tailnet: tuple[float, dict] = (0.0, {})


def tailnet_state() -> dict:
    """What scripts/tailnet-sync.py last saw on the host (address, account, Serve); {} if it is not installed."""
    global _tailnet
    path = get_settings().data_root / "run" / "tailnet.json"
    try:
        mtime = path.stat().st_mtime
        if mtime != _tailnet[0]:
            _tailnet = (mtime, json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}
    return _tailnet[1]


def public_base_url() -> str:
    """The address people use to open the hub. A Tailscale address follows the machine live (no restart when the
    account or tailnet changes); a custom SAH_PUBLIC_BASE_URL always wins."""
    url = get_settings().public_base_url
    if url.endswith(".ts.net") or "localhost" in url or "127.0.0.1" in url:
        return tailnet_state().get("url") or url
    return url
