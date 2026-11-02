---
name: presentacion-pptx
description: Úsala para crear presentaciones PowerPoint (.pptx) editables con python-pptx: diapositivas con títulos, viñetas, imágenes, tablas, gráficos nativos y notas del orador; exportarlas a PDF y revisar su aspecto.
license: MIT
---
# Presentaciones PowerPoint (.pptx)

Instalados: `python-pptx` 1.0, Node `pptxgenjs` 3.12 (alternativa), LibreOffice para PDF/miniaturas,
`pdftoppm` para revisar páginas como imágenes. Para presentaciones con muchas ecuaciones usa presentacion-beamer.

## Pasos

1. Guion antes que diseño: un mensaje por diapositiva, título = conclusión ("Las ventas crecen un 12 %"),
   ≤ 6 viñetas cortas, ~1 diapositiva por minuto de exposición.
2. Genera las figuras antes (matplotlib, PNG a 200 ppp con texto grande, ver graficos-publicacion).
3. Construye el .pptx partiendo de `plantilla_pptx.py` (read_skill_file): formato 16:9, portada, viñetas,
   imagen, tabla, gráfico nativo y notas del orador.
4. Revisa el resultado: `soffice --headless --convert-to pdf presentacion.pptx` y
   `pdftoppm -png -r 40 presentacion.pdf prev` → mira algunas PNG para detectar texto desbordado o solapes.
5. Si el usuario aporta su plantilla corporativa (.pptx/.potx), ábrela con `Presentation("plantilla.pptx")` y usa
   sus diseños (`prs.slide_layouts`), en vez de dar formato a mano. Para .potx: conviértela antes
   (`soffice --headless --convert-to pptx plantilla.potx`).

## Lo esencial de python-pptx

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches, Pt

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)    # 16:9 (por defecto es 4:3)
# diseños de la plantilla por defecto: 0 título, 1 título y contenido, 5 solo título, 6 en blanco

s = prs.slides.add_slide(prs.slide_layouts[1])
for ph in s.placeholders:                                 # ver "16:9" en fallos frecuentes
    top, height = ph.top, ph.height
    ph.left, ph.top, ph.width, ph.height = Inches(0.6), top, prs.slide_width - Inches(1.2), height
s.shapes.title.text = "Las ventas crecen un 12 %"
tf = s.placeholders[1].text_frame
tf.text = "Primer punto"                                  # primer párrafo
p = tf.add_paragraph(); p.text = "Detalle"; p.level = 1   # sangría
s.notes_slide.notes_text_frame.text = "Lo que diré en esta diapositiva."

s = prs.slides.add_slide(prs.slide_layouts[5])
s.shapes.title.text = "Evolución mensual"
s.shapes.add_picture("figura.png", Inches(1), Inches(1.6), width=Inches(7))  # mantiene proporción

datos = CategoryChartData()
datos.categories = ["Ene", "Feb", "Mar"]
datos.add_series("2025", (12.0, 15.5, 11.0))
datos.add_series("2026", (13.1, 16.0, 14.2))
graf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(8.3), Inches(1.6), Inches(4.6), Inches(4),
                          datos).chart
graf.has_legend = True
graf.legend.position = XL_LEGEND_POSITION.BOTTOM
graf.legend.include_in_layout = False

prs.save("presentacion.pptx")
```

Otros tipos de gráfico: `XL_CHART_TYPE.LINE_MARKERS`, `BAR_CLUSTERED`, `PIE`, `XY_SCATTER` (con `XyChartData`).
Tabla: `t = s.shapes.add_table(filas, cols, x, y, ancho, alto).table; t.cell(0, 0).text = "..."`.
Tamaño de letra de un párrafo: `for r in p.runs: r.font.size = Pt(20)`.

## Fallos frecuentes

- **Texto desbordado**: python-pptx no reduce la letra automáticamente. Limita las viñetas, baja el tamaño
  (`Pt(18)`) o divide la diapositiva. `tf.word_wrap = True` en cuadros de texto propios.
- `s.placeholders[1]` no existe en diseños "solo título" o "en blanco" → `KeyError`; usa `s.shapes.add_textbox`.
- Las imágenes SVG no se pueden insertar: usa PNG/JPG.
- **16:9 con la plantilla por defecto**: sus diseños están dibujados para 4:3 (10 in de ancho) y al cambiar
  `slide_width` los marcadores se quedan a la izquierda. Ensánchalos en cada diapositiva:
  asigna a cada marcador `left`, `top`, `width` y `height` a la vez (si solo cambias `left`/`width`, `top` y
  `height` pasan a 0), como hace `new_slide()` en la plantilla, y calcula la posición de tus formas a partir de
  `prs.slide_width`.
- No uses fuentes raras: Calibri/Arial están en el equipo del usuario. En el PDF generado aquí Arial se sustituye
  por Liberation Sans (mismas métricas), pero Calibri no tiene equivalente instalado y el texto puede ocupar más.
- Las tablas de python-pptx tienen el estilo por defecto del tema; para colores de cabecera:
  `c.fill.solid(); c.fill.fore_color.rgb = RGBColor(0x1F, 0x4E, 0x78)` (`from pptx.dml.color import RGBColor`).
