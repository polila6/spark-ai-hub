"""sah_check — quality check of what you produced, before you tell the user it is done (Spark AI Hub helper).

    python .sah/sah_check.py informe/informe.pdf            # PDF: layout + LaTeX log (informe.log next to it)
    python .sah/sah_check.py figs/*.png                      # pictures: cut-off content, low resolution
    python .sah/sah_check.py informe.pdf --pages 1-30 --margin-mm 12

For a PDF it reports, with page numbers: ink too close to the page edge (a table or figure sticking out), words printed on top
of other words, and — from the .log — Overfull \\hbox (too wide) lines with their source line, floats too large, undefined
references/citations and missing files. Exit code 1 when something needs fixing. Fix, recompile, run it again.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _pages(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(1, n + 1))
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return [p for p in out if 1 <= p <= n]


def check_log(log: Path, min_pt: float = 3.0) -> list[str]:
    if not log.exists():
        return [f"(no hay {log.name}: compila con latexmk para que se pueda revisar el log)"]
    text = log.read_text(errors="replace")
    issues: list[str] = []
    over = []
    for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\)([^\n]*)", text):
        pt = float(m.group(1))
        ln = re.search(r"lines? (\d+)", m.group(2))
        if pt >= min_pt:
            over.append((pt, ln.group(1) if ln else "?"))
    if over:
        over.sort(reverse=True)
        issues.append(f"{len(over)} cajas demasiado anchas (≥{min_pt:g} pt); las peores: "
                      + ", ".join(f"{pt:.0f} pt en la línea {ln}" for pt, ln in over[:6]))
    for pat, msg in ((r"Float too large for page by ([\d.]+)pt", "figura/tabla más grande que la página"),
                     (r"LaTeX Warning: Reference `([^']+)' on page \d+ undefined", "referencia sin definir"),
                     (r"LaTeX Warning: Citation `([^']+)' on page \d+ undefined", "cita sin definir"),
                     (r"! LaTeX Error: File `([^']+)' not found", "archivo que falta"),
                     (r"LaTeX Warning: File `([^']+)' not found", "imagen/archivo que falta"),
                     (r"Missing character: There is no (.{1,20}) in font", "carácter que la fuente no tiene")):
        found = sorted(set(re.findall(pat, text)))
        if found:
            issues.append(f"{msg}: {', '.join(found[:8])}" + (f" (+{len(found) - 8})" if len(found) > 8 else ""))
    errs = re.findall(r"^! (.{1,100})", text, re.M)
    if errs:
        issues.append(f"errores de compilación: {'; '.join(dict.fromkeys(errs))[:300]}")
    return issues


def _char_clashes(page, limit: int = 8000) -> int:
    """Pairs of letters whose boxes overlap (text drawn over text). Doubled letters of fake-bold are ignored."""
    chars = [c for c in page.chars[:limit] if c.get("text", "").strip() and c["x1"] > c["x0"] and c["bottom"] > c["top"]]
    chars.sort(key=lambda c: c["x0"])
    hits = 0
    for i, a in enumerate(chars):
        for b in chars[i + 1:i + 60]:
            if b["x0"] >= a["x1"] - 0.3:
                break
            w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if w <= 0.8 or h <= 0.8:
                continue
            if a["text"] == b["text"] and abs(a["x0"] - b["x0"]) < 1.2 and abs(a["top"] - b["top"]) < 1.2:
                continue
            small = min((a["x1"] - a["x0"]) * (a["bottom"] - a["top"]), (b["x1"] - b["x0"]) * (b["bottom"] - b["top"]))
            if w * h > 0.45 * small:
                hits += 1
    return hits


def check_pdf(pdf: Path, pages: str | None = None, margin_mm: float = 10.0) -> list[str]:
    import pdfplumber
    from PIL import Image

    issues = check_log(pdf.with_suffix(".log"))
    near: list[str] = []
    clash: list[str] = []
    with pdfplumber.open(pdf) as doc:
        n = len(doc.pages)
        wanted = _pages(pages, n)
        with tempfile.TemporaryDirectory() as tmp:
            for p in wanted:
                page = doc.pages[p - 1]
                W, H = float(page.width), float(page.height)
                # ink near the page edge, measured on a low-resolution render
                subprocess.run(["pdftoppm", "-r", "40", "-f", str(p), "-l", str(p), "-png", str(pdf), f"{tmp}/p"],
                               check=False, capture_output=True)
                img = next(iter(sorted(Path(tmp).glob(f"p-*{p}.png"))), None)
                if img:
                    with Image.open(img) as im:
                        g = im.convert("L")
                        bw, bh = g.size
                        mx, my = max(1, round(margin_mm / 25.4 * 40)), max(1, round(margin_mm / 25.4 * 40))
                        px = g.load()
                        dark = lambda x, y: px[x, y] < 200  # noqa: E731
                        sides = {"izquierdo": sum(dark(x, y) for x in range(mx - 1) for y in range(0, bh, 2)),
                                 "derecho": sum(dark(x, y) for x in range(bw - mx + 1, bw) for y in range(0, bh, 2)),
                                 "superior": sum(dark(x, y) for y in range(my - 1) for x in range(0, bw, 2)),
                                 "inferior": sum(dark(x, y) for y in range(bh - my + 1, bh) for x in range(0, bw, 2))}
                    bad = [k for k, v in sides.items() if v > 6]
                    if bad:
                        near.append(f"p.{p} ({'/'.join(bad)})")
                    img.unlink()
                # words printed over other words (overlapping text in figures/tables)
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False)[:1500]
                hits = 0
                for i, a in enumerate(words):
                    for b in words[i + 1:i + 40]:
                        w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
                        h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
                        if w > 2 and h > 2 and w * h > 0.35 * min((a["x1"] - a["x0"]) * (a["bottom"] - a["top"]),
                                                                    (b["x1"] - b["x0"]) * (b["bottom"] - b["top"])):
                            hits += 1
                            break
                chars = _char_clashes(page)  # (letters on letters also when the words are merged into one)
                if hits >= 2 or chars >= 4:
                    clash.append(f"p.{p} ({max(hits, chars // 4)} palabras encimadas)")
    if near:
        issues.append(f"contenido a menos de {margin_mm:g} mm del borde: " + ", ".join(near[:20]) + (" …" if len(near) > 20 else ""))
    if clash:
        issues.append("texto sobre texto: " + ", ".join(clash[:20]))
    return issues


def check_png(path: Path) -> list[str]:
    sys.path.insert(0, str(Path(__file__).parent))
    from sah_plot import check_image

    return check_image(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--pages")
    ap.add_argument("--margin-mm", type=float, default=10.0)
    ns = ap.parse_args()
    bad = 0
    for f in ns.files:
        p = Path(f)
        if not p.exists():
            print(f"✗ {f}: no existe")
            bad += 1
            continue
        try:
            issues = (check_pdf(p, ns.pages, ns.margin_mm) if p.suffix.lower() == ".pdf"
                      else check_png(p) if p.suffix.lower() in (".png", ".jpg", ".jpeg") else [])
        except Exception as e:  # noqa: BLE001
            issues = [f"no se pudo revisar: {e}"]
        if issues:
            bad += 1
            print(f"✗ {f}")
            for i in issues:
                print(f"    - {i}")
        else:
            print(f"✓ {f}: sin problemas detectados")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
