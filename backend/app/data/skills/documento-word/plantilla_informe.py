"""Word report template with python-docx: cover, TOC field, headings, table, figure, captions, page numbers.

Run: python plantilla_informe.py  ->  informe.docx   (PDF: soffice --headless --convert-to pdf informe.docx)
"""
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def add_field(paragraph, instruction: str) -> None:
    """Insert a Word field (PAGE, NUMPAGES, TOC ...) into a paragraph; Word computes it when opened."""
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, instruction), ("separate", None), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def shade(cell, hex_fill: str) -> None:
    """Background colour of a table cell (e.g. 'D9E2F3')."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def set_language(doc, lang: str = "es-ES") -> None:
    """Proofing language of the Normal style (spell checking in Word)."""
    rpr = doc.styles["Normal"].element.get_or_add_rPr()
    el = OxmlElement("w:lang")
    el.set(qn("w:val"), lang)
    rpr.append(el)


# ---- example figure
fig, ax = plt.subplots(figsize=(6, 3), layout="constrained")
ax.plot([1, 2, 3, 4], [10, 12, 9, 14], marker="o")
ax.set_xlabel("Semana")
ax.set_ylabel("Valor (ud.)")
fig.savefig("grafico.png", dpi=200)
plt.close(fig)

doc = Document()
set_language(doc)
normal = doc.styles["Normal"]
normal.font.name, normal.font.size = "Calibri", Pt(11)
sec = doc.sections[0]
sec.top_margin = sec.bottom_margin = Cm(2.5)
sec.left_margin = sec.right_margin = Cm(2.5)

# ---- cover
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = title.add_run("Informe técnico")
r.bold, r.font.size, r.font.color.rgb = True, Pt(26), RGBColor(0x1F, 0x4E, 0x78)
sub = doc.add_paragraph(f"Proyecto de ejemplo · {date.today():%d/%m/%Y}")
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_page_break()

# ---- table of contents (filled by Word on open / F9)
doc.add_heading("Índice", level=1)
add_field(doc.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u')
doc.add_page_break()

# ---- body
doc.add_heading("1. Introducción", level=1)
doc.add_paragraph("Contexto, objetivo y alcance del informe.")
doc.add_heading("2. Resultados", level=1)

rows = [("Muestra", "Media", "DE", "n"), ("A", "12,3", "0,8", "5"), ("B", "10,1", "1,1", "5")]
table = doc.add_table(rows=0, cols=len(rows[0]))
table.style = "Table Grid"
table.alignment = WD_TABLE_ALIGNMENT.CENTER
for i, values in enumerate(rows):
    cells = table.add_row().cells
    for cell, value in zip(cells, values):
        cell.text = value
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if i == 0:
            cell.paragraphs[0].runs[0].bold = True
            shade(cell, "D9E2F3")
doc.add_paragraph("Tabla 1. Resultados por muestra (media, desviación estándar y tamaño).", style="Caption")

doc.add_picture("grafico.png", width=Cm(14))
doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_paragraph("Figura 1. Evolución semanal del valor medido.", style="Caption")

doc.add_heading("3. Conclusiones", level=1)
for text in ("Primera conclusión.", "Segunda conclusión."):
    doc.add_paragraph(text, style="List Number")

# ---- footer: "Página X de Y"
foot = sec.footer.paragraphs[0]
foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
foot.add_run("Página ")
add_field(foot, "PAGE")
foot.add_run(" de ")
add_field(foot, "NUMPAGES")

doc.core_properties.title = "Informe técnico"
doc.save("informe.docx")
print("Creado informe.docx")
