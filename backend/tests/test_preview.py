"""Document viewer helpers (pure file logic). Run: docker compose run --rm --no-deps backend pytest -q tests"""
from types import SimpleNamespace

import pytest

from app.services import preview


def test_csv_table_and_truncation(tmp_path):
    p = tmp_path / "datos.csv"
    p.write_text("a;b\n" + "".join(f"{i};{i}\n" for i in range(10)))
    t = preview.table(p, "datos.csv", rows=5)["sheets"][0]
    assert t["rows"][0] == ["a", "b"] and len(t["rows"]) == 6 and t["truncated"] is True
    assert preview.table(p, "datos.csv", rows=50)["sheets"][0]["truncated"] is False


def test_excel_keeps_every_sheet(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Uno"
    wb.active.append(["x", 1])
    wb.create_sheet("Dos").append(["y", None])
    wb.save(tmp_path / "libro.xlsx")
    sheets = preview.table(tmp_path / "libro.xlsx", "libro.xlsx")["sheets"]
    assert [s["name"] for s in sheets] == ["Uno", "Dos"] and sheets[0]["rows"] == [["x", "1"]] and sheets[1]["rows"] == [["y", ""]]


def test_table_refuses_other_types(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(preview.PreviewError):
        preview.table(tmp_path / "a.txt", "a.txt")


def test_plain_text_rejects_binaries_and_cuts_big_files(tmp_path, monkeypatch):
    (tmp_path / "b.bin").write_bytes(b"\x00\x01binario")
    with pytest.raises(preview.PreviewError):
        preview.plain_text(tmp_path / "b.bin")
    monkeypatch.setattr(preview, "MAX_TEXT_BYTES", 5)
    (tmp_path / "t.txt").write_text("0123456789")
    assert preview.plain_text(tmp_path / "t.txt").startswith("01234\n…")


def test_office_conversion_is_cached_and_uses_the_sandbox(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(preview, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(preview.sandbox, "enabled", lambda db: True)

    def fake(db, user, script, files, timeout_s):
        calls.append((script, list(files)))
        return {"exit_code": 0, "outputs": {"out/doc.pdf": b"%PDF-1.7"}, "stderr": "", "stdout": ""}

    monkeypatch.setattr(preview.sandbox, "run_raw", fake)
    src = tmp_path / "x.pptx"
    src.write_bytes(b"PK..")
    a = preview.office_pdf(None, None, src, "Mi presentación.pptx")
    b = preview.office_pdf(None, None, src, "otro nombre.pptx")
    assert a == b and a.read_bytes() == b"%PDF-1.7" and len(calls) == 1 and calls[0][1] == ["doc.pptx"]
    assert "Mi presentación" not in calls[0][0]  # file names never reach the shell script


def test_failed_conversion_says_so_and_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(preview.sandbox, "enabled", lambda db: True)
    monkeypatch.setattr(preview.sandbox, "run_raw", lambda *a, **k: {"exit_code": 1, "outputs": {}, "stderr": "", "stdout": ""})
    src = tmp_path / "x.docx"
    src.write_bytes(b"PK..")
    with pytest.raises(preview.PreviewError):
        preview.office_pdf(None, None, src, "x.docx")
    assert not list((tmp_path / "cache" / "previews").glob("*.pdf"))


def test_only_office_types_are_converted(tmp_path):
    (tmp_path / "a.exe").write_bytes(b"MZ")
    with pytest.raises(preview.PreviewError):
        preview.office_pdf(None, None, tmp_path / "a.exe", "a.exe")
