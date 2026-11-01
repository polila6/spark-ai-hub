"""Hugging Face Hub integration via the official `huggingface_hub` client."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

from ..config import get_settings
from ..redis_client import get_redis

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
HF_URL_RE = re.compile(r"^https?://(?:www\.)?huggingface\.co/([^/?#]+/[^/?#]+)")


class HfError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def normalise_repo_id(value: str) -> str:
    value = value.strip()
    m = HF_URL_RE.match(value)
    if m:
        value = m.group(1)
    value = value.removeprefix("hf.co/")
    if not REPO_RE.match(value):
        raise HfError("invalid_repo_id", "Expected 'owner/name' or a huggingface.co URL")
    return value


def api(token: str | None) -> HfApi:
    return HfApi(token=token or False)


def _wrap(fn):
    try:
        return fn()
    except GatedRepoError as e:
        raise HfError("gated", "Gated repository: accept the licence on huggingface.co and configure a token", 403) from e
    except RepositoryNotFoundError as e:
        raise HfError("not_found", "Repository not found (or private)", 404) from e
    except RevisionNotFoundError as e:
        raise HfError("revision_not_found", "Revision not found", 404) from e
    except HfHubHTTPError as e:
        status = getattr(e.response, "status_code", 502) if getattr(e, "response", None) is not None else 502
        if status == 429:
            raise HfError("rate_limited", "Hugging Face rate limit reached; retry later", 429) from e
        raise HfError("hub_error", f"Hugging Face error ({status})", 502) from e


def _cache_get(key: str):
    try:
        v = get_redis().get(key)
        return json.loads(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _cache_set(key: str, value, ttl: int) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        pass


def search(token: str | None, *, query: str | None, task: str | None, library: str | None, author: str | None,
           tags: list[str], sort: str, limit: int, gated: bool | None = None) -> list[dict]:
    key = f"hf:search:{json.dumps([query, task, library, author, tags, sort, limit, gated])}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    def run():
        kwargs: dict[str, Any] = dict(search=query or None, sort=sort, limit=limit, full=False,
                                      expand=["downloads", "likes", "pipeline_tag", "library_name", "tags", "gated",
                                              "lastModified", "createdAt", "safetensors", "cardData"])
        filt = [t for t in tags if t]
        if task:
            kwargs["pipeline_tag"] = task
        if library:
            kwargs["library"] = library
        if author:
            kwargs["author"] = author
        if filt:
            kwargs["filter"] = filt
        if gated is not None:
            kwargs["gated"] = gated
        return list(api(token).list_models(**kwargs))

    models = _wrap(run)
    out = []
    for m in models:
        st = getattr(m, "safetensors", None)
        card = getattr(m, "card_data", None) or {}
        lic = None
        if hasattr(card, "get"):
            lic = card.get("license")
        if not lic:
            lic = next((t.split(":", 1)[1] for t in (m.tags or []) if t.startswith("license:")), None)
        out.append({
            "id": m.id,
            "author": m.id.split("/")[0],
            "downloads": getattr(m, "downloads", None),
            "likes": getattr(m, "likes", None),
            "pipeline_tag": getattr(m, "pipeline_tag", None),
            "library": getattr(m, "library_name", None),
            "tags": (m.tags or [])[:40],
            "gated": getattr(m, "gated", False) or False,
            "license": lic,
            "last_modified": getattr(m, "last_modified", None),
            "parameters": (st.total if st else None),
            "quantization": next((t for t in (m.tags or []) if t in ("fp8", "awq", "gptq", "gguf", "nvfp4", "mxfp4", "bitsandbytes", "4-bit", "8-bit")), None),
        })
    _cache_set(key, out, 300)
    return out


def model_info(token: str | None, repo_id: str, revision: str | None = None) -> dict:
    key = f"hf:info:{repo_id}:{revision}:{bool(token)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    def run():
        return api(token).model_info(repo_id, revision=revision, files_metadata=True, securityStatus=True)

    info = _wrap(run)
    files = []
    for s in info.siblings or []:
        lfs = getattr(s, "lfs", None)
        files.append({"path": s.rfilename, "size": s.size or (lfs.size if lfs else None),
                      "sha256": lfs.sha256 if lfs else None, "blob_id": getattr(s, "blob_id", None)})
    card = info.card_data.to_dict() if getattr(info, "card_data", None) else {}
    st = getattr(info, "safetensors", None)
    config = None
    if any(f["path"] == "config.json" for f in files):
        config = fetch_json(token, repo_id, "config.json", info.sha)
    sec = getattr(info, "security_repo_status", None) or getattr(info, "securityStatus", None)
    data = {
        "id": info.id,
        "sha": info.sha,
        "revision": revision or "main",
        "private": info.private,
        "gated": info.gated or False,
        "disabled": getattr(info, "disabled", False),
        "downloads": info.downloads,
        "likes": info.likes,
        "pipeline_tag": info.pipeline_tag,
        "library": info.library_name,
        "tags": info.tags or [],
        "license": card.get("license") or next((t.split(":", 1)[1] for t in (info.tags or []) if t.startswith("license:")), None),
        "license_name": card.get("license_name"),
        "base_model": card.get("base_model"),
        "languages": card.get("language"),
        "last_modified": info.last_modified,
        "created_at": getattr(info, "created_at", None),
        "parameters": st.total if st else None,
        "parameters_by_dtype": dict(st.parameters) if st else None,
        "files": files,
        "total_size": sum(f["size"] or 0 for f in files),
        "config": config,
        "security": sec if isinstance(sec, (dict, list)) else None,
    }
    _cache_set(key, data, 600)
    return data


def fetch_json(token: str | None, repo_id: str, filename: str, revision: str | None) -> dict | None:
    try:
        path = hf_hub_download(repo_id, filename, revision=revision, token=token or False,
                               cache_dir=str(get_settings().hf_cache_dir))
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def model_card(token: str | None, repo_id: str, revision: str | None) -> str | None:
    key = f"hf:card:{repo_id}:{revision}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        path = hf_hub_download(repo_id, "README.md", revision=revision, token=token or False,
                               cache_dir=str(get_settings().hf_cache_dir))
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:  # noqa: BLE001
        text = None
    if text and len(text) > 200_000:
        text = text[:200_000]
    _cache_set(key, text, 3600)
    return text


def refs(token: str | None, repo_id: str) -> dict:
    def run():
        r = api(token).list_repo_refs(repo_id)
        return {"branches": [{"name": b.name, "commit": b.target_commit} for b in r.branches],
                "tags": [{"name": t.name, "commit": t.target_commit} for t in r.tags]}

    return _wrap(run)


def commits(token: str | None, repo_id: str, limit: int = 20) -> list[dict]:
    def run():
        cs = api(token).list_repo_commits(repo_id)
        return [{"commit": c.commit_id, "title": c.title, "date": c.created_at} for c in cs[:limit]]

    return _wrap(run)


def whoami(token: str) -> dict | None:
    try:
        return HfApi(token=token).whoami()
    except Exception:  # noqa: BLE001
        return None


def snapshot_path(repo_id: str, sha: str) -> str:
    """Path of a downloaded snapshot inside the shared HF cache (as mounted in containers)."""
    return f"models--{repo_id.replace('/', '--')}/snapshots/{sha}"


def now() -> float:
    return time.time()
