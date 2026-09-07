"""Shared plotting utilities for the green_mobility paper figures.

Color-blind safe (Okabe-Ito based), consistent modal/climate colors across
all six figures. Import this from every notebook in this directory instead
of redefining colors/helpers locally.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Modal colors (fixed across the whole paper) ──────────────────────────────
MODE_COLORS = {
    "car": "#D55E00",       # vermillion
    "walk": "#009E73",      # bluish green
    "bike": "#0072B2",      # blue
    "active": "#56B4E9",    # sky blue (walk+bike combined)
    "multimodal": "#333333",  # dark grey/black
}

# ── Climate-regime colors: real per-city Köppen-Geiger codes (Wikipedia
# climate sections, sourced to AEMET/Köppen-Geiger; see figure1 notebook for
# the per-city citation check). Replaces an earlier custom 5-bucket scheme
# that mixed a Köppen axis with an elevation axis and mis-grouped Sevilla/
# Córdoba (Csa) as semi-arid -- not a downloaded dataset, but now a verified
# external classification rather than an ad hoc one.
CLIMATE_COLORS = {
    "BSh": "#CC79A7",  # hot semi-arid (Palma, Valencia, Murcia)
    "BSk": "#E69F00",  # cold semi-arid (Zaragoza, Madrid)
    "Csa": "#009E73",  # hot-summer Mediterranean (Barcelona, Sevilla, Córdoba, Valladolid, Granada)
    "Csb": "#56B4E9",  # warm-summer Mediterranean (A Coruña)
    "Cfb": "#0072B2",  # oceanic (Bilbao)
}

CLIMATE_REGION_LABELS = {
    "BSh": "BSh (hot semi-arid)",
    "BSk": "BSk (cold semi-arid)",
    "Csa": "Csa (hot-summer Mediterranean)",
    "Csb": "Csb (warm-summer Mediterranean)",
    "Cfb": "Cfb (oceanic)",
}

SHADE_STATE_COLORS = {
    "0": "#999999",
    "B": "#0072B2",
    "T": "#009E73",
    "BT": "#D55E00",
}

PANEL_LABELS = ["A", "B", "C", "D"]


def set_nature_style():
    matplotlib.rcParams.update({
        "font.size": 8,
        "font.family": "sans-serif",
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "pdf.fonttype": 42,  # embed fonts as real text, not outlines
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def new_panel_fig(width_in=3.5, height_in=3.0):
    """Nature two-column-compatible single-panel figure (max ~89mm per
    single-column panel; using 3.5in ~ 89mm)."""
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    return fig, ax


def label_panel(ax, letter, x=-0.15, y=1.05, fontsize=11):
    ax.text(x, y, letter, transform=ax.transAxes, fontsize=fontsize, fontweight="bold", va="bottom")


def save_panel(fig, out_dir: Path, name: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(out_dir / f"{name}.png", bbox_inches="tight", dpi=300)


def save_source_table(df, out_dir: Path, name: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / f"{name}_source.csv", index=False)


def blocked_panel(ax, letter, reason: str, missing_inputs: list[str]):
    """Renders an explicit BLOCKED panel -- no synthetic data, no invented
    values, no misleadingly-complete-looking placeholder."""
    ax.axis("off")
    label_panel(ax, letter, x=0.02, y=0.95)
    text = "BLOCKED\n\n" + reason + "\n\nMissing inputs:\n" + "\n".join(f"- {m}" for m in missing_inputs)
    ax.text(0.5, 0.45, text, transform=ax.transAxes, ha="center", va="center",
            fontsize=6.5, wrap=True, family="monospace",
            bbox=dict(boxstyle="round", facecolor="#f5f5f5", edgecolor="#999999"))


def make_composite(panel_paths_png: list[Path], out_dir: Path, name: str):
    """Grid composite preview from already-saved panel PNGs (grid sized to
    fit however many panel images are passed, not fixed at 2x2)."""
    import math
    import matplotlib.image as mpimg
    n = len(panel_paths_png)
    cols = 2 if n <= 4 else math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols) if n > 1 else 1
    fig, axes = plt.subplots(rows, cols, figsize=(3.5 * cols, 3.0 * rows))
    axes = axes.flatten() if hasattr(axes, "flatten") else [axes]
    for i, ax in enumerate(axes):
        if i < n and panel_paths_png[i].exists():
            ax.imshow(mpimg.imread(panel_paths_png[i]))
        ax.axis("off")
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}_composite.pdf", bbox_inches="tight")
    fig.savefig(out_dir / f"{name}_composite.png", bbox_inches="tight", dpi=300)
    plt.close(fig)


SEED = 42
