"""sah_tex — LaTeX tables and figures that fit the page (Spark AI Hub helper).

    from sah_tex import PREAMBLE, df_to_latex, figure, esc
    open("tabla1.tex", "w").write(df_to_latex(df, "Encuestas de octubre", "tab:oct"))      # then \\input{tabla1}
    open("fig1.tex", "w").write(figure("figs/evolucion.png", "Evolución del voto", "fig:evol"))

Put PREAMBLE's packages in your preamble once. Tables wider than the text are wrapped (tabularx), shrunk a little, or turned
into a landscape page; tables longer than a page become longtable; text is escaped. Figures are scaled to the line width and
never taller than 80 % of the page. Always compile with `latexmk -g -pdf` and then run `python .sah/sah_check.py <file>.pdf`.
"""
from __future__ import annotations

import re

PREAMBLE = r"""\usepackage{booktabs}
\usepackage{array}
\usepackage{tabularx}
\usepackage{longtable}
\usepackage{graphicx}
\usepackage{float}
\usepackage{adjustbox}
\usepackage{pdflscape}
\usepackage{caption}
\usepackage{microtype}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\setlength{\emergencystretch}{3em}
"""

_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def esc(text) -> str:
    """Escape LaTeX special characters in plain text."""
    return "".join(_SPECIAL.get(c, c) for c in str(text))


def _num(s: str) -> bool:
    return bool(re.fullmatch(r"[-+−]?\d[\d.,\s]*%?", s.strip()))


def df_to_latex(df, caption: str, label: str, decimals: int = 2, index: bool = False, font: str | None = None,
                landscape: str = "auto", max_rows_float: int = 32, source: str | None = None) -> str:
    """A table that fits the page. `landscape`: "auto" (wide tables get their own landscape page), "never" or "always"."""
    import pandas as pd

    data = df.reset_index() if index else df
    cols = [str(c) for c in data.columns]

    def cell(v):
        if isinstance(v, float):
            return "" if pd.isna(v) else (f"{v:.0f}" if v.is_integer() and abs(v) < 1e9 else f"{v:.{decimals}f}".replace(".", ","))
        return "" if v is None else esc(v)

    rows = [[cell(v) for v in rec] for rec in data.itertuples(index=False, name=None)]
    maxlen = [max([len(re.sub(r"\\\w+\{\}|\\.", "x", cols[j]))] + [len(re.sub(r"\\\w+\{\}|\\.", "x", r[j])) for r in rows] or [1])
              for j in range(len(cols))]
    numeric = [all(_num(r[j]) or r[j] == "" for r in rows) for j in range(len(cols))]
    total = sum(min(m, 40) for m in maxlen) + 2 * len(cols)
    long_text = [j for j in range(len(cols)) if not numeric[j] and maxlen[j] > 14]
    wide = total > 92  # characters that fit a 15 cm line at 9-10 pt
    size = font or ("\\small" if total <= 92 else "\\footnotesize" if total <= 120 else "\\scriptsize")
    use_landscape = landscape == "always" or (landscape == "auto" and total > 150 and len(cols) >= 8)
    if use_landscape:
        size = "\\footnotesize" if total <= 190 else "\\scriptsize"
    spec = []
    for j in range(len(cols)):
        if numeric[j]:
            spec.append("r")
        elif long_text and j in long_text and (wide or use_landscape):
            spec.append("Y")
        else:
            spec.append("l")
    head = " & ".join(f"\\textbf{{{esc(c)}}}" for c in cols) + r" \\"
    body = "\n".join(" & ".join(r) + r" \\" for r in rows)
    cap = f"\\caption{{{esc(caption)}}}\\label{{{label}}}"
    src = f"\n{{\\footnotesize\\textit{{Fuente: {esc(source)}}}}}" if source else ""
    long = len(rows) > max_rows_float
    width = "\\linewidth" if not use_landscape else "\\linewidth"
    if long:
        # longtable cannot sit in a float: caption inside, repeated header, no tabularx (use p-columns with Y widths)
        ps = []
        share = max(1, len(long_text))
        for j, sp in enumerate(spec):
            ps.append(sp if sp != "Y" else f"p{{{max(2.5, 8.5 / share):.1f}cm}}")
        out = (f"{{{size}\n\\begin{{longtable}}{{{''.join(ps)}}}\n{cap}\\\\\n\\toprule\n{head}\n\\midrule\n\\endfirsthead\n"
               f"\\toprule\n{head}\n\\midrule\n\\endhead\n\\bottomrule\n\\endfoot\n{body}\n\\end{{longtable}}}}{src}\n")
    elif "Y" in spec:
        out = (f"\\begin{{table}}[htbp]\n\\centering\n{size}\n{cap}\n\\begin{{tabularx}}{{{width}}}{{{''.join(spec)}}}\n"
               f"\\toprule\n{head}\n\\midrule\n{body}\n\\bottomrule\n\\end{{tabularx}}{src}\n\\end{{table}}\n")
    else:
        out = (f"\\begin{{table}}[htbp]\n\\centering\n{size}\n{cap}\n\\begin{{adjustbox}}{{max width=\\linewidth}}\n"
               f"\\begin{{tabular}}{{{''.join(spec)}}}\n\\toprule\n{head}\n\\midrule\n{body}\n\\bottomrule\n\\end{{tabular}}\n"
               f"\\end{{adjustbox}}{src}\n\\end{{table}}\n")
    if use_landscape:
        out = "\\begin{landscape}\n" + out + "\\end{landscape}\n"
    return out


def figure(path: str, caption: str, label: str, width: float = 0.95, placement: str = "htbp", source: str | None = None,
           fallback: str | None = None) -> str:
    """A figure scaled to the line width and at most 80 % of the page height. With `fallback`, a missing image file
    prints that text instead of breaking the compilation (e.g. a photo that could not be downloaded)."""
    inc = f"\\includegraphics[width={width}\\linewidth,height=0.8\\textheight,keepaspectratio]{{{path}}}"
    if fallback is not None:
        inc = f"\\IfFileExists{{{path}}}{{{inc}}}{{\\fbox{{\\parbox{{0.8\\linewidth}}{{\\centering {esc(fallback)}}}}}}}"
    src = f"\n{{\\footnotesize\\textit{{Fuente: {esc(source)}}}}}" if source else ""
    return f"\\begin{{figure}}[{placement}]\n\\centering\n{inc}\n\\caption{{{esc(caption)}}}\\label{{{label}}}{src}\n\\end{{figure}}\n"
