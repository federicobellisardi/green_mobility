"""Per-edge shade fraction — the input the UTCI module uses to discount the
mean radiant temperature term.

v1 simplification (explicitly documented, not hidden): shade_fraction is
taken directly from tree-canopy fraction (thermal/canopy.py). Real solar-
position-dependent building-shadow casting (which matters a lot in dense
urban canyons, especially outside midday) is genuinely out of scope for this
version — it needs a 3D building-height model + sun-position raytracing
(e.g. UMEP/SOLWEIG-style), which is a substantial separate modelling effort.
This is flagged here and in README.md "Limitations", not silently assumed
away: `shade_fraction` should be read as "canopy-only shade", an
underestimate of true midday shade wherever building shadow also matters.
"""
from __future__ import annotations

import pandas as pd


def shade_fraction_from_canopy(canopy: pd.DataFrame) -> pd.DataFrame:
    """canopy: DataFrame[edge_id, canopy_fraction] (see thermal/canopy.py).
    Returns DataFrame[edge_id, shade_fraction] (currently == canopy_fraction).
    """
    out = canopy[["edge_id"]].copy()
    out["shade_fraction"] = canopy["canopy_fraction"].clip(0.0, 1.0)
    return out
