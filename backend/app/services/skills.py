"""Skills: SKILL.md instruction packages (Anthropic "Agent Skills" layout) the chat model loads on demand.

A skill is text only — its scripts are stored as reference and never executed. Sources: GitHub repositories
(catalog from the `skill_sources` setting), any GitHub URL, or a direct https link to a SKILL.md file.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from ..models import Role, Skill, User, Visibility
from ..redis_client import get_redis
from ..settings_store import get_setting
from . import web

TEXT_EXT = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".sh", ".csv", ".html", ".xml", ".toml",
            ".css", ".sql", ".r", ".tex", ".xsd"}
MAX_FILES = 40
MAX_FILE_BYTES = 120_000
MAX_TOTAL_BYTES = 600_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CATALOG_TTL = 12 * 3600
BUILTIN = "builtin"  # Skill.installed_by of the skills shipped with the hub
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"


class SkillError(Exception):
    pass


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")[:64]
    return s or "skill"


def parse_skill_md(text: str) -> dict:
    """Frontmatter (name, description, license) + body of a SKILL.md."""
    meta: dict = {}
    body = text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        try:
            import yaml

            data = yaml.safe_load(m.group(1)) or {}
            if isinstance(data, dict):
                meta = data
        except Exception:  # noqa: BLE001 — tolerate sloppy frontmatter
            for line in m.group(1).splitlines():
                k, _, v = line.partition(":")
                if v:
                    meta[k.strip()] = v.strip().strip('"\'')
    name = slug(str(meta.get("name") or ""))
    if not meta.get("name"):
        h = re.search(r"^#\s+(.+)$", body, re.M)
        name = slug(h.group(1)) if h else "skill"
    desc = str(meta.get("description") or "").strip()
    if not desc:
        para = next((p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")), "")
        desc = para[:400]
    return {"name": name, "description": desc[:1500], "license": str(meta.get("license") or "")[:255] or None,
            "content": body.strip()}


# ------------------------------------------------------------------ GitHub


def _gh_json(url: str):
    r = web.fetch(url, max_bytes=2_000_000, accept="application/vnd.github+json")
    if r["status"] == 403:
        raise SkillError("GitHub ha limitado las consultas (60/hora sin token). Prueba en unos minutos.")
    if r["status"] == 404:
        raise SkillError("No existe esa ruta en GitHub")
    if r["status"] >= 400:
        raise SkillError(f"GitHub respondió {r['status']}")
    return json.loads(r["body"])


def _raw(url: str, limit: int = MAX_FILE_BYTES) -> str:
    r = web.fetch(url, max_bytes=limit)
    if r["status"] >= 400:
        raise SkillError(f"{url} respondió {r['status']}")
    return r["body"].decode(errors="replace")


def parse_source(src: str) -> dict:
    """owner/repo[/path] | https://github.com/o/r[/tree|blob/ref/path] | raw URL | any https .md URL."""
    src = src.strip()
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)(?:/(.+))?", src)
    if m and not src.startswith("http"):
        return {"kind": "github", "owner": m.group(1), "repo": m.group(2), "ref": None, "path": (m.group(3) or "").strip("/")}
    u = urlsplit(src)
    if u.scheme != "https":
        raise SkillError("Usa un enlace https (GitHub o un SKILL.md público)")
    parts = [p for p in u.path.split("/") if p]
    if u.hostname == "github.com" and len(parts) >= 2:
        ref, path = None, ""
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            ref, path = parts[3], "/".join(parts[4:])
        if path.endswith("SKILL.md"):
            path = path.rsplit("/", 1)[0] if "/" in path else ""
        return {"kind": "github", "owner": parts[0], "repo": parts[1].removesuffix(".git"), "ref": ref, "path": path}
    if u.hostname == "raw.githubusercontent.com" and len(parts) >= 4:
        path = "/".join(parts[3:])
        if path.endswith("SKILL.md"):
            path = path.rsplit("/", 1)[0] if "/" in path else ""
        return {"kind": "github", "owner": parts[0], "repo": parts[1], "ref": parts[2], "path": path}
    return {"kind": "url", "url": src}


def _gh_listing(owner: str, repo: str, path: str, ref: str | None) -> list[dict]:
    q = f"?ref={ref}" if ref else ""
    data = _gh_json(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}{q}")
    return data if isinstance(data, list) else [data]


def fetch_package(src: str) -> dict:
    """Download a skill package. Returns {name, description, license, content, files, source_url}."""
    s = parse_source(src)
    if s["kind"] == "url":
        text = _raw(s["url"])
        info = parse_skill_md(text)
        return {**info, "files": {}, "source_url": s["url"]}
    owner, repo, ref, path = s["owner"], s["repo"], s["ref"], s["path"]
    listing = _gh_listing(owner, repo, path, ref)
    names = {x["name"] for x in listing}
    if "SKILL.md" not in names:
        subs = [x["name"] for x in listing if x["type"] == "dir"]
        if "skills" in subs:
            raise SkillError(f"Este repositorio contiene varias skills: elige una de {owner}/{repo}/skills/<nombre> "
                             "(búscalas con el catálogo)")
        raise SkillError("No hay ningún SKILL.md en esa ruta")
    files: dict[str, str] = {}
    total = 0
    skill_md = ""
    queue = [(x, "") for x in listing]
    depth_dirs = 0
    while queue and len(files) < MAX_FILES:
        item, prefix = queue.pop(0)
        rel = prefix + item["name"]
        if item["type"] == "dir":
            if depth_dirs < 8 and rel.count("/") < 2:
                depth_dirs += 1
                queue += [(x, rel + "/") for x in _gh_listing(owner, repo, item["path"], ref)]
            continue
        ext = "." + item["name"].rsplit(".", 1)[-1].lower() if "." in item["name"] else ""
        if item["type"] != "file" or (ext not in TEXT_EXT and item["name"] != "LICENSE") or (item.get("size") or 0) > MAX_FILE_BYTES:
            continue
        if total + (item.get("size") or 0) > MAX_TOTAL_BYTES:
            continue
        text = _raw(item["download_url"])
        total += len(text.encode())
        if rel == "SKILL.md":
            skill_md = text
        else:
            files[rel] = text
    if not skill_md:
        raise SkillError("No se pudo descargar SKILL.md")
    info = parse_skill_md(skill_md)
    if info["name"] == "skill" and path:
        info["name"] = slug(path.rsplit("/", 1)[-1])
    url = f"https://github.com/{owner}/{repo}/tree/{ref or 'HEAD'}/{path}".rstrip("/")
    return {**info, "files": files, "source_url": url}


def catalog(db, refresh: bool = False) -> list[dict]:
    """Skills offered by the configured GitHub sources (cached 12 h): [{name, description, source, repo}]."""
    out: list[dict] = []
    r = get_redis()
    for src in get_setting(db, "skill_sources") or []:
        key = f"sah:skills:catalog:{src}"
        cached = None if refresh else r.get(key)
        if cached:
            out += json.loads(cached)
            continue
        s = parse_source(src)
        if s["kind"] != "github":
            continue
        items: list[dict] = []
        try:
            base = s["path"]
            listing = _gh_listing(s["owner"], s["repo"], base, s["ref"])
            if not base and any(x["name"] == "skills" and x["type"] == "dir" for x in listing):
                base = "skills"
                listing = _gh_listing(s["owner"], s["repo"], base, s["ref"])
            for d in [x for x in listing if x["type"] == "dir"][:80]:
                ref = s["ref"] or "HEAD"
                try:
                    md = _raw(f"https://raw.githubusercontent.com/{s['owner']}/{s['repo']}/{ref}/{d['path']}/SKILL.md", 200_000)
                except (SkillError, web.WebError):
                    continue
                info = parse_skill_md(md)
                items.append({"name": info["name"], "description": info["description"][:600], "license": info["license"],
                              "source": f"{s['owner']}/{s['repo']}/{d['path']}", "repo": f"{s['owner']}/{s['repo']}"})
        except (SkillError, web.WebError) as e:
            items = [{"error": str(e), "repo": src}]
            r.setex(key, 600, json.dumps(items))
            out += items
            continue
        r.setex(key, CATALOG_TTL, json.dumps(items))
        out += items
    return out


# ------------------------------------------------------------------ installed skills


def visible(db, user: User, enabled_only: bool = True):
    q = select(Skill).where(or_(Skill.owner_id == user.id, Skill.visibility == Visibility.public))
    if enabled_only:
        q = q.where(Skill.enabled.is_(True))
    rows = list(db.scalars(q.order_by(Skill.name)))
    seen: dict[str, Skill] = {}
    for s in rows:  # the user's own copy wins over a public one with the same name
        if s.name not in seen or s.owner_id == user.id:
            seen[s.name] = s
    return list(seen.values())


def find(db, user: User, name: str) -> Skill | None:
    name = slug(name)
    return next((s for s in visible(db, user) if s.name == name), None)


def install(db, user: User, src: str, installed_by: str = "user", overwrite: bool = True) -> Skill:
    pkg = fetch_package(src)
    if not NAME_RE.match(pkg["name"]):
        raise SkillError(f"Nombre de skill no válido: {pkg['name']}")
    if not pkg["content"].strip():
        raise SkillError("El SKILL.md está vacío")
    sk = db.scalar(select(Skill).where(Skill.owner_id == user.id, Skill.name == pkg["name"]))
    if sk and not overwrite:
        return sk
    if sk is None:
        sk = Skill(owner_id=user.id, name=pkg["name"])
        db.add(sk)
    sk.description, sk.content, sk.files = pkg["description"], pkg["content"][:200_000], pkg["files"]
    sk.source_url, sk.license, sk.installed_by, sk.enabled = pkg["source_url"], pkg["license"], installed_by, True
    db.flush()
    return sk


def mark_used(db, sk: Skill) -> None:
    sk.use_count = (sk.use_count or 0) + 1
    sk.last_used_at = datetime.now(timezone.utc)


def public(sk: Skill, user_id: uuid.UUID | None = None) -> dict:
    return {"id": str(sk.id), "name": sk.name, "description": sk.description, "source_url": sk.source_url,
            "license": sk.license, "installed_by": sk.installed_by, "visibility": sk.visibility.value,
            "enabled": sk.enabled, "use_count": sk.use_count, "last_used_at": sk.last_used_at,
            "files": sorted((sk.files or {}).keys()), "size_chars": len(sk.content or "") + sum(len(v) for v in (sk.files or {}).values()),
            "mine": user_id is None or sk.owner_id == user_id, "created_at": sk.created_at, "updated_at": sk.updated_at}


# ------------------------------------------------------------------ built-in skills shipped with the hub


def builtin_packages(root: Path = BUILTIN_DIR) -> list[dict]:
    """Skills in app/data/skills/<dir>/SKILL.md (+ text reference files): [{name, description, license, content, files}]."""
    out: list[dict] = []
    if not root.is_dir():
        return out
    for d in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith((".", "_"))):
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        files: dict[str, str] = {}
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d).as_posix()
            if f.is_file() and rel != "SKILL.md" and not any(part.startswith((".", "__")) for part in rel.split("/")):
                files[rel] = f.read_text(encoding="utf-8")
        out.append({**parse_skill_md(md.read_text(encoding="utf-8")), "files": files, "dir": d.name})
    return out


def sync_builtin(db) -> dict:
    """Install/refresh the hub's built-in skills as public skills owned by the first admin (idempotent).

    Content follows the files shipped with the hub; the admin's choices (enabled, visibility) are kept. A skill the
    admin wrote with the same name is never overwritten (that name is skipped); users' own copies win in visible().
    Built-in skills no longer shipped are removed. Returns counters for logging.
    """
    admin = db.scalar(select(User).where(User.role == Role.admin, User.is_active.is_(True)).order_by(User.created_at))
    if admin is None:
        return {"skipped": "no admin"}
    pkgs = [p for p in builtin_packages() if NAME_RE.match(p["name"]) and p["content"].strip()]
    rows = {s.name: s for s in db.scalars(select(Skill).where(Skill.installed_by == BUILTIN))}
    stats = {"created": 0, "updated": 0, "removed": 0, "skipped": 0}
    for pkg in pkgs:
        sk = rows.pop(pkg["name"], None)
        if sk is None:
            if db.scalar(select(Skill.id).where(Skill.owner_id == admin.id, Skill.name == pkg["name"])):
                stats["skipped"] += 1  # the admin has their own skill with this name
                continue
            sk = Skill(owner_id=admin.id, name=pkg["name"], installed_by=BUILTIN, visibility=Visibility.public,
                       enabled=True, files={})
            db.add(sk)
            stats["created"] += 1
        elif (sk.description, sk.content, sk.files or {}, sk.license) != (
                pkg["description"], pkg["content"], pkg["files"], pkg["license"]):
            stats["updated"] += 1
        else:
            continue
        sk.description, sk.content, sk.files, sk.license = pkg["description"], pkg["content"], pkg["files"], pkg["license"]
        sk.source_url = None
    for sk in rows.values():  # no longer shipped with the hub
        db.delete(sk)
        stats["removed"] += 1
    db.commit()
    return stats


def restore_builtin(db, sk: Skill) -> Skill:
    """Reset a built-in skill to the content shipped with the hub."""
    pkg = next((p for p in builtin_packages() if p["name"] == sk.name), None)
    if pkg is None:
        raise SkillError("Esta skill ya no viene incluida en el hub")
    sk.description, sk.content, sk.files, sk.license = pkg["description"], pkg["content"], pkg["files"], pkg["license"]
    db.flush()
    return sk
