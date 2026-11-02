---
name: analisis-estadistico
description: Úsala para elegir y aplicar la prueba estadística adecuada (t, ANOVA, no paramétricas, chi², correlación, regresión, modelos mixtos), comprobar supuestos, calcular tamaños del efecto e IC y redactar los resultados en formato APA con scipy/statsmodels.
license: MIT
---
# Análisis estadístico con scipy y statsmodels

Instalados: scipy, statsmodels, pandas, numpy, scikit-learn, seaborn. `pingouin` NO está (en un workspace con
paquetes habilitados: `pip install pingouin`). Funciones de tamaño del efecto listas para copiar: `efectos.py`.

## 1. Antes de calcular nada

- Pregunta de investigación e hipótesis (H0/H1), unilateral o bilateral (por defecto bilateral) y α (0,05).
- Diseño: ¿grupos **independientes** o **medidas repetidas/emparejadas**? ¿cuántos grupos? ¿unidad experimental
  (no confundas réplicas técnicas con n)?
- Tipo de variable respuesta: continua, ordinal, binaria, recuento, tiempo hasta evento.
- Explora: n por grupo, faltantes, histogramas/boxplots, atípicos. Decide qué hacer con ellos ANTES de ver p.

## 2. Elegir la prueba

| Situación | Paramétrica | Alternativa |
|---|---|---|
| 1 muestra vs valor | `stats.ttest_1samp(x, mu)` | `stats.wilcoxon(x - mu)` |
| 2 grupos independientes | Welch `stats.ttest_ind(a, b, equal_var=False)` | `stats.mannwhitneyu(a, b)` |
| 2 medidas emparejadas | `stats.ttest_rel(a, b)` | `stats.wilcoxon(a, b)` |
| ≥3 grupos independientes | ANOVA `smf.ols("y ~ C(g)").fit()` + `anova_lm` (Welch: `anova_oneway`) | `stats.kruskal(*grupos)` |
| ≥3 medidas repetidas | `AnovaRM` o modelo mixto | `stats.friedmanchisquare(*medidas)` |
| 2 factores | `smf.ols("y ~ C(a) * C(b)")` + `anova_lm(typ=2)` | transformar / modelo robusto |
| 2 variables categóricas | `stats.chi2_contingency(tabla)` | `stats.fisher_exact` (2×2, esperadas < 5) |
| Asociación continua | `stats.pearsonr(x, y)` | `stats.spearmanr(x, y)` / `kendalltau` |
| Predecir continua | `smf.ols("y ~ x1 + x2")` | `smf.rlm` (robusta) |
| Predecir binaria | `smf.logit("y ~ x1 + x2")` | — |
| Recuentos | `smf.poisson` / `smf.negativebinomial` | — |
| Medidas repetidas, datos desbalanceados | `smf.mixedlm("y ~ t * g", df, groups=df["sujeto"])` | — |

Welch es preferible a Student por defecto (no exige varianzas iguales y pierde muy poca potencia).

## 3. Supuestos

```python
from scipy import stats
stats.shapiro(residuos)                 # normalidad (de los RESIDUOS o de cada grupo); n > ~50: mira el QQ-plot
stats.levene(a, b, c, center="median")  # homogeneidad de varianzas (Brown-Forsythe)
import statsmodels.api as sm
sm.qqplot(modelo.resid, line="45", fit=True)
```
Con n grande la normalidad casi siempre "falla" sin importancia práctica: decide con gráficos + robustez del método.
Independencia: la garantiza el diseño, no una prueba.

## 4. Código de referencia

```python
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.oneway import anova_oneway

# Two independent groups (Welch) with 95 % CI of the mean difference
r = stats.ttest_ind(a, b, equal_var=False)
ic = r.confidence_interval(confidence_level=0.95)
print(f"t({r.df:.1f}) = {r.statistic:.2f}, p = {r.pvalue:.3f}, IC95 [{ic.low:.2f}, {ic.high:.2f}]")

# One-way ANOVA + Tukey post hoc (df long: columns y, g)
m = smf.ols("y ~ C(g)", data=df).fit()
tabla = anova_lm(m, typ=2)
print(tabla)
print(pairwise_tukeyhsd(df["y"], df["g"]).summary())
print(anova_oneway(df["y"], df["g"], use_var="unequal"))   # Welch ANOVA if variances differ

# Several p-values (e.g. many outcomes or pairwise tests)
rechazo, p_ajust, _, _ = multipletests(pvalores, alpha=0.05, method="holm")   # o "fdr_bh"

# Chi-square with expected counts check
chi2, p, gl, esperadas = stats.chi2_contingency(pd.crosstab(df["x"], df["y"]))

# Regression: coefficients, CI and diagnostics
m = smf.ols("y ~ x1 + C(grupo)", data=df).fit()
print(m.summary())                      # coef, IC 95 %, R², F; revisa m.resid frente a m.fittedvalues

# Mixed model (repeated measures per subject)
mm = smf.mixedlm("y ~ tiempo * grupo", df, groups=df["sujeto"]).fit()
print(mm.summary())

# Bootstrap CI for any statistic (e.g. the median)
res = stats.bootstrap((x,), np.median, confidence_level=0.95, n_resamples=9999, rng=np.random.default_rng(1))
print(res.confidence_interval)

# Sample size / power
from statsmodels.stats.power import TTestIndPower
n = TTestIndPower().solve_power(effect_size=0.5, alpha=0.05, power=0.8)   # ≈ 64 per group
```

## 5. Tamaño del efecto (siempre, con IC si es posible)

| Prueba | Efecto | Pequeño / medio / grande (Cohen, orientativo) |
|---|---|---|
| t | d de Cohen o g de Hedges | 0,2 / 0,5 / 0,8 |
| ANOVA | η² parcial u ω² | 0,01 / 0,06 / 0,14 |
| Mann-Whitney / Wilcoxon | r = Z/√N o correlación biserial de rangos | 0,1 / 0,3 / 0,5 |
| chi² | V de Cramér | depende de gl (gl=1: 0,1 / 0,3 / 0,5) |
| correlación | r | 0,1 / 0,3 / 0,5 |
| logística | odds ratio `np.exp(m.params)` con `np.exp(m.conf_int())` | — |

Los umbrales de Cohen son orientativos: interpreta en el contexto del campo.

## 6. Redacción (APA 7)

- "Los tratados mostraron mayor resistencia (M = 13,1; DE = 2,0) que los controles (M = 10,2; DE = 1,9),
  t(37,8) = 4,71, p < 0,001, d = 1,49, IC 95 % [0,78; 2,18]."
- p exacto con 2-3 decimales (p = 0,032); "p < 0,001" solo por debajo de 0,001. Nunca "p = 0,000".
- En español, coma decimal y punto y coma para separar los límites del IC.
- Reporta n, pruebas usadas, correcciones, software (Python, scipy x.y, statsmodels x.y) y supuestos.

## Errores que debes evitar

- Varias pruebas t en lugar de ANOVA/post hoc, o muchas comparaciones sin corregir.
- Tratar medidas repetidas como independientes (pseudorreplicación).
- Concluir "no hay efecto" de p > 0,05 (ausencia de evidencia ≠ evidencia de ausencia): mira el IC y la potencia.
- Elegir la prueba después de ver qué da p < 0,05; eliminar atípicos sin criterio previo.
- Correlación ≠ causalidad; R² alto no valida el modelo sin revisar residuos.
