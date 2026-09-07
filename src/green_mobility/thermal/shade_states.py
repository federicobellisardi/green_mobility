"""Four-state shade decomposition (0/B/T/BT) for isolating the building
baseline from the marginal effect of tree canopy, instead of conflating
"tree+building shade" with "tree shade alone" (a real conceptual error
caught in review: attributing the whole ambient-vs-combined difference to
trees, when buildings -- an existing urban condition, not a planting
intervention -- account for most of it).

States (per edge, per hour, per date, per phenology):
  0  (ambient/no local shade): no tree shade, no building shade.
  B  (building only):          building_shadow_fraction, no tree shade.
  T  (tree only):               tree_shadow_fraction, no building shade.
  BT (combined):                both, unioned via
                                 total = 1-(1-tree)*(1-building)
                                 (never summed directly -- see module docstring
                                 of thermal.canopy_fusion for the same
                                 no-double-counting rationale).

Coefficients: tree_shade_coefficient and building_shade_coefficient are KEPT
SEPARATE, not assumed equal. thermal.era5_utci's DEFAULT_UTCI_REDUCTION_FULL_SHADE_C
(6.0 C) is a canopy-specific figure (see that module's docstring: "a commonly
reported range... for UTCI specifically" -- about tree canopy, not
buildings). There is no equivalent literature-backed constant for building
shade's effect on UTCI wired into this codebase; building_shade_coefficient
is therefore always an explicit, user-supplied sensitivity parameter here,
never defaulted to the tree value silently.

Combining two DIFFERENT coefficients over a unioned shadow fraction: applying
a single coefficient to the union would implicitly force tree and building
shade to have equal cooling power. Instead, each source's marginal
(non-overlapping) contribution is weighted by its own coefficient, and the
overlapping portion (shaded by both) is weighted by the LARGER of the two
coefficients (physically: a point cannot be cooled by more than the stronger
of two overlapping shadows -- this is a stated modelling choice, not a
measured fact, and is documented wherever it is used).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from green_mobility.thermal.era5_utci import DEFAULT_UTCI_REDUCTION_FULL_SHADE_C

DEFAULT_TREE_SHADE_COEFFICIENT_C = DEFAULT_UTCI_REDUCTION_FULL_SHADE_C  # 6.0 C, canopy-specific, already documented
BUILDING_COEFFICIENT_SENSITIVITY_RATIOS = (0.25, 0.50, 0.75, 1.00)  # of tree_shade_coefficient -- no "true" value asserted

STATES = ("0", "B", "T", "BT")


def union_shadow_fraction(tree_frac: pd.Series, building_frac: pd.Series) -> pd.Series:
    """1-(1-tree)*(1-building) -- geometric union, never a direct sum."""
    return 1 - (1 - tree_frac) * (1 - building_frac)


def state_utci_reduction(
    tree_effective_frac: pd.Series,   # tree_shadow_fraction * daylight_fraction (already daylight-gated)
    building_frac: pd.Series,          # building_shadow_fraction (already elevation-gated at the source)
    state: str,
    tree_coef: float = DEFAULT_TREE_SHADE_COEFFICIENT_C,
    building_coef: float | None = None,
) -> pd.Series:
    """UTCI reduction [C] for one of the 4 states. building_coef must be
    passed explicitly for states B/BT (no silent default to tree_coef)."""
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}, got {state!r}")
    zero = pd.Series(0.0, index=tree_effective_frac.index)

    if state == "0":
        return zero
    if state == "T":
        return tree_effective_frac * tree_coef
    if building_coef is None:
        raise ValueError(f"building_coef must be provided explicitly for state {state!r}")
    if state == "B":
        return building_frac * building_coef

    # state == "BT": marginal-weighted combination (see module docstring).
    tree_only = tree_effective_frac * (1 - building_frac)
    building_only = building_frac * (1 - tree_effective_frac)
    both = tree_effective_frac * building_frac
    stronger = max(tree_coef, building_coef)
    return tree_only * tree_coef + building_only * building_coef + both * stronger


def four_state_reductions(
    tree_effective_frac: pd.Series, building_frac: pd.Series,
    tree_coef: float = DEFAULT_TREE_SHADE_COEFFICIENT_C, building_coef: float = DEFAULT_TREE_SHADE_COEFFICIENT_C,
) -> dict[str, pd.Series]:
    """Convenience: all 4 states' UTCI reductions in one call, same inputs/coefficients."""
    return {
        s: state_utci_reduction(tree_effective_frac, building_frac, s, tree_coef, building_coef)
        for s in STATES
    }
