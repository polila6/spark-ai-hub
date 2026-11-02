---
name: excel-con-graficos
description: Úsala para crear o modificar hojas Excel (.xlsx) profesionales con Python: tablas con formato, fórmulas, gráficos nativos de Excel, formato condicional, varias hojas, y para leer Excel con pandas.
license: MIT
---
# Excel con formato y gráficos (openpyxl / xlsxwriter / pandas)

Instalados: `openpyxl` 3.1 (leer y escribir, modificar archivos existentes), `xlsxwriter` 3.x (solo escribir,
muy completo en gráficos y formatos), `pandas`. LibreOffice headless para recalcular o convertir a PDF.

**Cuál usar**: archivo nuevo con gráficos y formatos → xlsxwriter (plantilla `ejemplo_xlsxwriter.py`).
Modificar un Excel existente conservando su contenido → openpyxl (`load_workbook`). Solo volcar tablas → pandas.

## Pasos

1. Diseña las hojas: `Datos` (tabla plana, una fila por registro, encabezados en la fila 1), `Resumen`
   (fórmulas o tablas dinámicas calculadas en pandas), `Gráficos`.
2. Escribe los datos y el formato; usa fórmulas de Excel cuando el usuario quiera un libro "vivo"
   (en inglés y con coma como separador: `=SUM(B2:B10)`, `=AVERAGE(...)`, `=IF(A2>0,"Sí","No")`), aunque Excel las
   muestre luego en español.
3. Añade gráficos nativos (se pueden editar en Excel), no imágenes, salvo que se pida una figura concreta.
4. Guarda y verifica reabriendo con openpyxl o pandas. Para entregar también en PDF:
   `soffice --headless --convert-to pdf libro.xlsx`.

## openpyxl: formatos, fórmulas y gráfico

```python
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, ScatterChart, Reference, Series
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Datos"
ws.append(["Mes", "Ingresos", "Gastos", "Margen"])
for i, (m, ing, gas) in enumerate([("Ene", 1200, 800), ("Feb", 1500, 900), ("Mar", 1100, 950)], start=2):
    ws.append([m, ing, gas, f"=B{i}-C{i}"])            # fórmula: Excel la calcula al abrir

head = PatternFill("solid", fgColor="1F4E78")
thin = Side(style="thin", color="BFBFBF")
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = head
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
for row in ws.iter_rows(min_row=2, min_col=2, max_col=4):
    for c in row:
        c.number_format = '#,##0.00 "€"'
        c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
for col in range(1, ws.max_column + 1):
    ws.column_dimensions[get_column_letter(col)].width = 14
ws.freeze_panes = "A2"                                 # fija la fila de encabezados
ws.auto_filter.ref = ws.dimensions                     # filtros en los encabezados

n = ws.max_row
ws.conditional_formatting.add(f"D2:D{n}", CellIsRule(operator="lessThan", formula=["0"],
                                                      font=Font(color="9C0006"), fill=PatternFill("solid", fgColor="FFC7CE")))
ws.conditional_formatting.add(f"B2:B{n}", ColorScaleRule(start_type="min", start_color="F8696B",
                                                          end_type="max", end_color="63BE7B"))

ch = BarChart()
ch.type = "col"
ch.title = "Ingresos y gastos"
ch.y_axis.title = "€"
ch.x_axis.title = "Mes"
data = Reference(ws, min_col=2, max_col=3, min_row=1, max_row=n)  # incluye encabezados → nombres de series
cats = Reference(ws, min_col=1, min_row=2, max_row=n)
ch.add_data(data, titles_from_data=True)
ch.set_categories(cats)
ch.width, ch.height = 16, 8                           # cm aproximados
ws.add_chart(ch, "F2")
wb.save("informe.xlsx")
```

Dispersión (XY): `ScatterChart()`, y por serie
`s = Series(Reference(ws, min_col=3, min_row=2, max_row=n), Reference(ws, min_col=2, min_row=2, max_row=n), title="Medidas")`
(primero Y, luego X); `s.marker.symbol = "circle"`; `s.graphicalProperties.line.noFill = True` para solo puntos;
`ch.series.append(s)`.

## Fallos frecuentes

- **openpyxl no calcula fórmulas**: al leer con `load_workbook(..., data_only=True)` un archivo creado por Python
  las fórmulas valen `None` hasta que Excel o LibreOffice lo recalculen. Si necesitas los valores, calcula en
  pandas o recalcula: `soffice --headless --convert-to xlsx --outdir recalculado libro.xlsx`.
- `load_workbook` + `save` **pierde** gráficos e imágenes existentes que openpyxl no entiende y las macros
  (para .xlsm usa `keep_vba=True`). Avisa al usuario y guarda con otro nombre.
- No mezcles xlsxwriter y openpyxl sobre el mismo archivo: xlsxwriter no puede abrir archivos existentes.
- Fechas: escribe objetos `datetime`/`date` y aplica `number_format = "DD/MM/YYYY"`; no escribas cadenas.
- Números como texto (p. ej. "1.234,5") no se pueden sumar: conviértelos antes a float.
- Nombre de hoja ≤ 31 caracteres y sin `[]:*?/\`.
- Límite: 1 048 576 filas × 16 384 columnas; para más datos usa CSV/Parquet.

## pandas: varias hojas con formato básico

```python
import pandas as pd
with pd.ExcelWriter("resultados.xlsx", engine="xlsxwriter") as xw:
    df.to_excel(xw, sheet_name="Datos", index=False)
    resumen.to_excel(xw, sheet_name="Resumen")
    wb, ws = xw.book, xw.sheets["Datos"]
    ws.set_column(0, len(df.columns) - 1, 14)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(df), len(df.columns) - 1)
```
Leer: `pd.read_excel("libro.xlsx", sheet_name=None)` devuelve un dict {hoja: DataFrame};
`header=2` si los encabezados están en la tercera fila; `usecols="A:F"`, `skiprows`, `nrows`.

Plantilla completa con xlsxwriter (gráfico de columnas + línea, formatos, totales): `ejemplo_xlsxwriter.py`.
