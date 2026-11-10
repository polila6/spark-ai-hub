"""sah_fig — figures drawn in LaTeX (TikZ / pgfplots) that match the document and never overlap (Spark AI Hub helper).

    from sah_fig import build, fig_tex
    build("cronologia", r'''
    \\begin{tikzpicture}
      \\begin{axis}[width=14cm, height=7cm, xlabel={Edad (Ma)}, ybar, symbolic x coords={A,B,C}]
        \\addplot coordinates {(A,3) (B,5) (C,2)};
      \\end{axis}
    \\end{tikzpicture}''')            # → figs/cronologia.pdf (vector) + figs/cronologia.preview.png, prints OK / PROBLEMAS
    open("fig1.tex", "w").write(fig_tex("figs/cronologia.pdf", "Cronología", "fig:crono"))   # then \\input{fig1}

`body` is only the picture (a tikzpicture, or a pgfplots axis inside it); file paths inside it (data/x.csv, imagenes/y.jpg) are
relative to the folder you run the script from; the file around it (standalone class, babel
español, tikz libraries, pgfplots, xcolor) is added for you. `packages` adds \\usepackage lines (e.g. "\\usepackage{forest}"),
`libraries` extra \\usetikzlibrary names. After compiling, the PDF is checked for words printed over other words and the log
for errors and overfull boxes; with PROBLEMAS the figure is NOT ready: shorten labels, add `text width`/`align=center` to
nodes, `anchor`/`above=`/`xshift` to separate them, enlarge the picture or draw less, and build again. Then LOOK at
figs/<name>.preview.png with view_image before using it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

HEAD = r"""\documentclass[border=6pt]{standalone}
\usepackage[T1]{fontenc}
\usepackage[spanish]{babel}
\usepackage{lmodern}
\usepackage{xcolor}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,calc,shapes.geometric,shapes.misc,fit,backgrounds,decorations.pathreplacing,patterns,matrix,chains}
\usepackage{pgfplots}
\pgfplotsset{compat=newest}
\usepgfplotslibrary{fillbetween,groupplots,dateplot}
"""


def build(name: str, body: str, packages: str = "", libraries: str = "", outdir: str = "figs", preamble: str | None = None,
          engine: str = "pdflatex", preview: bool = True, dpi: int = 110) -> Path | None:
    """Write outdir/name.tex around `body`, compile it, check it and print OK or PROBLEMAS. Returns the PDF path (None if it
    did not compile)."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    src = out / f"{name}.tex"
    head = preamble if preamble is not None else HEAD
    lib = f"\\usetikzlibrary{{{libraries}}}\n" if libraries else ""
    src.write_text(f"{head}{packages}\n{lib}\\begin{{document}}\n{body.strip()}\n\\end{{document}}\n", encoding="utf-8")
    eng = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}.get(engine, "-pdf")
    # compiled from the CURRENT directory (not from outdir): paths inside `body` (data/x.csv, imagenes/y.jpg) are relative to
    # where the script runs, like in the document
    r = subprocess.run(["latexmk", "-g", eng, "-interaction=nonstopmode", "-halt-on-error", f"-outdir={out}", str(src)],
                       capture_output=True, text=True, timeout=300)
    pdf = out / f"{name}.pdf"
    if r.returncode != 0 or not pdf.exists():
        log = out / f"{name}.log"
        text = log.read_text(errors="replace") if log.exists() else (r.stdout + r.stderr)
        lines = text.splitlines()
        first = next((i for i, ln in enumerate(lines) if ln.startswith("!")), None)
        shown = "\n".join(ln for ln in lines[first:first + 9] if ln.strip()) if first is not None else text[-800:]
        print(f"PROBLEMAS {name}: no compila\n{shown}\n(corrige ese error en el cuerpo de la figura y vuelve a llamar a build())")
        return None
    issues = _check(pdf)  # (before cleaning: the log is part of the check)
    subprocess.run(["latexmk", "-c", f"-outdir={out}", str(src)], capture_output=True)
    if preview and shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(pdf), str(out / f"{name}.preview")], capture_output=True)
    if issues:
        print(f"PROBLEMAS {pdf}: " + "; ".join(issues))
        print("La figura NO está lista: corrige (etiquetas más cortas, nodos separados, más tamaño) y vuelve a llamar a build().")
    else:
        print(f"OK {pdf}" + (f" · vista previa: {out / (name + '.preview.png')}" if preview else ""))
    return pdf


def _check(pdf: Path) -> list[str]:
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from sah_check import check_pdf

        return [i for i in check_pdf(pdf, margin_mm=0.4) if "borde" not in i]
    except Exception as e:  # noqa: BLE001
        return [f"no se pudo revisar ({type(e).__name__})"]


def fig_tex(path: str, caption: str, label: str, width: float = 0.95, placement: str = "htbp", source: str | None = None) -> str:
    """The \\begin{figure} block for a figure built with build() (see sah_tex.figure)."""
    from sah_tex import figure

    return figure(path, caption, label, width, placement, source)
