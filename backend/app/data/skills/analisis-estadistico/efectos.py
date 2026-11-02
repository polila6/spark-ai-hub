"""Effect sizes with confidence intervals (numpy/scipy only). Copy the functions you need.

Run as a script for a quick self-test: python efectos.py
"""
import numpy as np
from scipy import stats


def cohen_d(a, b, paired: bool = False) -> float:
    """Cohen's d. Independent groups: pooled SD. Paired: d_z = mean(diff) / sd(diff)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if paired:
        diff = a - b
        return diff.mean() / diff.std(ddof=1)
    na, nb = a.size, b.size
    sp = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return (a.mean() - b.mean()) / sp


def hedges_g(a, b) -> float:
    """Hedges' g: Cohen's d with small-sample bias correction (independent groups)."""
    n = len(a) + len(b)
    return cohen_d(a, b) * (1 - 3 / (4 * n - 9))


def d_ci(d: float, na: int, nb: int, level: float = 0.95) -> tuple[float, float]:
    """Approximate CI for d (normal approximation of its standard error; Hedges & Olkin)."""
    se = np.sqrt((na + nb) / (na * nb) + d**2 / (2 * (na + nb)))
    z = stats.norm.ppf(0.5 + level / 2)
    return float(d - z * se), float(d + z * se)


def eta2_parcial(tabla_anova, efecto: str) -> float:
    """Partial eta squared from a statsmodels anova_lm table (columns sum_sq; row 'Residual')."""
    ss = tabla_anova.loc[efecto, "sum_sq"]
    return ss / (ss + tabla_anova.loc["Residual", "sum_sq"])


def omega2(tabla_anova, efecto: str) -> float:
    """Omega squared (less biased than eta squared) from a one-way/between anova_lm table."""
    ss, gl = tabla_anova.loc[efecto, "sum_sq"], tabla_anova.loc[efecto, "df"]
    ms_err = tabla_anova.loc["Residual", "sum_sq"] / tabla_anova.loc["Residual", "df"]
    ss_tot = tabla_anova["sum_sq"].sum()
    return (ss - gl * ms_err) / (ss_tot + ms_err)


def r_rangos_biserial(a, b) -> float:
    """Rank-biserial correlation for Mann-Whitney U (−1..1; sign: a > b is positive)."""
    u = stats.mannwhitneyu(a, b, alternative="two-sided").statistic  # U of the first sample
    return 2 * u / (len(a) * len(b)) - 1


def cramer_v(tabla) -> float:
    """Cramér's V for an r x c contingency table (no continuity correction)."""
    tabla = np.asarray(tabla)
    chi2 = stats.chi2_contingency(tabla, correction=False)[0]
    n = tabla.sum()
    k = min(tabla.shape) - 1
    return float(np.sqrt(chi2 / (n * k)))


def r_ci(r: float, n: int, level: float = 0.95) -> tuple[float, float]:
    """CI of a Pearson correlation via Fisher z."""
    z = np.arctanh(r)
    se = 1 / np.sqrt(n - 3)
    q = stats.norm.ppf(0.5 + level / 2)
    return float(np.tanh(z - q * se)), float(np.tanh(z + q * se))


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    a, b = rng.normal(13, 2, 20), rng.normal(10, 2, 20)
    d = cohen_d(a, b)
    print(f"d = {d:.2f}, g = {hedges_g(a, b):.2f}, IC95 = {d_ci(d, 20, 20)}")
    print(f"r biserial = {r_rangos_biserial(a, b):.2f}")
    print(f"V = {cramer_v([[10, 20], [25, 5]]):.2f}")
    print(f"IC r=0.45, n=30: {r_ci(0.45, 30)}")
