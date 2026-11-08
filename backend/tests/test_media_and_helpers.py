"""Web pictures for documents, the figure/table helpers shipped to the sandbox and the reviewer agent that must read what it reviews.
Run: docker compose run --rm --no-deps backend pytest -q tests/test_media_and_helpers.py"""
import io
import json

import pytest
from PIL import Image

from app.services import agent as agent_mod
from app.services import media_fetch as mf
from app.services import sandbox_helpers as helpers


def _png(w=100, h=80, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 120)).save(buf, "PNG")
    return buf.getvalue()


def _webp(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "WEBP")
    return buf.getvalue()


def test_process_image_converts_webp_to_jpeg_and_resizes():
    out = mf.process_image(_webp(4000, 3000), "image/webp", max_px=2000)
    assert out["ext"] == ".jpg" and out["data"][:3] == b"\xff\xd8\xff"
    assert max(out["width"], out["height"]) == 2000 and out["original_size"] == (4000, 3000)


def test_process_image_keeps_transparency_as_png():
    out = mf.process_image(_png(120, 90, "RGBA"), "image/png")
    assert out["ext"] == ".png" and out["data"][:4] == b"\x89PNG"


def test_process_image_refuses_svg_tiny_and_garbage():
    with pytest.raises(mf.MediaError, match="SVG"):
        mf.process_image(b"<svg/>", "image/svg+xml")
    with pytest.raises(mf.MediaError, match="pequeña"):
        mf.process_image(_png(20, 20), "image/png")
    with pytest.raises(mf.MediaError, match="no es una imagen válida"):
        mf.process_image(b"not an image at all", "image/jpeg")


def _fake_get(body, ct, url="https://example.org/f"):
    return lambda u, m, a: {"url": url, "status": 200, "content_type": ct, "body": body, "headers": {}}


def test_fetch_any_image_pdf_csv_and_refusals(monkeypatch):
    monkeypatch.setattr(mf, "_get", _fake_get(_png(300, 200), "image/png"))
    assert mf.fetch_any("https://example.org/a.png")["kind"] == "image"
    monkeypatch.setattr(mf, "_get", _fake_get(b"%PDF-1.7 ...", "application/octet-stream"))
    r = mf.fetch_any("https://example.org/doc")
    assert r["kind"] == "file" and r["ext"] == ".pdf"
    monkeypatch.setattr(mf, "_get", _fake_get(b"a,b\n1,2\n", "text/plain"))
    assert mf.fetch_any("https://example.org/d", ".csv")["ext"] == ".csv"  # the wanted extension wins over text/plain
    monkeypatch.setattr(mf, "_get", _fake_get(b"<!DOCTYPE html><html>", "text/html"))
    with pytest.raises(mf.MediaError, match="fetch_url"):
        mf.fetch_any("https://example.org/page")
    monkeypatch.setattr(mf, "_get", _fake_get(b"MZ\x90", "application/x-msdownload"))
    with pytest.raises(mf.MediaError, match="no permitido"):
        mf.fetch_any("https://example.org/x.exe")


def test_get_rejects_non_http_and_reports_denied(monkeypatch):
    with pytest.raises(mf.MediaError, match="http"):
        mf._get("file:///etc/passwd", 10, "*/*")
    monkeypatch.setattr(mf.web, "fetch", lambda *a, **k: {"url": "https://x.org/i.jpg", "status": 403, "content_type": "", "body": b"", "headers": {}})
    with pytest.raises(mf.MediaError, match="403"):
        mf._get("https://x.org/i.jpg", 10, "*/*")


COMMONS = {"query": {"pages": {
    "1": {"title": "File:Feijoo.jpg", "index": 1, "imageinfo": [{"mime": "image/jpeg", "url": "https://u/a.jpg", "thumburl": "https://u/t.jpg",
          "descriptionurl": "https://c/File:Feijoo.jpg", "width": 3000, "height": 2000, "thumbwidth": 1600, "thumbheight": 1066,
          "extmetadata": {"Artist": {"value": "<a href='x'>PP</a> Partido"}, "LicenseShortName": {"value": "CC0"}}}]},
    "2": {"title": "File:Logo.svg", "index": 2, "imageinfo": [{"mime": "image/svg+xml", "url": "https://u/l.svg"}]}}}}


def test_search_images_parses_author_licence_and_skips_svg(monkeypatch):
    monkeypatch.setattr(mf, "_json", lambda url: COMMONS)
    res = mf.search_images("Feijóo", "commons", 5)
    assert len(res) == 1 and res[0]["license"] == "CC0" and res[0]["author"] == "PP Partido" and res[0]["url"] == "https://u/t.jpg"
    assert "CC0" in mf.credit_line(res[0])


def test_search_images_falls_through_sources_and_reports_errors(monkeypatch):
    def boom(url):
        raise mf.MediaError("caído")
    monkeypatch.setattr(mf, "_json", boom)
    with pytest.raises(mf.MediaError, match="No se pudo buscar"):
        mf.search_images("x", "auto", 3)
    with pytest.raises(mf.MediaError, match="Falta"):
        mf.search_images("  ")


def test_helpers_install_is_idempotent_and_repairs(tmp_path):
    dest = tmp_path / ".sah"
    helpers.install(dest)
    assert all((dest / n).exists() for n in helpers.FILES) and (dest / "README.md").exists()
    stamp = (dest / ".version").read_text()
    (dest / "sah_plot.py").write_text("broken")
    helpers.install(dest)  # same version stamp: left alone
    assert (dest / "sah_plot.py").read_text() == "broken"
    (dest / ".version").unlink()
    helpers.install(dest)  # no stamp: reinstalled
    assert (dest / "sah_plot.py").read_text() != "broken" and (dest / ".version").read_text() == stamp


def test_helpers_install_never_raises_on_readonly(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    helpers.install(f / "sub")  # a path under a regular file cannot be created: ignored


def test_with_path_keeps_line_numbers():
    code = "import os\nprint(1)\n"
    out = helpers.with_path(code, "python")
    assert out.count("\n") == code.count("\n") and out.startswith("import sys; sys.path.insert(0, '/work/.sah'); import os")
    assert helpers.with_path("from __future__ import annotations\n", "python").startswith("from __future__")
    assert helpers.with_path('"""doc"""\n', "python").startswith('"""doc"""')
    assert "PYTHONPATH=/work/.sah" in helpers.with_path("ls", "bash")
    assert helpers.with_path("console.log(1)", "node") == "console.log(1)"


def test_helper_modules_compile_and_ship_the_documented_api():
    import ast
    for n in helpers.FILES:
        ast.parse((helpers.HERE / n).read_text())
    plot = (helpers.HERE / "sah_plot.py").read_text()
    assert all(f"def {f}(" in plot for f in ("new_fig", "save_fig", "table_image", "audit", "autofix", "check_image"))
    assert "def df_to_latex(" in (helpers.HERE / "sah_tex.py").read_text()


def test_reviewer_agent_can_read_what_it_reviews():
    rev = agent_mod.BUILTIN_AGENTS["revisor"]
    assert {"ws_list", "ws_read", "read_document"} <= set(rev["tools"])
    assert "LITERAL" in rev["instructions"] and not ({"ws_write", "ws_run", "ws_edit"} & set(rev["tools"]))


def test_every_agent_tool_is_a_sub_agent_tool():
    for name, prof in agent_mod.BUILTIN_AGENTS.items():
        assert set(prof["tools"]) <= agent_mod.SUB_AGENT_TOOLS, name
    assert {"search_images", "download_file"} <= agent_mod.SUB_AGENT_TOOLS
    assert "download_file" in agent_mod.WS_WRITE_TOOLS  # sub-agents may only download inside their write scope


def test_figure_agents_get_the_visual_rules():
    for n in ("visualizador", "maquetador-latex", "estadistico"):
        assert "sah_plot" in agent_mod.BUILTIN_AGENTS[n]["instructions"], n
    assert "download_file" in agent_mod.BUILTIN_AGENTS["maquetador-latex"]["tools"]


class _Ws:
    id = "w"
    name = "ws"


class _Svc:
    def __init__(self):
        self.files = {}

    def write(self, db, ws, path, data):
        self.files[path] = data
        return {"path": path, "size": len(data)}

    def read(self, ws, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"content": self.files[path].decode()}


class _Db:
    def commit(self):
        pass


def _runner(monkeypatch, svc):
    r = object.__new__(agent_mod.Runner)
    r.image_meta, r.workspace, r.project_id = {}, _Ws(), None
    r._ws_call = lambda fn: fn(_Db(), _Ws(), svc)
    return r


def test_download_file_saves_into_workspace_with_credit(monkeypatch):
    svc = _Svc()
    r = _runner(monkeypatch, svc)
    url = "https://upload.example/feijoo.jpg"
    r.image_meta[url] = {"title": "Feijoo 2023.jpg", "author": "PP", "license": "CC0", "page_url": "https://c/File:F", "source": "Commons"}
    monkeypatch.setattr(mf, "_get", _fake_get(_webp(1200, 900), "image/webp", url))
    res = r.tool_download_file("c1", {"url": url})
    assert res["ok"] and res["workspace"]["changed"] == ["imagenes/feijoo-2023.jpg"]
    assert svc.files["imagenes/feijoo-2023.jpg"][:3] == b"\xff\xd8\xff"  # webp became a JPEG LaTeX can use
    assert json.loads(svc.files["imagenes/creditos.json"])["imagenes/feijoo-2023.jpg"]["license"] == "CC0"
    assert "includegraphics" in res["content"] and "CC0" in res["content"]


def test_download_file_corrects_the_extension_to_the_real_format(monkeypatch):
    svc = _Svc()
    r = _runner(monkeypatch, svc)
    monkeypatch.setattr(mf, "_get", _fake_get(_png(300, 200), "image/png"))
    res = r.tool_download_file("c1", {"url": "https://x.org/a", "path": "imagenes/foto.jpg"})
    assert res["workspace"]["changed"] == ["imagenes/foto.jpg"] and svc.files["imagenes/foto.jpg"][:3] == b"\xff\xd8\xff"
    monkeypatch.setattr(mf, "_get", _fake_get(_png(300, 200, "RGBA"), "image/png"))
    res = r.tool_download_file("c2", {"url": "https://x.org/b", "path": "imagenes/logo.jpg"})
    assert res["workspace"]["changed"] == ["imagenes/logo.png"]  # transparency needs PNG


def test_download_file_errors_are_agent_errors(monkeypatch):
    r = _runner(monkeypatch, _Svc())
    monkeypatch.setattr(mf, "_get", _fake_get(b"<html>", "text/html"))
    with pytest.raises(agent_mod.AgentError, match="fetch_url"):
        r.tool_download_file("c", {"url": "https://x.org/p"})


# ---------------------------------------------------------------- sub-agent reports and the image circuit breaker
import threading
from types import SimpleNamespace


def _sub_runner(monkeypatch, steps):
    """A Runner able to run tool_delegate_task with a scripted model: `steps` = list of (text, calls)."""
    r = object.__new__(agent_mod.Runner)
    r.agents = {"revisor": {**agent_mod.BUILTIN_AGENTS["revisor"], "builtin": True},
                "sinherramientas": {"description": "x", "instructions": "y", "tools": [], "skills": []}}
    r.t = SimpleNamespace(lang="es", model_id="m", user_id=None)
    r.workspace, r.project_id, r.flags = None, None, {"web": True, "docs": True, "skills": True, "agents": True, "code": True}
    r.ws_claims, r.ws_claims_lock = {}, threading.Lock()
    r.stop, r.emit, r.small = (lambda: False), (lambda e: None), False
    r.image_meta, r.image_failures = {}, 0
    r._all_specs, r._fit = (lambda: []), (lambda *a, **k: None)
    seq = iter(steps)
    r._llm_step = lambda messages, specs, max_out, thinking=False: (*next(seq), None)
    monkeypatch.setattr(agent_mod.scheduler, "ensure_loaded", lambda *a, **k: SimpleNamespace(id="i"))
    monkeypatch.setattr(agent_mod.scheduler, "begin_request", lambda *a: None)
    monkeypatch.setattr(agent_mod.scheduler, "end_request", lambda *a: None)
    return r


def test_subagent_that_never_opened_anything_is_flagged_unverified(monkeypatch):
    r = _sub_runner(monkeypatch, [("# Informe\nEl archivo cap03.tex línea 142 dice «PP 145 escaños».", [])])
    res = r.tool_delegate_task("c", {"agent": "sinherramientas", "task": "Revisa los archivos de /work/informe/ y cita líneas"})
    assert "SIN VERIFICAR" in res["summary"] and res["content"].index("SIN VERIFICAR") < res["content"].index("cap03.tex")
    assert "no apliques" in res["agent"]["report"].lower() or "No apliques" in res["agent"]["report"]


def test_subagent_without_file_references_is_not_flagged(monkeypatch):
    r = _sub_runner(monkeypatch, [("Respuesta directa", [])])
    res = r.tool_delegate_task("c", {"agent": "sinherramientas", "task": "Dame tres ideas de nombre"})
    assert "SIN VERIFICAR" not in res["summary"] and res["agent"]["report"] == "Respuesta directa"


def test_subagent_with_empty_last_message_is_asked_for_a_report_once(monkeypatch):
    r = _sub_runner(monkeypatch, [("", []), ("Hecho: escribí informe/cap1.tex; falta la bibliografía.", [])])
    res = r.tool_delegate_task("c", {"agent": "sinherramientas", "task": "Escribe algo"})
    assert "cap1.tex" in res["agent"]["report"] and "no devolvió" not in res["agent"]["report"]


def test_subagent_that_stays_silent_still_returns_a_summary_of_what_it_did(monkeypatch):
    r = _sub_runner(monkeypatch, [("", []), ("", [])])
    res = r.tool_delegate_task("c", {"agent": "sinherramientas", "task": "Escribe algo"})
    assert "no devolvió informe" in res["agent"]["report"] and "Comprueba el resultado" in res["agent"]["report"]


def test_image_generation_is_switched_off_after_two_failures():
    r = object.__new__(agent_mod.Runner)
    r.image_failures = 0
    r._image_guard()
    r.image_failures = agent_mod.IMAGE_FAILS_MAX
    with pytest.raises(agent_mod.AgentError, match="no insistas"):
        r._image_guard()
