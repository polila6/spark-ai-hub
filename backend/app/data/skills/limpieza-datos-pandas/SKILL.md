---
name: limpieza-datos-pandas
description: Úsala para limpiar y preparar datos tabulares con pandas (CSV/Excel de instrumentos, encuestas, exportaciones en español con ; y coma decimal): tipos, fechas, duplicados, faltantes, atípicos, formato largo/ancho y validación, con un script reproducible.
license: MIT
---
# Limpieza de datos con pandas 3

La versión instalada es **pandas 3.1** (con pyarrow). Cambios que rompen código antiguo:
- **Copy-on-Write siempre activo**: `df["col"][mask] = valor` (asignación encadenada) NO modifica nada (solo
  avisa con `ChainedAssignmentError`). Usa `df.loc[mask, "col"] = valor`.
- El texto es por defecto de tipo `str` (StringDtype), no `object`: selecciónalo con
  `df.select_dtypes(include="str")`; los faltantes de texto son `NaN`.
- `inplace=True` está obsoleto en muchos métodos: reasigna (`df = df.drop_duplicates()`).

## Flujo de trabajo

1. **Nunca toques el original**: lee de `datos/crudo/…` y escribe en `datos/limpio/…`. Todo en un script
   (`limpieza.py`, plantilla en `plantilla_limpieza.py`) que se pueda volver a ejecutar.
2. **Inspecciona**: `df.shape`, `df.dtypes`, `df.head()`, `df.isna().sum()`, `df.nunique()`,
   `df.describe(include="all")`, valores raros por columna (`df[c].value_counts(dropna=False).head(20)`).
3. **Lee bien el archivo** (el 80 % de los problemas):
   ```python
   df = pd.read_csv("datos.csv", sep=";", decimal=",", thousands=".", encoding="utf-8-sig")  # Excel español
   # codificación desconocida: prueba "utf-8-sig" y, si falla con UnicodeDecodeError, "latin-1" (o "cp1252")
   df = pd.read_excel("datos.xlsx", sheet_name="Hoja1", header=0, skiprows=0)    # header=2 si hay títulos encima
   ```
   Detecta el separador con `sep=None, engine="python"` si no lo sabes.
4. **Nombres de columnas** normalizados (minúsculas, sin tildes ni espacios): ver `normalizar_columnas()`.
5. **Tipos**:
   ```python
   # solo si la columna quedó como texto (p. ej. "1.234,5" con read_csv sin decimal=","):
   df["valor"] = pd.to_numeric(df["valor"].str.replace(".", "", regex=False)
                                          .str.replace(",", ".", regex=False), errors="coerce")
   df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce")     # 03/02/2024 = 3 de febrero
   df["grupo"] = df["grupo"].str.strip().str.lower().astype("category")
   ```
   Tras cada conversión con `errors="coerce"`, **cuenta los nuevos NaN** y muestra ejemplos de lo que no se
   pudo convertir: son los datos sucios que hay que entender.
6. **Duplicados**: exactos `df.duplicated().sum()`; por clave `df.duplicated(subset=["id", "fecha"], keep=False)`.
   Revisa antes de borrar.
7. **Faltantes**: entiende el motivo (no medido, no aplica, error). Opciones: eliminar filas
   (`dropna(subset=[...])`) si son pocas y al azar; imputar (mediana por grupo,
   `df["x"] = df["x"].fillna(df.groupby("g")["x"].transform("median"))`) dejando una columna indicadora
   `x_imputado`; o dejarlos y que el análisis los gestione. Documenta la decisión.
8. **Atípicos**: márcalos, no los borres sin criterio:
   ```python
   q1, q3 = df["x"].quantile([0.25, 0.75])
   iqr = q3 - q1
   df["x_atipico"] = ~df["x"].between(q1 - 1.5 * iqr, q3 + 1.5 * iqr)
   ```
   Elimina solo valores imposibles (temperaturas de −999, edades de 250) o códigos de "sin dato" (−99, 9999):
   `df["x"] = df["x"].mask(df["x"].isin([-99, 9999]))`.
9. **Texto**: `str.strip()`, `str.lower()`, unificar variantes con un dict (`.replace({"h": "hombre", ...})`),
   quitar tildes: `s.str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii")`.
10. **Forma ordenada (tidy)**: una fila por observación, una columna por variable.
    ancho → largo: `df.melt(id_vars=["id"], var_name="semana", value_name="valor")`;
    largo → ancho: `df.pivot_table(index="id", columns="semana", values="valor", aggfunc="mean")`.
11. **Valida** con asserts antes de guardar:
    ```python
    assert df["id"].is_unique
    assert df["edad"].between(0, 120).all()
    assert df["fecha"].notna().all()
    ```
12. **Guarda** en Parquet (conserva tipos) y CSV para el usuario:
    `df.to_parquet("datos/limpio/datos.parquet", index=False)`;
    `df.to_csv("datos/limpio/datos.csv", index=False, encoding="utf-8-sig")` (Excel lo abre con tildes) o
    `sep=";", decimal=","` si lo van a abrir en un Excel en español.
13. **Informe de limpieza**: filas/columnas antes y después, faltantes por columna, transformaciones y filas
    descartadas (guárdalas en `descartadas.csv`).

## Trampas frecuentes

- Fechas mezcladas: `pd.to_datetime(..., format="mixed", dayfirst=True)` interpreta también las ISO
  (`2024-01-03`) como día primero (→ 1 de marzo). Si conviven formatos, convierte cada uno por separado
  (`format="%Y-%m-%d"` y `format="%d/%m/%Y"`) y combina con `fillna`.
- Excel convierte códigos en números o fechas (`01234` → 1234, `3-4` → 3 de abril): lee esas columnas como texto
  (`dtype={"codigo": "str"}`).
- `0` frente a vacío: no imputes ceros por defecto.
- `groupby` excluye NaN de las claves: usa `dropna=False` si los quieres ver.
- Unir tablas: `pd.merge(a, b, on="id", how="left", validate="one_to_one", indicator=True)` y revisa
  `_merge` para detectar claves sin pareja.
- Archivos grandes (> 1 GB): `usecols=[...]`, `dtype` explícitos, o consulta con DuckDB/`query_data`.
