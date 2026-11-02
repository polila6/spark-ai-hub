"""16:9 PowerPoint template with python-pptx: cover, bullets, image, table, native chart, notes, closing slide.

Run: python plantilla_pptx.py  ->  presentacion.pptx
Check: soffice --headless --convert-to pdf presentacion.pptx && pdftoppm -png -r 40 presentacion.pdf prev
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

ACCENT = RGBColor(0x1F, 0x4E, 0x78)

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
W, H = prs.slide_width, prs.slide_height
MARGIN = Inches(0.6)


def new_slide(layout: int):
    """Add a slide and widen its placeholders: the default layouts are drawn for 4:3 (10 in wide)."""
    slide = prs.slides.add_slide(prs.slide_layouts[layout])
    for ph in slide.placeholders:
        top, height = ph.top, ph.height  # inherited from the layout; set all four or top/height become 0
        ph.left, ph.top, ph.width, ph.height = MARGIN, top, W - 2 * MARGIN, height
    return slide


def bullets(slide, items, size=22):
    """Fill the body placeholder with (text, level) items."""
    tf = slide.placeholders[1].text_frame
    tf.clear()
    for i, (text, level) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text, p.level = text, level
        for r in p.runs:
            r.font.size = Pt(size - 4 * level)


# 1. Cover
s = new_slide(0)
s.shapes.title.text = "Título de la presentación"
s.placeholders[1].text = "Nombre Apellido · Grupo de investigación · Universidad"

# 2. Bullets + speaker notes
s = new_slide(1)
s.shapes.title.text = "El problema cuesta 3 h por ensayo"
bullets(s, [("Contexto en una línea", 0), ("Dato que lo demuestra", 1),
            ("Qué falta en las soluciones actuales", 0), ("Nuestro objetivo", 0)])
s.notes_slide.notes_text_frame.text = "Empezar con la anécdota del laboratorio (30 s)."

# 3. Image generated with matplotlib (big fonts for slides)
plt.rcParams.update({"font.size": 16})
fig, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
ax.plot(range(1, 13), [5, 6, 7, 7, 8, 9, 9, 10, 11, 12, 12, 13], marker="o", lw=2.5)
ax.set_xlabel("Mes")
ax.set_ylabel("Producción (t)")
ax.spines[["top", "right"]].set_visible(False)
fig.savefig("figura.png", dpi=200)
plt.close(fig)
s = new_slide(5)
s.shapes.title.text = "La producción se duplica en un año"
pic_w = Inches(9)
s.shapes.add_picture("figura.png", int((W - pic_w) / 2), Inches(1.7), width=pic_w)

# 4. Table
s = new_slide(5)
s.shapes.title.text = "El modelo propuesto reduce el error"
rows = [("Modelo", "RMSE", "R²"), ("Referencia", "0,52", "0,71"), ("Propuesto", "0,34", "0,86")]
tbl = s.shapes.add_table(len(rows), 3, Inches(2.5), Inches(2), Inches(8.3), Inches(2.4)).table
for i, row in enumerate(rows):
    for j, value in enumerate(row):
        c = tbl.cell(i, j)
        c.text = value
        para = c.text_frame.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        para.runs[0].font.size = Pt(20)
        if i == 0:
            c.fill.solid()
            c.fill.fore_color.rgb = ACCENT
            para.runs[0].font.bold = True
            para.runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

# 5. Native (editable) chart
s = new_slide(5)
s.shapes.title.text = "2026 supera a 2025 en todos los trimestres"
data = CategoryChartData()
data.categories = ["T1", "T2", "T3", "T4"]
data.add_series("2025", (12.0, 15.5, 11.0, 17.2))
data.add_series("2026", (13.1, 16.0, 14.2, 19.0))
chart = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1.5), Inches(1.6), Inches(10.3), Inches(5.4),
                           data).chart
chart.has_legend = True
chart.legend.position = XL_LEGEND_POSITION.BOTTOM
chart.legend.include_in_layout = False
chart.value_axis.has_major_gridlines = False
chart.category_axis.tick_labels.font.size = Pt(16)
chart.value_axis.tick_labels.font.size = Pt(14)

# 6. Closing
s = new_slide(6)
box = s.shapes.add_textbox(0, Inches(3), W, Inches(1.5))
p = box.text_frame.paragraphs[0]
p.text, p.alignment = "¡Gracias! ¿Preguntas?", PP_ALIGN.CENTER
p.runs[0].font.size, p.runs[0].font.bold, p.runs[0].font.color.rgb = Pt(40), True, ACCENT

prs.save("presentacion.pptx")
print("Creado presentacion.pptx con", len(prs.slides), "diapositivas")
