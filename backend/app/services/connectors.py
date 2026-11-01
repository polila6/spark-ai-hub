"""Knowledge connectors: keep a knowledge collection in sync with an external source.

* webdav — Nextcloud / ownCloud / any WebDAV folder (user + app password)
* github — a repository (optional token for private repos), optionally one folder
* zotero — the PDFs/HTML snapshots stored in a Zotero library (user or group, API key)
* smb    — a Windows / NAS network share (\\\\host\\share\\folder)

Only documents and data files are imported (PDF, Office, text, code, CSV/Excel…), up to 100 MB each. Remote hosts
must resolve to public addresses unless an administrator lists the private host/network in Settings
(`connector_private_hosts`); loopback, link-local and the hub's own containers are always refused.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import socket
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ConnectorItem, KbConnector, KbDocument, User, UserFile
from ..security import decrypt_str, encrypt_str
from ..settings_store import get_setting
from . import files as files_svc
from . import kb, storage
from .uploads import safe_name

KINDS = ("webdav", "github", "zotero", "smb")
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_FILES = 2000
IMPORT_EXT = files_svc.DOC_EXT | files_svc.CODE_EXT | {".csv", ".tsv", ".xlsx", ".xlsm", ".json", ".jsonl"}
UA = "SparkAIHub-connector/0.5"


class ConnectorError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ network guard


def _allowed_private(db: Session, host: str, ip: ipaddress._BaseAddress) -> bool:
    for entry in get_setting(db, "connector_private_hosts") or []:
        entry = str(entry).strip().lower()
        if not entry:
            continue
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            if host.lower() == entry or host.lower().endswith("." + entry.lstrip(".")):
                return True
    return False


def resolve(db: Session, host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ConnectorError(f"No se puede resolver {host}") from e
    addrs = sorted({i[4][0].split("%")[0] for i in infos}, key=lambda a: ":" in a)
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ConnectorError(f"{host}: dirección no permitida")
        if not ip.is_global and not _allowed_private(db, host, ip):
            raise ConnectorError(f"{host} es una dirección privada. Un administrador puede permitirla en Ajustes → "
                                 "Sistema → «Redes privadas para conectores».")
    return addrs[0]


def _request(db: Session, method: str, url: str, *, headers: dict | None = None, auth: tuple | None = None,
             content: bytes | None = None, max_bytes: int = MAX_FILE_BYTES, redirects: int = 5) -> httpx.Response:
    current = url
    for _ in range(redirects + 1):
        u = urlsplit(current)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConnectorError("Solo direcciones http(s)")
        port = u.port or (443 if u.scheme == "https" else 80)
        ip = resolve(db, u.hostname, port)
        netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")
        pinned = urlunsplit((u.scheme, netloc, u.path or "/", u.query, ""))
        h = {"User-Agent": UA, **(headers or {}), "Host": u.hostname if not u.port else f"{u.hostname}:{u.port}"}
        if auth and urlsplit(url).hostname == u.hostname:  # never send credentials to another host
            h["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        try:
            with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False) as c:
                with c.stream(method, pinned, headers=h, content=content, extensions={"sni_hostname": u.hostname}) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        if r.status_code == 303:
                            method, content = "GET", None
                        continue
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise ConnectorError("Archivo demasiado grande")
                    hdrs = {k: v for k, v in r.headers.items() if k.lower() not in ("content-encoding", "content-length",
                                                                                    "transfer-encoding")}
                    return httpx.Response(r.status_code, headers=hdrs, content=bytes(body), request=r.request)
        except httpx.HTTPError as e:
            raise ConnectorError(f"No se pudo conectar con {u.hostname}: {type(e).__name__}") from e
    raise ConnectorError("Demasiadas redirecciones")


def _ok(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code in (401, 403):
        raise ConnectorError(f"{what}: acceso denegado (revisa usuario, contraseña o token)")
    if r.status_code == 404:
        raise ConnectorError(f"{what}: no existe (revisa la dirección o la ruta)")
    if r.status_code >= 400:
        raise ConnectorError(f"{what}: error {r.status_code}")
    return r


# ------------------------------------------------------------------ sources: list() -> [{path, etag, size}], get(path)


class WebDav:
    def __init__(self, db, cfg: dict, secret: str | None):
        self.db, self.base = db, cfg["url"].rstrip("/") + "/"
        self.auth = (cfg.get("username") or "", secret or "") if cfg.get("username") else None
        self.root = urlsplit(self.base).path

    def list(self) -> list[dict]:
        out, todo = [], [self.base]
        body = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:getcontentlength/>'
                b'<d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>')
        seen = set()
        while todo and len(out) < MAX_FILES:
            url = todo.pop()
            if url in seen or len(seen) > 400:
                continue
            seen.add(url)
            r = _ok(_request(self.db, "PROPFIND", url, headers={"Depth": "1", "Content-Type": "application/xml"},
                             auth=self.auth, content=body, max_bytes=20 * 1024 * 1024), "WebDAV")
            ns = {"d": "DAV:"}
            for resp in ET.fromstring(r.content).findall("d:response", ns):
                href = unquote(resp.findtext("d:href", default="", namespaces=ns))
                path = urlsplit(href).path
                if not path.startswith(self.root) or path.rstrip("/") == self.root.rstrip("/"):
                    continue
                # a response may hold several propstat blocks (found props with 200, missing ones with 404)
                props = [ps.find("d:prop", ns) for ps in resp.findall("d:propstat", ns)
                         if " 200 " in (ps.findtext("d:status", default=" 200 ", namespaces=ns) + " ")]
                props = [x for x in props if x is not None]
                if not props:
                    continue

                def val(tag: str) -> str:
                    return next((x.findtext(tag, default="", namespaces=ns) for x in props if x.find(tag, ns) is not None), "")
                if any(x.find("d:resourcetype/d:collection", ns) is not None for x in props) or href.endswith("/"):
                    todo.append(urljoin(self.base, quote(path)))
                    continue
                rel = path[len(self.root):]
                out.append({"path": rel, "etag": (val("d:getetag") or val("d:getlastmodified")).strip('"'),
                            "size": int(val("d:getcontentlength") or 0)})
        return out

    def get(self, item: dict) -> bytes:
        return _ok(_request(self.db, "GET", urljoin(self.base, quote(item["path"])), auth=self.auth), "WebDAV").content


class GitHub:
    API = "https://api.github.com"

    def __init__(self, db, cfg: dict, secret: str | None):
        self.db = db
        self.repo = cfg["repo"].strip().strip("/")
        self.branch = (cfg.get("branch") or "").strip()
        self.prefix = (cfg.get("path") or "").strip("/")
        self.h = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {secret}"} if secret else {})}

    def list(self) -> list[dict]:
        if not self.branch:
            info = json.loads(_ok(_request(self.db, "GET", f"{self.API}/repos/{self.repo}", headers=self.h), "GitHub").content)
            self.branch = info.get("default_branch") or "main"
        tree = json.loads(_ok(_request(self.db, "GET", f"{self.API}/repos/{self.repo}/git/trees/{quote(self.branch)}?recursive=1",
                                       headers=self.h, max_bytes=50 * 1024 * 1024), "GitHub").content)
        out = []
        for t in tree.get("tree", []):
            if t.get("type") != "blob" or (self.prefix and not t["path"].startswith(self.prefix + "/")):
                continue
            out.append({"path": t["path"][len(self.prefix) + 1:] if self.prefix else t["path"], "etag": t["sha"],
                        "size": t.get("size", 0), "full": t["path"]})
        return out[:MAX_FILES]

    def get(self, item: dict) -> bytes:
        return _ok(_request(self.db, "GET", f"{self.API}/repos/{self.repo}/contents/{quote(item['full'])}?ref={quote(self.branch)}",
                            headers={**self.h, "Accept": "application/vnd.github.raw"}), "GitHub").content


class Zotero:
    API = "https://api.zotero.org"

    def __init__(self, db, cfg: dict, secret: str | None):
        self.db = db
        kind = "groups" if cfg.get("library_type") == "group" else "users"
        self.base = f"{self.API}/{kind}/{int(cfg['library_id'])}"
        self.h = {"Zotero-API-Version": "3", **({"Zotero-API-Key": secret} if secret else {})}

    def list(self) -> list[dict]:
        out, start = [], 0
        while len(out) < MAX_FILES:
            r = _ok(_request(self.db, "GET", f"{self.base}/items?itemType=attachment&format=json&limit=100&start={start}",
                             headers=self.h, max_bytes=30 * 1024 * 1024), "Zotero")
            items = json.loads(r.content)
            for it in items:
                d = it.get("data", {})
                if d.get("linkMode") not in ("imported_file", "imported_url") or not d.get("filename"):
                    continue
                out.append({"path": f"{d['key']}/{d['filename']}", "etag": str(d.get("md5") or d.get("version")), "size": 0,
                            "key": d["key"]})
            if len(items) < 100:
                break
            start += 100
        return out

    def get(self, item: dict) -> bytes:
        return _ok(_request(self.db, "GET", f"{self.base}/items/{item['key']}/file", headers=self.h), "Zotero").content


class Smb:
    """Each connector uses its own connection cache: smbclient's global pool would reuse another connector's session
    (other credentials) for the same server."""

    def __init__(self, db, cfg: dict, secret: str | None):
        self.db, self.host = db, cfg["host"].strip()
        self.share = cfg["share"].strip("\\/ ")
        self.folder = (cfg.get("path") or "").strip("\\/ ").replace("/", "\\")
        self.user = cfg.get("username") or ""
        if cfg.get("domain"):
            self.user = f"{cfg['domain']}\\{self.user}"
        self.password = secret or ""
        self.cache: dict = {}

    def _session(self):
        import smbclient

        ip = resolve(self.db, self.host, 445)
        guest = not self.user
        # guest sessions cannot sign, so they cannot pass SMB 3 secure negotiation either
        smbclient.ClientConfig(require_secure_negotiate=not guest)
        try:
            smbclient.register_session(ip, username=self.user or "guest", password=self.password or "", port=445,
                                       connection_timeout=15, auth_protocol="ntlm", require_signing=not guest,
                                       connection_cache=self.cache)
        except Exception as e:  # noqa: BLE001  (smbprotocol raises many specific errors)
            self.close()
            raise ConnectorError(_smb_message(self.host, e))
        return smbclient, ip

    def close(self) -> None:
        import smbclient

        try:
            smbclient.reset_connection_cache(connection_cache=self.cache)
        except Exception:  # noqa: BLE001
            pass

    def list(self) -> list[dict]:
        smbclient, ip = self._session()
        root = f"\\\\{ip}\\{self.share}" + (f"\\{self.folder}" if self.folder else "")
        out, todo = [], [root]
        try:
            while todo and len(out) < MAX_FILES:
                d = todo.pop()
                for e in smbclient.scandir(d, connection_cache=self.cache):
                    if e.is_dir():
                        if not e.name.startswith("."):
                            todo.append(f"{d}\\{e.name}")
                        continue
                    st = e.stat()
                    rel = f"{d}\\{e.name}"[len(root) + 1:].replace("\\", "/")
                    out.append({"path": rel, "etag": f"{int(st.st_mtime)}-{st.st_size}", "size": st.st_size,
                                "full": f"{d}\\{e.name}"})
        except Exception as e:  # noqa: BLE001
            self.close()
            raise ConnectorError(_smb_message(self.host, e))
        return out

    def get(self, item: dict) -> bytes:
        smbclient, _ = self._session()
        try:
            with smbclient.open_file(item["full"], mode="rb", connection_cache=self.cache) as fh:
                return fh.read(MAX_FILE_BYTES + 1)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(_smb_message(self.host, e))


def _smb_message(host: str, e: Exception) -> str:
    t = str(e)
    if "ACCESS_DENIED" in t or "LOGON_FAILURE" in t or "0xc000006d" in t.lower():
        return f"SMB {host}: acceso denegado (revisa usuario, contraseña, dominio y que tenga permiso sobre el recurso)"
    if "BAD_NETWORK_NAME" in t or "0xc00000cc" in t.lower():
        return f"SMB {host}: el recurso compartido no existe (revisa el nombre)"
    if "OBJECT_NAME_NOT_FOUND" in t or "OBJECT_PATH_NOT_FOUND" in t:
        return f"SMB {host}: la carpeta no existe"
    return f"SMB {host}: {type(e).__name__}: {t[:150]}"


SOURCES = {"webdav": WebDav, "github": GitHub, "zotero": Zotero, "smb": Smb}


# ------------------------------------------------------------------ CRUD helpers


def validate(kind: str, cfg: dict) -> dict:
    if kind not in KINDS:
        raise ConnectorError("Tipo de conector no válido")
    need = {"webdav": ["url"], "github": ["repo"], "zotero": ["library_id"], "smb": ["host", "share"]}[kind]
    for k in need:
        if not str(cfg.get(k) or "").strip():
            raise ConnectorError(f"Falta «{k}»")
    if kind == "webdav" and not str(cfg["url"]).startswith(("https://", "http://")):
        raise ConnectorError("La dirección WebDAV debe empezar por https://")
    if kind == "github" and len(str(cfg["repo"]).strip("/").split("/")) != 2:
        raise ConnectorError("Repositorio en formato propietario/nombre")
    if kind == "zotero" and not str(cfg["library_id"]).strip().isdigit():
        raise ConnectorError("El ID de la biblioteca de Zotero es un número")
    allowed = {"webdav": ["url", "username"], "github": ["repo", "branch", "path"],
               "zotero": ["library_type", "library_id"], "smb": ["host", "share", "path", "username", "domain"]}[kind]
    return {k: str(cfg[k]).strip() for k in allowed if cfg.get(k) not in (None, "")}


def set_secret(c: KbConnector, secret: str | None) -> None:
    if secret is not None:
        c.secret = encrypt_str(secret) if secret else None


def public(c: KbConnector) -> dict:
    return {"id": str(c.id), "kind": c.kind, "name": c.name, "config": c.config, "has_secret": bool(c.secret),
            "collection_id": str(c.collection_id) if c.collection_id else None,
            "project_id": str(c.project_id) if c.project_id else None, "schedule": c.schedule, "enabled": c.enabled,
            "status": c.status, "status_message": c.status_message, "stats": c.stats or {},
            "last_sync_at": c.last_sync_at, "created_at": c.created_at}


def source(db: Session, c: KbConnector):
    return SOURCES[c.kind](db, c.config or {}, decrypt_str(c.secret) if c.secret else None)


def test(db: Session, c: KbConnector) -> dict:
    src = source(db, c)
    try:
        items = src.list()
    finally:
        getattr(src, "close", lambda: None)()
    ok = [i for i in items if PurePosixPath(i["path"]).suffix.lower() in IMPORT_EXT]
    return {"files": len(items), "importable": len(ok), "examples": [i["path"] for i in ok[:8]]}


# ------------------------------------------------------------------ sync


def sync(db: Session, c: KbConnector, progress=None) -> dict:
    """Import new/changed files, refresh changed ones and drop files deleted at the source."""
    user = db.get(User, c.owner_id)
    if c.collection_id is None:
        c.collection_id = kb.default_collection(db, user).id
    c.status, c.status_message = "syncing", None
    db.commit()
    src = source(db, c)
    try:
        return _sync(db, c, user, src, progress)
    finally:
        getattr(src, "close", lambda: None)()


def _sync(db: Session, c: KbConnector, user: User, src, progress) -> dict:
    remote = [i for i in src.list() if PurePosixPath(i["path"]).suffix.lower() in IMPORT_EXT
              and (not i.get("size") or i["size"] <= MAX_FILE_BYTES)][:MAX_FILES]
    known = {it.remote_path: it for it in db.scalars(select(ConnectorItem).where(ConnectorItem.connector_id == c.id))}
    folder = f"Conectores/{safe_name(c.name)}"[:255]
    added = updated = removed = failed = 0
    errors: list[str] = []
    for n, item in enumerate(remote):
        prev = known.pop(item["path"], None)
        if prev and prev.etag == item["etag"] and prev.file_id:
            continue
        try:
            data = src.get(item)
            if len(data) > MAX_FILE_BYTES:
                raise ConnectorError("más de 100 MB")
            f = _store(db, user, c, folder, item["path"], data)
            if prev:
                _drop_file(db, prev.file_id)
                prev.etag, prev.file_id, prev.synced_at = item["etag"], f.id if f else None, datetime.now(timezone.utc)
                updated += 1
            else:
                db.add(ConnectorItem(connector_id=c.id, remote_path=item["path"], etag=item["etag"], file_id=f.id if f else None))
                added += 1
            db.commit()
            if f:
                _index(db, f, c)
        except (ConnectorError, storage.StorageError, files_svc.FileError) as e:
            failed += 1
            errors.append(f"{item['path']}: {getattr(e, 'message', e)}")
            db.rollback()
            if isinstance(e, storage.StorageError):
                break
        if progress:
            progress((n + 1) / max(1, len(remote)), f"{n + 1}/{len(remote)}")
    for gone in known.values():  # deleted at the source
        _drop_file(db, gone.file_id)
        db.delete(gone)
        removed += 1
    db.flush()
    c.status = "ok" if not failed else "error"
    c.status_message = "; ".join(errors[:5]) or None
    c.last_sync_at = datetime.now(timezone.utc)
    c.stats = {"remote": len(remote), "added": added, "updated": updated, "removed": removed, "failed": failed,
               "files": db.query(ConnectorItem).filter(ConnectorItem.connector_id == c.id).count()}
    db.commit()
    return c.stats


def _store(db: Session, user: User, c: KbConnector, folder: str, path: str, data: bytes) -> UserFile | None:
    name = PurePosixPath(path).name
    kind, ext = files_svc.classify(name, data[:64])
    storage.ensure_user_quota(db, user, len(data))
    fid = uuid.uuid4()
    dst = files_svc.user_dir(user.id) / f"{fid}{ext}"
    dst.write_bytes(data)
    f = UserFile(id=fid, owner_id=user.id, project_id=c.project_id, folder=folder, filename=safe_name(PurePosixPath(name).stem) + ext,
                 mime=files_svc.guess_mime(ext), kind=kind, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                 path=str(dst.relative_to(get_settings().data_root)), text_status="pending", source="connector",
                 meta={"connector": str(c.id), "remote_path": path[:1000]})
    db.add(f)
    db.flush()
    return f


def _index(db: Session, f: UserFile, c: KbConnector) -> None:
    try:
        res = files_svc.process(f)
        f.text_status = {"ready": "ready", "unsupported": "unsupported"}.get(res["status"], "failed")
        f.text_chars = res["chars"]
        f.meta = {**(f.meta or {}), "pages": res["pages"]}
        db.commit()
        if f.text_status != "ready" or not c.collection_id:
            return
        doc = KbDocument(collection_id=c.collection_id, file_id=f.id, owner_id=f.owner_id, status="indexing")
        db.add(doc)
        db.commit()
        kb.index_document(db, doc)
    except kb.KbError as e:
        db.rollback()
        f.meta = {**(f.meta or {}), "index_error": e.message[:300]}
        db.commit()
    except Exception as e:  # noqa: BLE001  (hostile/corrupt documents)
        db.rollback()
        f.text_status, f.meta = "failed", {**(f.meta or {}), "extract_error": str(e)[:300]}
        db.commit()


def _drop_file(db: Session, file_id) -> None:
    if not file_id:
        return
    f = db.get(UserFile, file_id)
    if f:
        for d in db.scalars(select(KbDocument).where(KbDocument.file_id == f.id)):
            db.delete(d)
        try:
            files_svc.abs_path(f).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(f)


def due(db: Session) -> list[KbConnector]:
    now = datetime.now(timezone.utc)
    out = []
    for c in db.scalars(select(KbConnector).where(KbConnector.enabled.is_(True), KbConnector.schedule != "manual")):
        every = timedelta(hours=1) if c.schedule == "hourly" else timedelta(days=1)
        if c.status != "syncing" and (c.last_sync_at is None or now - c.last_sync_at >= every):
            out.append(c)
    return out
