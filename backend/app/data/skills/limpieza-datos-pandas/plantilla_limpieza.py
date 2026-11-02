"""Reproducible cleaning script template (pandas 3). Adapt the CONFIG block and the `limpiar` steps.

Run: python limpieza.py datos/crudo/archivo.csv
Writes datos/limpio/<name>.parquet, datos/limpio/<name>.csv, datos/limpio/<name>_descartadas.csv and prints a report.
"""
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- CONFIG
SEP, DECIMAL, THOUSANDS = ";", ",", "."   # Spanish Excel export; use ",", ".", None for international CSV
COLUMNAS_FECHA = ["fecha"]                # parsed day-first (dd/mm/yyyy)
COLUMNAS_NUMERO = ["valor"]
COLUMNAS_TEXTO_CODIGO = ["codigo"]        # keep as text (leading zeros)
CODIGOS_SIN_DATO = [-99, -999, 9999]
CLAVE = ["id"]                            # columns that must identify a row


def normalizar_nombre(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode("ascii")
    s = "".join(ch if ch.isalnum() else "_" for ch in s.strip().lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "columna"


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: normalizar_nombre(c) for c in df.columns})


def _codigos_como_texto(columnas) -> dict:
    """dtype mapping for the ORIGINAL column names whose normalized name is a code column."""
    return {c: "str" for c in columnas if normalizar_nombre(c) in COLUMNAS_TEXTO_CODIGO}


def leer(ruta: Path) -> pd.DataFrame:
    if ruta.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        cols = pd.read_excel(ruta, nrows=0).columns
        return pd.read_excel(ruta, dtype=_codigos_como_texto(cols))
    for enc in ("utf-8-sig", "latin-1"):
        try:
            opts = {"sep": SEP, "encoding": enc}
            cols = pd.read_csv(ruta, nrows=0, **opts).columns
            return pd.read_csv(ruta, decimal=DECIMAL, thousands=THOUSANDS, dtype=_codigos_como_texto(cols), **opts)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"No se pudo leer {ruta}")


def informe_faltantes(df: pd.DataFrame) -> pd.Series:
    return df.isna().sum()


def limpiar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = normalizar_columnas(df)
    descartadas = []

    # text: trim spaces, empty strings -> NaN
    for c in df.select_dtypes(include="str").columns:
        df[c] = df[c].str.strip().replace("", pd.NA)

    # numbers
    for c in [c for c in COLUMNAS_NUMERO if c in df]:
        antes = df[c].notna()
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.to_numeric(df[c].astype("str").str.replace(".", "", regex=False)
                                  .str.replace(",", ".", regex=False), errors="coerce")
        df[c] = df[c].mask(df[c].isin(CODIGOS_SIN_DATO))
        perdidos = antes & df[c].isna()
        if perdidos.any():
            print(f"[{c}] {int(perdidos.sum())} valores no numéricos o códigos sin dato -> NaN")

    # dates
    for c in [c for c in COLUMNAS_FECHA if c in df]:
        original = df[c].copy()
        df[c] = pd.to_datetime(df[c], dayfirst=True, errors="coerce")
        malas = original.notna() & df[c].isna()
        if malas.any():
            print(f"[{c}] {int(malas.sum())} fechas no válidas, p. ej.: {original[malas].head(3).tolist()}")

    # duplicates (exact), then by key (kept for review)
    dup = df.duplicated()
    if dup.any():
        descartadas.append(df[dup].assign(motivo="duplicado exacto"))
        df = df[~dup]
    clave = [c for c in CLAVE if c in df]
    if clave:
        dup_clave = df.duplicated(subset=clave, keep=False)
        if dup_clave.any():
            print(f"Aviso: {int(dup_clave.sum())} filas comparten clave {clave} (revisar, no se eliminan)")

    # outliers: flagged, not removed
    for c in [c for c in COLUMNAS_NUMERO if c in df]:
        q1, q3 = df[c].quantile([0.25, 0.75])
        iqr = q3 - q1
        df[f"{c}_atipico"] = df[c].notna() & ~df[c].between(q1 - 1.5 * iqr, q3 + 1.5 * iqr)

    out = pd.concat(descartadas) if descartadas else pd.DataFrame(columns=[*df.columns, "motivo"])
    return df.reset_index(drop=True), out


def main(ruta: str) -> None:
    ruta = Path(ruta)
    crudo = leer(ruta)
    limpio, descartadas = limpiar(crudo)
    destino = Path("datos/limpio")
    destino.mkdir(parents=True, exist_ok=True)
    limpio.to_parquet(destino / f"{ruta.stem}.parquet", index=False)
    limpio.to_csv(destino / f"{ruta.stem}.csv", index=False, encoding="utf-8-sig")
    descartadas.to_csv(destino / f"{ruta.stem}_descartadas.csv", index=False, encoding="utf-8-sig")
    print("\n== Informe de limpieza ==")
    print(f"Filas: {len(crudo)} -> {len(limpio)} (descartadas: {len(descartadas)})")
    print(f"Columnas: {crudo.shape[1]} -> {limpio.shape[1]}")
    print("Faltantes por columna:\n" + informe_faltantes(limpio).to_string())
    print("Tipos:\n" + limpio.dtypes.astype(str).to_string())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python limpieza.py <archivo.csv|xlsx>")
    main(sys.argv[1])
