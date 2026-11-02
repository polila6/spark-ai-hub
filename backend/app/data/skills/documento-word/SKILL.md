---
name: documento-word
description: Úsala para generar o editar documentos Word (.docx) con Python (python-docx) o pandoc: informes, actas, cartas, memorias con títulos, tablas, figuras, pies, numeración de páginas e índice; y para convertir Word a PDF o Markdown.
license: MIT
---
# Documentos Word (.docx)

Instalados: `python-docx` 1.2, `pandoc` 2.17, LibreOffice (`soffice --headless`), Node `docx` (alternativa JS).

**Cuál usar**
- Texto largo con estructura (informe, memoria, apuntes) y pocas exigencias de maquetación → escribe Markdown y
  conviértelo con pandoc (rápido, índice y citas automáticas).
- Formato preciso (tablas con estilo, encabezados/pies, márgenes, campos) o rellenar una plantilla → python-docx.
- PDF final → genera el .docx y conviértelo: `soffice --headless --convert-to pdf informe.docx`.

## Vía rápida: Markdown → Word con pandoc

```bash
pandoc informe.md -o informe.docx --toc --number-sections -M lang=es-ES \
       --resource-path=.:figuras            # imágenes: ![Pie de figura](figuras/f1.png){width=80%}
# con estilo propio (fuentes, colores, márgenes): crea la plantilla una vez y edítala en Word/LibreOffice
pandoc -o plantilla.docx --print-default-data-file reference.docx
pandoc informe.md -o informe.docx --reference-doc=plantilla.docx
# con bibliografía: --citeproc --bibliography=refs.bib   (ver skill citas-bibtex)
```
Tablas en Markdown con formato pipe (`| a | b |`). Las ecuaciones `$...$` de LaTeX se convierten a ecuaciones
nativas de Word.

## python-docx: plantilla de informe

Plantilla completa y probada en `plantilla_informe.py` (read_skill_file). Lo esencial:

```python
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt, RGBColor

doc = Document()
st = doc.styles["Normal"]
st.font.name, st.font.size = "Calibri", Pt(11)
sec = doc.sections[0]
sec.left_margin = sec.right_margin = Cm(2.5)

doc.add_heading("Informe de resultados", level=0)          # título
doc.add_heading("1. Introducción", level=1)
p = doc.add_paragraph("Texto normal con ")
p.add_run("negrita").bold = True
p.add_run(" y ")
p.add_run("cursiva").italic = True
doc.add_paragraph("Primer punto", style="List Bullet")
doc.add_paragraph("Paso uno", style="List Number")

datos = [("Muestra", "Valor", "Unidad"), ("A", "12,3", "MPa"), ("B", "10,1", "MPa")]
t = doc.add_table(rows=0, cols=3)
t.style = "Table Grid"                                      # otros: "Light Grid Accent 1", "Medium Shading 1 Accent 1"
t.alignment = WD_TABLE_ALIGNMENT.CENTER
for i, fila in enumerate(datos):
    celdas = t.add_row().cells
    for c, v in zip(celdas, fila):
        c.text = v
        if i == 0:
            c.paragraphs[0].runs[0].bold = True
doc.add_paragraph("Tabla 1. Resultados de las muestras.", style="Caption")

doc.add_picture("figuras/grafico.png", width=Cm(15))
doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_paragraph("Figura 1. Evolución temporal.", style="Caption")

doc.add_page_break()
doc.save("informe.docx")
```

## Tareas habituales

- **Número de página / índice (campos)**: python-docx no tiene API para campos; la plantilla incluye
  `add_field(paragraph, "PAGE")` y `add_field(p, 'TOC \\o "1-3" \\h \\z \\u')`. Word actualiza el índice al
  abrir (o con F9); al convertir a PDF con LibreOffice el índice puede salir vacío: si el PDF es el entregable,
  usa pandoc `--toc` o genera el índice a mano.
- **Orientación horizontal** de una sección: `s = doc.add_section(); s.orientation = WD_ORIENT.LANDSCAPE;
  s.page_width, s.page_height = s.page_height, s.page_width`.
- **Encabezado/pie**: `sec.header.paragraphs[0].text = "Proyecto X"`; `sec.footer.paragraphs[0]`.
- **Color de texto**: `run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x78)`.
- **Sombrear una celda**: requiere XML (`w:shd`), ver `shade(cell, "D9E2F3")` en la plantilla.
- **Salto de línea dentro de un párrafo**: `run.add_break()`; salto de página: `run.add_break(WD_BREAK.PAGE)`.

## Editar un .docx existente

```python
doc = Document("entrada.docx")
for p in doc.paragraphs:
    if "{{FECHA}}" in p.text:
        for r in p.runs:                     # reemplaza run a run para conservar el formato
            r.text = r.text.replace("{{FECHA}}", "3 de noviembre de 2026")
for t in doc.tables:                         # el texto de las tablas NO está en doc.paragraphs
    for row in t.rows:
        for c in row.cells:
            ...
doc.save("salida.docx")                      # guarda con otro nombre, nunca sobre el original
```
Si el marcador quedó partido en varios runs (pasa a menudo), `p.text` lo contiene pero ningún run: une el texto
en el primer run y vacía los demás (pierde el formato mixto de ese párrafo).

## Leer / convertir

- Texto y tablas: `pandoc entrada.docx -t markdown -o entrada.md` (conserva títulos, listas y tablas).
- Word → PDF: `soffice --headless --convert-to pdf --outdir salida entrada.docx`.
- .doc antiguo → .docx: `soffice --headless --convert-to docx archivo.doc`.

## Errores frecuentes

- `KeyError: "no style with name 'X'"`: el estilo no existe en la plantilla por defecto (los nombres son en
  inglés: "Heading 1", "List Bullet", "Caption", "Table Grid", "Quote"). Con una plantilla en español los
  nombres visibles cambian, pero python-docx usa el nombre interno en inglés.
- Imágenes: PNG/JPG/GIF/BMP/TIFF; SVG y PDF no se pueden insertar (conviértelos a PNG a 200-300 ppp).
- Los cambios de `doc.styles["Normal"]` no afectan a títulos (tienen su propio estilo "Heading N").
