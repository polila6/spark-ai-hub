"""Helper modules copied into every workspace (.sah/) and chat sandbox run so the model's code can use them:

sah_plot (legible figures and tables), sah_tex (LaTeX tables/figures that fit) and sah_check (PDF/figure quality check).
They are plain files, never imported by the backend; the sandbox image already has everything they need."""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).parent
FILES = ("sah_plot.py", "sah_tex.py", "sah_check.py")
PATH_IN_SANDBOX = "/work/.sah"
README = """# .sah — ayudantes del Spark AI Hub (no los borres; se reinstalan solos)
sah_plot.py   gráficas y tablas legibles: new_fig / save_fig / table_image   (import sah_plot)
sah_tex.py    tablas y figuras LaTeX que caben en la página                    (import sah_tex)
sah_check.py  revisa un PDF o una imagen:  python .sah/sah_check.py informe.pdf
"""


def _digest() -> str:
    h = hashlib.sha256()
    for n in FILES:
        h.update((HERE / n).read_bytes())
    return h.hexdigest()[:16]


def install(dest: Path) -> None:
    """Copy the helpers into `dest` (idempotent: untouched when the installed version is current)."""
    stamp = dest / ".version"
    want = _digest()
    try:
        if stamp.exists() and stamp.read_text().strip() == want and all((dest / n).exists() for n in FILES):
            return
        dest.mkdir(parents=True, exist_ok=True)
        for n in FILES:
            (dest / n).write_bytes((HERE / n).read_bytes())
        (dest / "README.md").write_text(README)
        stamp.write_text(want)
    except OSError:
        pass  # a read-only or full workspace must not stop the program from running


def with_path(code: str, language: str) -> str:
    """Make `import sah_plot` work in user code: python gets sys.path on its first line (line numbers stay the same)."""
    if language == "python":
        first = code.lstrip("﻿").split("\n", 1)[0].lstrip()
        if first.startswith(("from __future__", '"""', "'''", "#!", "#")) or code[:1] in (" ", "\t"):
            return code  # cannot prefix safely; the helpers are still importable with sys.path.insert(0, '/work/.sah')
        return f"import sys; sys.path.insert(0, '{PATH_IN_SANDBOX}'); " + code
    if language == "bash":
        return f"export PYTHONPATH={PATH_IN_SANDBOX}${{PYTHONPATH:+:$PYTHONPATH}}\n" + code
    return code
