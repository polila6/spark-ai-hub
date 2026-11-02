"""Matplotlib style for publication figures. Copy next to your script and import it.

    from estilo_publicacion import aplicar_estilo, OKABE_ITO, etiquetar_paneles
    aplicar_estilo(tamano=8)            # journals: 7-9 pt; slides: 14-18 pt
"""
import matplotlib as mpl
import matplotlib.pyplot as plt

# Okabe & Ito (2008) colour-blind safe palette
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]

# Final figure widths in inches
UNA_COLUMNA = 3.5
DOBLE_COLUMNA = 7.2


def aplicar_estilo(tamano: float = 8, serif: bool = False, usetex: bool = False) -> None:
    """Clean, compact style: readable fonts at final size, no top/right spines, colour-blind palette."""
    mpl.rcParams.update({
        "font.size": tamano,
        "axes.titlesize": tamano,
        "axes.labelsize": tamano,
        "xtick.labelsize": tamano - 1,
        "ytick.labelsize": tamano - 1,
        "legend.fontsize": tamano - 1,
        "font.family": "serif" if serif else "sans-serif",
        "mathtext.fontset": "cm" if serif else "dejavusans",
        "text.usetex": usetex,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.8,
        "axes.prop_cycle": mpl.cycler(color=OKABE_ITO),
        "lines.linewidth": 1.4,
        "lines.markersize": 4,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "legend.frameon": False,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,  # embed TrueType fonts (editable text; required by many journals)
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def etiquetar_paneles(axes, fmt: str = "({})", letras: str = "abcdefghijklmnopqrstuvwxyz", **kw) -> None:
    """Write (a), (b)... in the top-left corner of each axes."""
    opts = {"fontweight": "bold", "va": "bottom", "ha": "left"} | kw
    for ax, letra in zip(list(getattr(axes, "flat", axes)), letras):
        ax.text(-0.02, 1.02, fmt.format(letra), transform=ax.transAxes, **opts)


if __name__ == "__main__":
    import numpy as np

    aplicar_estilo()
    fig, axs = plt.subplots(1, 2, figsize=(DOBLE_COLUMNA, 2.6), layout="constrained")
    x = np.linspace(0, 2 * np.pi, 100)
    for i in range(3):
        axs[0].plot(x, np.sin(x + i), label=f"fase {i}")
    axs[0].legend()
    axs[1].hist(np.random.default_rng(0).normal(size=500), bins=30, color=OKABE_ITO[0])
    etiquetar_paneles(axs)
    fig.savefig("prueba_estilo.pdf")
    fig.savefig("prueba_estilo.png")
    print("ok")
