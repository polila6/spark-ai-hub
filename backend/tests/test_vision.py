"""Small vision model that looks at pictures and document pages (tool view_image).
Run: docker compose run --rm --no-deps backend pytest -q tests/test_vision.py"""
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.models import ModelStatus
from app.services import agent as agent_mod
from app.services import vision


def M(alias, gb, status=ModelStatus.installed, caps=("chat", "vision"), backend="ollama"):
    return SimpleNamespace(alias=alias, estimated_memory_gb=gb, status=status, capabilities=list(caps), backend=backend)


def test_choose_prefers_a_small_installed_vision_model_over_the_loaded_big_one():
    big = M("main-35b", 40, ModelStatus.ready, backend="vllm")
    small = M("qwen3-vl-4b", 4.8)
    assert vision.choose([big, small]) is small
    assert vision.choose([big]) is big  # no small helper: the loaded vision model
    assert vision.choose([M("text-only", 3, caps=("chat",)), M("embed", 1, caps=("embeddings",))]) is None


def test_choose_honours_the_admin_setting_and_ignores_unusable_models():
    a, b = M("a-4b", 4.8), M("b-2b", 2.5)
    assert vision.choose([a, b], "a-4b") is a
    assert vision.choose([a, b], "auto") is b  # smallest first
    assert vision.choose([a, b], "missing") is b  # a stale setting falls back to auto
    assert vision.choose([M("dl", 3, ModelStatus.downloading)], "dl") is None
    assert vision.choose([M("img", 3, backend="comfyui")]) is None


def test_parse_pages():
    assert vision.parse_pages(None, 10) == [1, 2, 3, 4, 5, 6]
    assert vision.parse_pages("2-4,9", 10) == [2, 3, 4, 9]
    assert vision.parse_pages("3-1", 10) == [1, 2, 3]
    assert vision.parse_pages("0,11,x,5", 10) == [5]
    assert vision.parse_pages("1-100", 100) == [1, 2, 3, 4, 5, 6]  # never more than 6 per call
    assert vision.parse_pages(7, 10) == [7]


def _pdf(tmp_path, pages=3):
    imgs = [Image.new("RGB", (400, 560), (255, 255 - 40 * i, 255)) for i in range(pages)]
    p = tmp_path / "doc.pdf"
    imgs[0].save(p, "PDF", save_all=True, append_images=imgs[1:])
    return p


def test_render_pdf_and_count_pages(tmp_path):
    p = _pdf(tmp_path, 3)
    assert vision.pdf_pages(p) == 3
    out = vision.render_pdf(p, [1, 3, 9])
    assert [n for n, _ in out] == [1, 3]  # page 9 does not exist
    assert Image.open(io.BytesIO(out[0][1])).size[0] > 300


def test_downscale_limits_the_long_side():
    buf = io.BytesIO()
    Image.new("RGB", (4000, 1000), "white").save(buf, "PNG")
    assert max(Image.open(io.BytesIO(vision.downscale(buf.getvalue()))).size) == vision.MAX_SIDE


class _Svc:
    def __init__(self, base: Path):
        self.base, self.files = base, {}

    def raw(self, ws, path):
        return self.base / path, "application/pdf"

    def write(self, db, ws, path, data):
        self.files[path] = data
        return {"path": path}


class _Db:
    def commit(self):
        pass


def _runner(monkeypatch, tmp_path, svc):
    r = object.__new__(agent_mod.Runner)
    r.workspace, r.files, r.model = SimpleNamespace(id="w", name="ws"), {}, None
    r.emit = lambda e: None
    r._ws_call = lambda fn: fn(_Db(), SimpleNamespace(), svc)
    helper = M("qwen3-vl-4b", 4.8)
    monkeypatch.setattr(agent_mod.vision, "helper_model", lambda db, cur=None: helper)
    seen = []
    monkeypatch.setattr(agent_mod.vision, "analyze", lambda m, imgs, q=None, labels=None, **k: seen.append((len(imgs), labels, q)) or "- Tabla fuera del margen (página 2)")

    class FakeSession:
        def __enter__(self):
            return _Db()

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(agent_mod, "SessionLocal", FakeSession)
    return r, seen


def test_view_image_reviews_pdf_pages_in_small_batches_and_saves_captures(monkeypatch, tmp_path):
    _pdf(tmp_path, 5)
    svc = _Svc(tmp_path)
    r, seen = _runner(monkeypatch, tmp_path, svc)
    res = r.tool_view_image("c", {"path": "doc.pdf", "pages": "1-5"})
    assert res["ok"] and [n for n, _, _ in seen] == [3, 2]  # 5 pages = 3 + 2: a small model reads a few at a time
    assert seen[0][1] == ["página 1 de 5", "página 2 de 5", "página 3 de 5"]
    assert sorted(svc.files) == [f"capturas/doc-p{i}.png" for i in range(1, 6)]
    assert res["workspace"]["changed"] == sorted(svc.files) and "Tabla fuera del margen" in res["content"]
    assert "AVISO" in res["content"] and "qwen3-vl-4b" in res["content"]


def test_view_image_on_a_picture_and_with_a_question(monkeypatch, tmp_path):
    Image.new("RGB", (300, 200), "white").save(tmp_path / "fig.png")
    r, seen = _runner(monkeypatch, tmp_path, _Svc(tmp_path))
    res = r.tool_view_image("c", {"path": "fig.png", "question": "¿Qué muestra?"})
    assert res["ok"] and "workspace" not in res and seen == [(1, ["imagen"], "¿Qué muestra?")]


def test_view_image_refuses_other_files_and_missing_models(monkeypatch, tmp_path):
    (tmp_path / "a.docx").write_text("x")
    r, _ = _runner(monkeypatch, tmp_path, _Svc(tmp_path))
    with pytest.raises(agent_mod.AgentError, match="solo imágenes y PDF") as e:
        r.tool_view_image("c", {"path": "a.docx"})
    assert "soffice" in (e.value.remediation or "")
    with pytest.raises(agent_mod.AgentError, match="path"):
        r.tool_view_image("c", {})
    monkeypatch.setattr(agent_mod.vision, "helper_model", lambda db, cur=None: None)
    Image.new("RGB", (100, 100)).save(tmp_path / "b.png")
    with pytest.raises(agent_mod.AgentError, match="visión"):
        r.tool_view_image("c", {"path": "b.png"})


def test_view_image_tool_is_offered_only_with_a_vision_model(monkeypatch):
    r = object.__new__(agent_mod.Runner)
    r.workspace, r.flags, r._vision_cache = SimpleNamespace(id="w"), {"code": True, "web": True, "docs": True, "skills": True, "agents": True}, None
    monkeypatch.setattr(agent_mod.vision, "available", lambda db: False)

    class FakeSession:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(agent_mod, "SessionLocal", FakeSession)
    assert r._tool_allowed("view_image") is False
    r._vision_cache = None
    monkeypatch.setattr(agent_mod.vision, "available", lambda db: True)
    assert r._tool_allowed("view_image") is True
    r.workspace, r.flags["code"] = None, False
    assert r._tool_allowed("view_image") is False  # a plain chat without code tools does not get it
