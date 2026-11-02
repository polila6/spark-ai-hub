---
name: graficos-publicacion
description: Úsala para hacer figuras científicas con calidad de publicación (artículos, tesis, pósters) con matplotlib/seaborn: tamaño y fuentes correctos, paletas aptas para daltónicos, barras de error, paneles (a)(b), exportación PDF/SVG/PNG 300 ppp.
license: MIT
---
# Figuras con calidad de publicación (matplotlib / seaborn)

Instalados: matplotlib 3.x, seaborn 0.13, numpy, pandas. Sin pantalla: el backend es Agg (guarda archivos).

## Pasos

1. Decide el mensaje de la figura en una frase; elige el tipo:
   - comparar grupos (datos continuos) → puntos individuales + media/mediana e IC, caja o violín;
     **no** barras con barras de error ("dinamita") salvo para recuentos o proporciones;
   - evolución → líneas (con banda de IC si hay réplicas); relación → dispersión (+ recta y R²);
   - distribución → histograma, KDE o ECDF; matriz → heatmap con barra de color y escala adecuada
     (divergente centrada en 0 para correlaciones).
2. Fija el tamaño FINAL antes de dibujar: una columna ≈ 3,5 in (8,9 cm), doble columna ≈ 7,2 in (18 cm),
   diapositiva ≈ 10 × 5,6 in. Nunca escales la figura después: las fuentes cambiarían.
3. Aplica el estilo de `estilo_publicacion.py`. En un workspace, tras use_skill está en
   `.skills/graficos-publicacion/` (cópialo junto a tu script); con run_code pasa `skills=["graficos-publicacion"]`
   y añade `sys.path.insert(0, "/work/skills/graficos-publicacion")`. Si no, léelo con read_skill_file y pega
   la función. Después: `from estilo_publicacion import aplicar_estilo, OKABE_ITO, etiquetar_paneles`.
4. Dibuja, etiqueta ejes con magnitud y unidad entre paréntesis: "Temperatura (°C)", "Tiempo (s)".
5. Exporta: PDF/SVG (vectorial) para LaTeX y revistas; PNG 300 ppp (600 para dibujos de líneas) para Word y
   presentaciones; TIFF si la revista lo exige (`fig.savefig("f.tiff", dpi=600)`, requiere Pillow, instalado).
6. Mira la figura generada (ábrela o conviértela a PNG) y comprueba solapes, tamaños y legibilidad.

## Plantilla básica

```python
import matplotlib.pyplot as plt
import numpy as np
from estilo_publicacion import OKABE_ITO, aplicar_estilo, etiquetar_paneles

aplicar_estilo(tamano=8)                        # fuentes 8 pt (revista); 14-18 para diapositivas
rng = np.random.default_rng(1)
fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.8), layout="constrained")

grupos = {"Control": rng.normal(10, 2, 20), "Tratamiento": rng.normal(13, 2, 20)}
for i, (nombre, y) in enumerate(grupos.items()):
    x = np.full(y.size, i) + rng.uniform(-0.12, 0.12, y.size)          # jitter
    a.scatter(x, y, s=10, color=OKABE_ITO[i], alpha=0.7, lw=0)
    m, ic = y.mean(), 1.96 * y.std(ddof=1) / np.sqrt(y.size)
    a.errorbar(i + 0.3, m, yerr=ic, fmt="o", color="black", capsize=3, ms=4)
a.set_xticks(range(len(grupos)), list(grupos))
a.set_ylabel("Resistencia (MPa)")

t = np.linspace(0, 10, 200)
for i, k in enumerate([0.2, 0.4, 0.8]):
    b.plot(t, np.exp(-k * t), color=OKABE_ITO[i], label=f"$k = {k}$")
b.set_xlabel("Tiempo (s)")
b.set_ylabel("Concentración relativa")
b.legend(frameon=False)

etiquetar_paneles([a, b])                      # (a), (b)
fig.savefig("figura1.pdf")                     # vectorial
fig.savefig("figura1.png", dpi=300)
```

## Reglas de estilo

- Texto de la figura entre 7 y 10 pt a tamaño final (igual o algo menor que el texto del artículo).
- Colores: Okabe-Ito para categorías (≤ 8), `viridis`/`cividis` secuenciales, `RdBu_r`/`coolwarm` divergentes con
  `TwoSlopeNorm(vcenter=0)`. Nunca `jet`/`rainbow`. Distingue también por marcador o estilo de línea (impresión en
  gris y daltonismo).
- Quita el marco superior y derecho, rejillas suaves o ninguna, sin títulos dentro de la figura en artículos (va
  en el pie). Leyenda sin marco y dentro solo si no tapa datos.
- Explica siempre qué representan las barras de error (DE, EE o IC 95 %) y n en el pie de figura.
- Mismo eje y misma escala en paneles comparables (`sharey=True`).
- Escala logarítmica cuando los datos abarcan órdenes de magnitud (`ax.set_yscale("log")`).
- Números con coma decimal en figuras en español: `plt.rcParams["axes.formatter.use_locale"] = True` solo
  funciona si el locale es_ES está instalado (no suele estarlo en el entorno); alternativa fiable:
  `ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}".replace(".", ",")))`
  (`from matplotlib.ticker import FuncFormatter`).

## seaborn (rápido para datos en formato largo)

```python
import seaborn as sns
ax = sns.boxplot(data=df, x="grupo", y="valor", color="white", width=0.5, fliersize=0)
sns.stripplot(data=df, x="grupo", y="valor", ax=ax, size=3, alpha=0.6, color="0.2")
g = sns.relplot(data=df, x="tiempo", y="valor", hue="grupo", kind="line", errorbar=("ci", 95),
                height=2.6, aspect=1.3, palette=OKABE_ITO[:3])
g.savefig("evolucion.pdf")
```
En seaborn 0.13 el intervalo se elige con `errorbar=("ci", 95)`, `"sd"` o `"se"` (el antiguo `ci=` está obsoleto).

## Fallos frecuentes

- Etiquetas cortadas al guardar → usa `layout="constrained"` (o `bbox_inches="tight"` al guardar).
- Fuentes distintas a las del documento LaTeX: con `text.usetex=True` (TeX Live está instalado) las fuentes
  coinciden, pero es más lento; alternativa: `mathtext.fontset="cm"` y `font.family="serif"`.
- PDF muy pesado con millones de puntos → `ax.scatter(..., rasterized=True)` y `dpi=300` al guardar.
- Caracteres especiales (°, µ, ±, ²) funcionan con DejaVu Sans; en mathtext usa `r"$\mu$m"`.
