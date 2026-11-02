"""Reproducible data -> report pipeline: CSV/Excel -> summary, tables, charts -> Markdown -> Word/PDF with pandoc.

Run: python generar_informe.py [datos.csv]     (without arguments it creates example data)
Outputs: informe.md, informe.docx, informe.pdf, figuras/*.png
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ENTRADA = Path(sys.argv[1]) if len(sys.argv) > 1 else None
FIG = Path("figuras")
FIG.mkdir(exist_ok=True)
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})


def es(x: float, dec: int = 1) -> str:
    """Spanish number format: 1234.5 -> '1.234,5'."""
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def cargar() -> pd.DataFrame:
    if ENTRADA is None:  # example data
        rng = np.random.default_rng(7)
        fechas = pd.date_range("2026-01-01", periods=180, freq="D")
        return pd.DataFrame({"fecha": np.tile(fechas, 3),
                             "sede": np.repeat(["Vera", "Alcoy", "Gandia"], len(fechas)),
                             "consumo_kwh": rng.gamma(9, 40, len(fechas) * 3).round(1)})
    if ENTRADA.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(ENTRADA)
    return pd.read_csv(ENTRADA, sep=None, engine="python")


df = cargar()
df["fecha"] = pd.to_datetime(df["fecha"])
df["mes"] = df["fecha"].dt.to_period("M").dt.to_timestamp()

# ---------------------------------------------------------------- calculations
n = len(df)
total = df["consumo_kwh"].sum()
por_sede = (df.groupby("sede")["consumo_kwh"].agg(total="sum", media="mean", dias="count")
            .sort_values("total", ascending=False))
mensual = df.pivot_table(index="mes", columns="sede", values="consumo_kwh", aggfunc="sum")
sede_max = por_sede.index[0]
cuota_max = por_sede["total"].iloc[0] / total * 100

# ---------------------------------------------------------------- figures
fig, ax = plt.subplots(figsize=(6.5, 3), layout="constrained")
for sede in mensual.columns:
    ax.plot(mensual.index, mensual[sede] / 1000, marker="o", label=sede)
ax.set_ylabel("Consumo mensual (MWh)")
ax.legend(frameon=False, ncols=3)
fig.autofmt_xdate()
fig.savefig(FIG / "evolucion.png", dpi=200)
plt.close(fig)

fig, ax = plt.subplots(figsize=(5, 2.6), layout="constrained")
ax.barh(por_sede.index[::-1], por_sede["total"][::-1] / 1000, color="#0072B2")
ax.set_xlabel("Consumo total (MWh)")
fig.savefig(FIG / "por_sede.png", dpi=200)
plt.close(fig)

# ---------------------------------------------------------------- Markdown
tabla = por_sede.reset_index().rename(columns={"sede": "Sede", "total": "Total (kWh)", "media": "Media diaria (kWh)",
                                               "dias": "Días"})
md = f"""---
title: "Informe de consumo energético"
date: "{date.today():%d/%m/%Y}"
lang: es-ES
header-includes:
  - \\AtBeginDocument{{\\renewcommand{{\\tablename}}{{Tabla}}}}
---

# Resumen ejecutivo

- Periodo analizado: {df['fecha'].min():%d/%m/%Y} – {df['fecha'].max():%d/%m/%Y} ({n} registros).
- Consumo total: **{es(total / 1000)} MWh**.
- La sede con mayor consumo es **{sede_max}**, con el {es(cuota_max)} % del total.

# Datos

Fuente: {ENTRADA.name if ENTRADA else "datos de ejemplo"}. Un registro por sede y día.

# Resultados

## Consumo por sede

{tabla.to_markdown(index=False, floatfmt=".1f")}

: Consumo por sede.

![Consumo total por sede.](figuras/por_sede.png){{width=70%}}

## Evolución mensual

![Consumo mensual por sede.](figuras/evolucion.png){{width=95%}}

# Conclusiones

1. {sede_max} concentra el {es(cuota_max)} % del consumo: es la prioridad para medidas de ahorro.
2. Revisar los meses con picos frente a la ocupación y la climatología.

*Limitaciones:* los datos no incluyen la superficie ni la ocupación, por lo que no se comparan intensidades.
"""
Path("informe.md").write_text(md, encoding="utf-8")

# ---------------------------------------------------------------- conversions
subprocess.run(["pandoc", "informe.md", "-o", "informe.docx", "--resource-path=."], check=True)
r = subprocess.run(["pandoc", "informe.md", "-o", "informe.pdf", "--pdf-engine=xelatex", "-V", "geometry:margin=2.5cm",
                    "-V", "mainfont=DejaVu Sans", "--resource-path=."], capture_output=True, text=True)
print("PDF:", "ok" if r.returncode == 0 else r.stderr[-800:])
print("Creados informe.md, informe.docx" + (", informe.pdf" if r.returncode == 0 else ""))
