"""Files a conversation keeps between run_code calls (pure file logic). Run: docker compose run --rm --no-deps backend pytest -q tests"""
import uuid
from types import SimpleNamespace

import pytest

from app.services import sandbox

CID = uuid.uuid4()


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "get_settings", lambda: SimpleNamespace(data_root=tmp_path))
    return tmp_path


def run_dir(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def test_files_survive_to_the_next_run_with_folders(tmp_path):
    a = run_dir(tmp_path, "a")
    (a / "revision").mkdir()
    (a / "revision" / "informe.tex").write_text("v1")
    assert sandbox.restore_conversation_files(CID, a) == set()
    assert sandbox.keep_conversation_files(CID, a, set())
    b = run_dir(tmp_path, "b")
    restored = sandbox.restore_conversation_files(CID, b)
    assert restored == {"revision/informe.tex"} and (b / "revision" / "informe.tex").read_text() == "v1"


def test_inputs_skills_and_the_program_are_not_kept(tmp_path):
    a = run_dir(tmp_path, "a")
    for rel in ("in/datos.csv", "skills/x/SKILL.md", ".main.py", "sub/.main.txt", "resultado.txt"):
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_text("x")
    sandbox.keep_conversation_files(CID, a, set())
    assert set(sandbox._kept_files(sandbox._conv_dir(CID))) == {"sub/.main.txt", "resultado.txt"}


def test_symlinks_are_never_followed(tmp_path):
    secret = tmp_path / "secreto.txt"
    secret.write_text("no")
    a = run_dir(tmp_path, "a")
    (a / "enlace.txt").symlink_to(secret)
    (a / "carpeta").symlink_to(tmp_path)
    (a / "bien.txt").write_text("ok")
    sandbox.keep_conversation_files(CID, a, set())
    store = sandbox._conv_dir(CID)
    assert not (store / "enlace.txt").exists() and not (store / "carpeta").exists() and (store / "bien.txt").read_text() == "ok"


def test_a_run_removes_only_what_it_deleted(tmp_path):
    a = run_dir(tmp_path, "a")
    (a / "uno.txt").write_text("1")
    (a / "dos.txt").write_text("2")
    sandbox.keep_conversation_files(CID, a, set())
    b = run_dir(tmp_path, "b")
    restored = sandbox.restore_conversation_files(CID, b)
    (b / "uno.txt").unlink()
    store = sandbox._conv_dir(CID)
    (store / "de_otro_agente.txt").write_text("3")  # written meanwhile by an agent running in parallel
    sandbox.keep_conversation_files(CID, b, restored)
    assert sorted(p.name for p in store.iterdir()) == ["de_otro_agente.txt", "dos.txt"]


def test_over_the_limit_nothing_is_kept_and_it_says_so(tmp_path):
    a = run_dir(tmp_path, "a")
    (a / "grande.bin").write_bytes(b"x" * 100)
    assert sandbox.keep_conversation_files(CID, a, set(), limit_bytes=10) is False
    assert not sandbox._conv_dir(CID).exists()


def test_forget_and_purge(tmp_path):
    import os

    a = run_dir(tmp_path, "a")
    (a / "f.txt").write_text("x")
    sandbox.keep_conversation_files(CID, a, set())
    assert sandbox.purge_old_conversation_files(days=30) == 0
    os.utime(sandbox._conv_dir(CID), (1, 1))
    assert sandbox.purge_old_conversation_files(days=30) == 1
    sandbox.keep_conversation_files(CID, a, set())
    sandbox.forget_conversation_files(CID)
    assert not sandbox._conv_dir(CID).exists()
