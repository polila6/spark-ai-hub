"""sah_plot — figures and tables that come out legible the first time (Spark AI Hub helper, ships with every sandbox/workspace).

    from sah_plot import new_fig, save_fig, table_image, wrap

    fig, ax = new_fig(w_cm=15, h_cm=8)          # fits the text column of a LaTeX / Word page
    ax.plot(x, y, label="PP")
    ax.set_title("Evolución de la intención de voto")
    ax.legend()
    save_fig(fig, "figs/evolucion.png")         # fixes what it can, prints what is still wrong

    table_image(df, "figs/tabla.png", title="Resultados")   # tables as pictures that never overflow

`save_fig` draws the figure, measures every text (title, labels, ticks, legend, annotations, table cells) and checks that
nothing leaves the picture, nothing overlaps and the legend does not cover data. It wraps long titles, rotates and thins tick
labels, shrinks fonts and moves the legend below the plot before it reports anything it could not solve. READ ITS OUTPUT:
a line starting with «PROBLEMAS» means the picture is NOT ready — change the figure (shorter labels, bigger size, barh
instead of bar, fewer categories) and save again.
"""
from __future__ import annotations

import itertools
import os
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402

__all__ = ["style", "new_fig", "wrap", "audit", "autofix", "save_fig", "table_image", "check_image", "PALETTE"]

# Okabe-Ito: distinguishable for colour-blind readers and in greyscale prints
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]


def style(base: float = 9.0) -> None:
    """Sober defaults sized for a printed page (called on import unless SAH_PLOT_NO_STYLE is set)."""
    matplotlib.rcParams.update({
        "figure.dpi": 100, "savefig.dpi": 200, "figure.constrained_layout.use": True,
        "font.size": base, "axes.titlesize": base + 1.5, "axes.labelsize": base, "xtick.labelsize": base - 1,
        "ytick.labelsize": base - 1, "legend.fontsize": base - 1, "legend.frameon": False,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
        "axes.prop_cycle": matplotlib.cycler(color=PALETTE), "axes.titlelocation": "left",
        "axes.unicode_minus": True, "lines.linewidth": 1.6, "figure.facecolor": "white", "savefig.facecolor": "white",
    })


if not os.environ.get("SAH_PLOT_NO_STYLE"):
    style()


def new_fig(w_cm: float = 15.0, h_cm: float | None = None, nrows: int = 1, ncols: int = 1, **kw):
    """Figure sized in centimetres (default 15 × 9 cm ≈ the text width of an A4 report) with constrained layout."""
    h_cm = h_cm if h_cm else round(w_cm * 0.6, 1)
    kw.setdefault("constrained_layout", True)
    return plt.subplots(nrows, ncols, figsize=(w_cm / 2.54, h_cm / 2.54), **kw)


def wrap(text, width: int = 28) -> str:
    """Break a long label over several lines: ax.set_xticklabels([wrap(t, 14) for t in labels])."""
    return "\n".join(textwrap.wrap(str(text), width=max(6, width), break_long_words=False) or [""])


# ---------------------------------------------------------------------------------------------------- measuring

def _renderer(fig):
    fig.canvas.draw()
    return fig.canvas.get_renderer()


def _texts(fig) -> list[tuple[str, str, object]]:
    """(kind, label, artist) for every visible text of the figure."""
    out = []

    def add(kind, art):
        try:
            if art.get_visible() and art.get_text().strip():
                out.append((kind, art.get_text().strip().replace("\n", " ")[:40], art))
        except Exception:  # noqa: BLE001
            pass

    for t in fig.texts:
        add("texto", t)
    for ax in fig.axes:
        if not ax.axison:  # axis("off"): only artists drawn on it (tables, annotations, legend) matter
            for art in ax.texts:
                add("anotación", art)
            for tb in ax.tables:
                for cell in tb.get_celld().values():
                    add("celda", cell.get_text())
            continue
        for k, art in (("título", ax.title), ("título", ax._left_title), ("título", ax._right_title),
                       ("etiqueta x", ax.xaxis.label), ("etiqueta y", ax.yaxis.label)):
            add(k, art)
        for art in ax.get_xticklabels():
            add("tick x", art)
        for art in ax.get_yticklabels():
            add("tick y", art)
        for art in ax.texts:
            add("anotación", art)
        leg = ax.get_legend()
        if leg:
            for art in leg.get_texts():
                add("leyenda", art)
            if leg.get_title():
                add("leyenda", leg.get_title())
        for tb in ax.tables:
            for cell in tb.get_celld().values():
                add("celda", cell.get_text())
    return out


def _box(art, r):
    try:
        return art.get_window_extent(r)
    except Exception:  # noqa: BLE001
        return None


def _inter(a, b) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0.0


def audit(fig, strict: bool = False) -> list[str]:
    """Problems of a drawn figure, in plain language (empty list = nothing found). `strict` also reports text that
    leaves the canvas (harmless when saving with bbox_inches='tight', which save_fig does)."""
    r = _renderer(fig)
    W, H = fig.canvas.get_width_height()
    issues: list[str] = []
    items = []
    for kind, label, art in _texts(fig):
        bb = _box(art, r)
        if bb is not None and bb.width > 0.5 and bb.height > 0.5:
            items.append((kind, label, bb, art))
    for kind, label, bb, _ in items if strict else []:
        if bb.x0 < -2 or bb.y0 < -2 or bb.x1 > W + 2 or bb.y1 > H + 2:
            issues.append(f"{kind} «{label}» se sale de la figura")
    seen: set[tuple[str, str]] = set()
    for (k1, l1, b1, a1), (k2, l2, b2, a2) in itertools.combinations(items, 2):
        if k1 == "celda" and k2 == "celda":
            continue
        ov = _inter(b1, b2)
        if ov <= 0:
            continue
        small = min(b1.width * b1.height, b2.width * b2.height) or 1
        if ov / small < 0.12:
            continue
        key = (k1, k2) if k1 == k2 else tuple(sorted((k1, k2)))  # one line per kind of clash
        if key in seen:
            continue
        seen.add(key)
        issues.append(f"{k1} «{l1}» se solapa con {k2} «{l2}»")
    for ax in fig.axes:  # decorations so big that the plot itself shrinks to a sliver
        if ax.axison and ax.get_label() != "<colorbar>":
            ab = _box(ax, r)
            if ab is not None and (ab.width < 0.3 * W or ab.height < 0.22 * H) and len(fig.axes) == 1:
                issues.append("el área del gráfico queda minúscula (etiquetas o títulos demasiado largos para el tamaño)")
                break
    # table cells that do not fit in their cell
    for ax in fig.axes:
        for tb in ax.tables:
            for (row, col), cell in tb.get_celld().items():
                tb_, cb_ = _box(cell.get_text(), r), _box(cell, r)
                if tb_ is not None and cb_ is not None and cell.get_text().get_text().strip() and tb_.width > cb_.width * 1.02:
                    issues.append(f"el texto de la celda ({row},{col}) «{cell.get_text().get_text()[:25]}» no cabe")
                    break
    # legend over data
    for ax in fig.axes:
        leg = ax.get_legend()
        if not leg or not leg.get_visible():
            continue
        lb = _box(leg, r)
        if lb is None:
            continue
        covered = 0
        total = 0
        for line in ax.get_lines():
            if not line.get_visible() or line.get_label().startswith("_") and len(line.get_xdata()) < 3:
                continue
            xy = ax.transData.transform(list(zip(line.get_xdata(orig=False), line.get_ydata(orig=False))))
            for x, y in xy:
                total += 1
                if lb.x0 <= x <= lb.x1 and lb.y0 <= y <= lb.y1:
                    covered += 1
        for p in ax.patches:
            pb = _box(p, r)
            if pb is not None and pb.width > 1:
                total += 1
                if _inter(pb, lb) > 0.15 * pb.width * pb.height:
                    covered += 1
        if total and covered / total > 0.02 and covered >= 2:
            issues.append("la leyenda tapa datos del gráfico")
            break
    # ticks that overlap each other are reported once per axis
    return issues


def _numeric(labels) -> bool:
    ok = 0
    for x in labels:
        try:
            float(x.replace("−", "-").replace(",", "."))
            ok += 1
        except ValueError:
            pass
    return ok >= max(1, len([x for x in labels if x])) * 0.8


def _categorical(ticks) -> bool:
    labs = [t.get_text() for t in ticks if t.get_text().strip()]
    return bool(labs) and sum(any(c.isalpha() for c in x) for x in labs) > len(labs) * 0.6


def _tick_overlap(ax, which: str, r) -> bool:
    labs = [t for t in (ax.get_xticklabels() if which == "x" else ax.get_yticklabels()) if t.get_visible() and t.get_text().strip()]
    boxes = [b for b in (_box(t, r) for t in labs) if b is not None]
    boxes.sort(key=lambda b: (b.x0, b.y0))
    return any(_inter(a, b) > 0.05 * min(a.width * a.height, b.width * b.height) for a, b in zip(boxes, boxes[1:]))


def autofix(fig, rounds: int = 6) -> list[str]:
    """Try the usual cures until audit() finds nothing. Returns the problems that remain."""
    fig_w_px = fig.get_figwidth() * fig.dpi
    for step in range(rounds):
        issues = audit(fig)
        if not issues:
            return []
        r = _renderer(fig)
        for ax in fig.axes:
            # 1. wrap long titles and axis labels to the width of the axes
            wb = _box(ax, r)
            width = max(wb.width if wb is not None else 0, 0.8 * fig_w_px)  # the axes may be squeezed by the very labels being fixed
            for art in (ax.title, ax._left_title, ax._right_title, ax.xaxis.label, ax.yaxis.label):
                txt = art.get_text()
                if txt and "\n" not in txt:
                    bb = _box(art, r)
                    if bb is not None and bb.width > width * (0.95 if art is not ax.yaxis.label else 1.6):
                        n = max(12, int(len(txt) * width * 0.9 / bb.width))
                        art.set_text(wrap(txt, n))
            # 1b. long category names: freeze them and wrap each over several lines
            if step == 0:
                labs = [t.get_text() for t in ax.get_xticklabels()]
                if labs and max(len(x) for x in labs) > 14 and not _numeric(labs):
                    n = max(1, len([x for x in labs if x]))
                    per = max(8, int(width / max(1, n) / (ax.xaxis.get_ticklabels()[0].get_fontsize() * 0.55 * fig.dpi / 72)))
                    ax.set_xticks(ax.get_xticks())
                    ax.set_xticklabels([wrap(x, per) for x in labs])
            # 2. tick labels: rotate, then shrink, then show fewer
            if _tick_overlap(ax, "x", r):
                rot = [0, 30, 45, 90][min(3, step)]
                if step <= 3:
                    for t in ax.get_xticklabels():
                        t.set_rotation(rot)
                        t.set_ha("right" if rot not in (0, 90) else "center")
                if step >= 3 and not _categorical(ax.get_xticklabels()):  # never drop the names of categories
                    labs = ax.get_xticklabels()
                    k = 2 if step == 3 else 3
                    for i, t in enumerate(labs):
                        t.set_visible(i % k == 0)
                if step >= 2:
                    ax.tick_params(axis="x", labelsize=max(6, ax.xaxis.get_ticklabels()[0].get_fontsize() - 1))
            if _tick_overlap(ax, "y", r):
                ax.tick_params(axis="y", labelsize=max(6, ax.yaxis.get_ticklabels()[0].get_fontsize() - 1))
            # 3. legend: below the plot (outside) with several columns
            leg = ax.get_legend()
            if leg is not None and step >= 1 and any("leyenda" in i for i in issues):
                handles, labels = ax.get_legend_handles_labels()
                if handles:
                    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.12 - 0.04 * (step - 1)),
                              ncol=min(len(labels), 4), fontsize=max(6, leg.get_texts()[0].get_fontsize() - (1 if step > 3 else 0)))
            # 4. annotations that clash: smaller font
            if step >= 2:
                for a in ax.texts:
                    a.set_fontsize(max(6, a.get_fontsize() - 1))
        if step >= 1 and any("minúscula" in i for i in issues):  # more height gives the plot room (width is the page's)
            w0, h0 = fig.get_size_inches()
            if h0 < getattr(fig, "_sah_h0", h0) * 2.0:
                fig._sah_h0 = getattr(fig, "_sah_h0", h0)
                fig.set_size_inches(w0, h0 * 1.25)
        fig.canvas.draw()
    return audit(fig)


def check_image(path: str | Path, border: int = 2) -> list[str]:
    """Raster check: content touching the very edge of the picture means it was cut off."""
    from PIL import Image

    problems = []
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w < 500:
            problems.append(f"{w}px de ancho: se verá borrosa en un documento")
        px = im.load()

        def ink(x, y):
            p = px[x, y]
            return sum(p) < 3 * 235

        edges = {"izquierdo": [(x, y) for x in range(border) for y in range(0, h, max(1, h // 80))],
                 "derecho": [(w - 1 - x, y) for x in range(border) for y in range(0, h, max(1, h // 80))],
                 "superior": [(x, y) for y in range(border) for x in range(0, w, max(1, w // 80))],
                 "inferior": [(x, h - 1 - y) for y in range(border) for x in range(0, w, max(1, w // 80))]}
        for name, pts in edges.items():
            if sum(1 for x, y in pts if ink(x, y)) > max(3, len(pts) * 0.05):
                problems.append(f"hay contenido pegado al borde {name} (¿recortado?)")
    return problems


def save_fig(fig, path: str | Path, dpi: int = 200, fix: bool = True, close: bool = True, quiet: bool = False) -> list[str]:
    """Check, fix and save a figure. Returns the problems left (empty = good) and prints a verdict."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    issues = autofix(fig) if fix else audit(fig)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.08)
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        issues = issues + check_image(path)
    if close:
        plt.close(fig)
    if not quiet:
        if issues:
            print(f"[sah_plot] PROBLEMAS en {path}: " + "; ".join(dict.fromkeys(issues)) +
                  " → rehaz la figura (etiquetas más cortas, más tamaño, barh, menos categorías) y guarda de nuevo.")
        else:
            print(f"[sah_plot] OK {path}")
    return issues


# ---------------------------------------------------------------------------------------------------- tables

def _text_w(r, s: str, size: float) -> float:
    w, _, _ = r.get_text_width_height_descent(s, FontProperties(size=size), ismath=False)
    return float(w)


def table_image(df, path: str | Path, title: str | None = None, w_cm: float = 15.0, max_rows: int = 28,
                font: float = 8.5, dpi: int = 220, index: bool = False, number_format: str = "{:,.2f}") -> list[str]:
    """Draw a DataFrame as a picture whose columns are measured so no text leaves its cell. Long tables are split
    into pages (path_1.png, path_2.png …). Returns the files written."""
    import pandas as pd

    data = df.reset_index() if index else df.copy()
    cols = [str(c) for c in data.columns]
    def fmt(v):
        if isinstance(v, float):
            return "" if pd.isna(v) else (f"{v:.0f}" if float(v).is_integer() and abs(v) < 1e9 else number_format.format(v))
        return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)
    rows = [[fmt(v) for v in rec] for rec in data.itertuples(index=False, name=None)]
    fig0 = plt.figure(figsize=(w_cm / 2.54, 2))
    r = _renderer(fig0)
    avail = (w_cm / 2.54) * fig0.dpi * 0.98
    size = font
    wrap_at = [40] * len(cols)
    while True:  # shrink the font and wrap the widest columns until the table fits the width
        widths = []
        for j, c in enumerate(cols):
            cells = [c] + [row[j] for row in rows]
            longest = max(_text_w(r, line, size) for x in cells for line in wrap(x, wrap_at[j]).split("\n"))
            widths.append(longest + 2 * size * 0.9)
        if sum(widths) <= avail:
            break
        j = max(range(len(cols)), key=lambda k: widths[k])
        if wrap_at[j] > 10:
            wrap_at[j] = max(10, int(wrap_at[j] * 0.7))
        elif size > 6.0:
            size -= 0.5
            wrap_at = [40] * len(cols)
        else:
            break
    plt.close(fig0)
    scale = min(1.0, avail / sum(widths))
    widths = [w * scale for w in widths]
    written = []
    pages = [rows[i:i + max_rows] for i in range(0, max(1, len(rows)), max_rows)] or [[]]
    path = Path(path)
    for n, chunk in enumerate(pages, 1):
        lines = [max(wrap(x, wrap_at[j]).count("\n") + 1 for j, x in enumerate(row)) if row else 1 for row in chunk]
        head_lines = max(wrap(c, wrap_at[j]).count("\n") + 1 for j, c in enumerate(cols))
        row_h = size * 1.7 / 72
        fig_h = (head_lines * row_h + sum(l * row_h for l in lines) + (0.5 if title else 0.15))
        fig = plt.figure(figsize=(sum(widths) / fig0.dpi, max(0.8, fig_h)), constrained_layout=False)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        tbl = ax.table(cellText=[[wrap(x, wrap_at[j]) for j, x in enumerate(row)] for row in chunk],
                       colLabels=[wrap(c, wrap_at[j]) for j, c in enumerate(cols)],
                       colWidths=[w / sum(widths) for w in widths], cellLoc="left", colLoc="left", loc="center",
                       bbox=[0, 0, 1, 1 - (0.5 / fig.get_figheight() if title else 0.0)])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(size)
        total_lines = head_lines + sum(lines)
        for (i, j), cell in tbl.get_celld().items():
            nl = head_lines if i == 0 else lines[i - 1]
            cell.set_height(nl / total_lines * (1 - (0.5 / fig.get_figheight() if title else 0.0)))
            cell.set_edgecolor("#D0D4DA")
            cell.set_linewidth(0.5)
            cell.PAD = 0.04
            if i == 0:
                cell.set_facecolor("#1F3A5F")
                cell.get_text().set_color("white")
                cell.get_text().set_fontweight("bold")
            elif i % 2 == 0:
                cell.set_facecolor("#F3F5F8")
        if title:
            fig.text(0.0, 1 - 0.18 / fig.get_figheight(), title + (f"  ({n}/{len(pages)})" if len(pages) > 1 else ""),
                     fontsize=size + 1.5, fontweight="bold", va="top", ha="left")
        out = path if len(pages) == 1 else path.with_name(f"{path.stem}_{n}{path.suffix}")
        issues = audit(fig)
        fig.savefig(out, dpi=dpi, bbox_inches="tight", pad_inches=0.06)
        plt.close(fig)
        written.append(str(out))
        print(f"[sah_plot] {'PROBLEMAS en' if issues else 'OK'} {out}" + (": " + "; ".join(issues) if issues else ""))
    return written
