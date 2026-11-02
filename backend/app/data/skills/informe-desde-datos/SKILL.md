---
name: informe-desde-datos
description: Úsala cuando el usuario quiera convertir un archivo de datos (CSV/Excel) en un informe automático y reproducible en Word, PDF o HTML con resumen, tablas, gráficos y conclusiones; o regenerar periódicamente el mismo informe con datos nuevos.
license: MIT
---
# De datos a informe (Word / PDF / HTML) con un script reproducible

Idea: un único script `informe.py` lee los datos, calcula, dibuja y escribe un Markdown que pandoc convierte a
.docx, .pdf (vía LaTeX) o .html. Cambian los datos → se vuelve a ejecutar y sale el informe actualizado.
Plantilla probada: `generar_informe.py` (read_skill_file; en un workspace, `.skills/informe-desde-datos/`).

## Pasos

1. **Entiende los datos y la pregunta**: qué decisión apoya el informe, para quién, qué indicadores importan.
   Inspecciona (`describe_data` o pandas: columnas, tipos, rango de fechas, faltantes). Si están sucios, aplica
   antes la skill limpieza-datos-pandas.
2. **Define el contenido** (máx. 5-7 bloques): resumen ejecutivo con 3-5 cifras clave → descripción de los datos
   (origen, periodo, n, exclusiones) → resultados por pregunta (tabla + gráfico + 2-3 frases que digan qué se ve)
   → conclusiones y limitaciones → anexo con detalles.
3. **Copia y adapta la plantilla** (`generar_informe.py`): cambia `ENTRADA`, las columnas y los bloques.
   Mantén separado cálculo, gráficos y texto.
4. **Ejecuta** `python informe.py` y revisa el resultado (abre el Markdown intermedio, comprueba cifras con una
   consulta independiente).
5. **Formatos**:
   ```bash
   pandoc informe.md -o informe.docx --resource-path=.          # Word (editable)
   pandoc informe.md -o informe.pdf --pdf-engine=xelatex -V lang=es -V geometry:margin=2.5cm \
          -V mainfont="DejaVu Sans" --resource-path=.          # PDF
   pandoc informe.md -o informe.html --standalone --self-contained --metadata title="Informe"   # HTML autocontenido
   ```
   En el PDF, LaTeX numera solo figuras y tablas ("Figura 1:"): no pongas "Figura 1." en el pie del Markdown.
   Con `lang: es-ES` las tablas se llaman "Cuadro"; la plantilla lo cambia a "Tabla" con `header-includes`.
6. Entrega el informe y el script, y explica cómo regenerarlo.

## Reglas para el texto generado

- Las cifras del texto salen de variables, nunca escritas a mano:
  `f"El valor medio fue {media:.1f} {unidad} (n = {n})"`.
- Formato español: `f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")` → 1.234,56
  (función `es()` en la plantilla).
- Cada gráfico con número, título descriptivo y una frase de interpretación; cada tabla con unidades.
- Di qué NO muestran los datos (sesgos, periodo incompleto, faltantes).
- Tablas pandas → Markdown: `df.to_markdown(index=False, floatfmt=".2f")` (usa tabulate, instalado).

## Variantes

- **Excel con gráficos** como entregable: skill excel-con-graficos.
- **Word con formato fino** (estilos, portada, encabezados): genera el docx con python-docx (skill documento-word)
  o usa pandoc con `--reference-doc=plantilla.docx`.
- **Informe periódico**: el usuario puede programarlo como tarea en el chat ("cada lunes genera el informe con el
  último CSV de la carpeta…").
- **Muchos datos**: agrega con DuckDB/SQL (`query_data`) y trae a pandas solo el resultado.

## Errores frecuentes

- PDF con pandoc: `! LaTeX Error: Unicode character` → usa `--pdf-engine=xelatex` (o lualatex).
- Imágenes que no aparecen: rutas relativas al directorio desde el que se ejecuta pandoc; usa `--resource-path`.
- Tablas muy anchas en PDF: reduce columnas, redondea o pasa la tabla a anexo / Excel.
- Gráficos con fechas solapadas: `fig.autofmt_xdate()` o `matplotlib.dates.ConciseDateFormatter`.
