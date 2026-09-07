"""Edge ranking strategies for canopy/shade intervention comparison.

Each strategy takes the same combined per-edge table and returns edge_ids
ordered from "plant first" to "plant last". `allocate_budget` then walks
that order accumulating length_m until it reaches q% of total network
length (the intervention quantum agreed with the user — a share of
street-edge length, not edge count or a tree-count budget).

Expected columns on `edges`:
  edge_id, length_m, flow_total, flow_active, exposure_score,
  cold_exposure_score, vulnerability_index, utci_peak_c
(all but edge_id/length_m are produced upstream by exposure.combine /
thermal.utci / vulnerability.index — see cli.py `intervene` command).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = (
    "edge_id", "length_m", "flow_total", "flow_active",
    "exposure_score", "cold_exposure_score", "vulnerability_index", "utci_peak_c",
)

DEFAULT_LAMBDA = 1.0  # the symmetric, "no preference" value already in
# thermal.dose_benefit.DEFAULT_LAMBDA_SWEEP -- reused rather than inventing a
# new number. A value judgement, not a physical fact: dose_benefit.py's own
# docstring is explicit this codebase has no grounds to pick one on the
# study's behalf. This is a starting default for the ranking below, not a
# claim -- thermal.dose_benefit.net_benefit_sweep remains the full
# lambda-sensitivity view for the paper.


def _check_columns(edges: pd.DataFrame) -> None:
    missing = set(REQUIRED_COLUMNS) - set(edges.columns)
    if missing:
        raise ValueError(f"edges table missing columns: {sorted(missing)}")


def _minmax(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(0.5, index=s.index)
    return (s - lo) / (hi - lo)


def rank_random(edges: pd.DataFrame, seed: int) -> pd.Index:
    _check_columns(edges)
    rng = np.random.default_rng(seed)
    return pd.Index(rng.permutation(edges["edge_id"].to_numpy()))


def rank_thermal_hotspot(edges: pd.DataFrame) -> pd.Index:
    _check_columns(edges)
    return edges.sort_values("utci_peak_c", ascending=False)["edge_id"]


def rank_flow(edges: pd.DataFrame) -> pd.Index:
    _check_columns(edges)
    return edges.sort_values("flow_total", ascending=False)["edge_id"]


def rank_active_mobility(edges: pd.DataFrame) -> pd.Index:
    _check_columns(edges)
    return edges.sort_values("flow_active", ascending=False)["edge_id"]


def _net_benefit(edges: pd.DataFrame, lam: float) -> pd.Series:
    """Heat burden addressable here, minus lam x cold burden already present
    here -- both computed on TODAY's shaded UTCI (exposure.combine), not an
    existing-canopy attribution delta. lam=1.0 treats a degree-person-hour
    of avoided heat and a degree-person-hour of added cold stress as equally
    weighted, a value judgement this module does not claim is uniquely
    correct — see DEFAULT_LAMBDA."""
    return edges["exposure_score"] - lam * edges["cold_exposure_score"]


def rank_optimized(edges: pd.DataFrame, lam: float = DEFAULT_LAMBDA) -> pd.Index:
    """Highest active-mobility net heat/cold benefit first — targets where
    shade removes the most person-hours of heat-stress exceedance per km
    planted, net of any cold-stress it would add."""
    _check_columns(edges)
    return edges.assign(_score=_net_benefit(edges, lam)).sort_values("_score", ascending=False)["edge_id"]


def rank_equity(edges: pd.DataFrame, equity_weight: float = 0.5, lam: float = DEFAULT_LAMBDA) -> pd.Index:
    """Blend of normalized net heat/cold benefit and normalized
    vulnerability_index (equal weight by default) — same ranking as
    `optimized`, but edges serving more vulnerable populations move up the
    list."""
    _check_columns(edges)
    if not (0.0 <= equity_weight <= 1.0):
        raise ValueError("equity_weight must be in [0, 1]")
    net = _net_benefit(edges, lam)
    score = (1 - equity_weight) * _minmax(net) + equity_weight * _minmax(edges["vulnerability_index"])
    return edges.assign(_equity_score=score).sort_values("_equity_score", ascending=False)["edge_id"]


STRATEGY_FUNCS = {
    "random": lambda edges, seed: rank_random(edges, seed),
    "thermal_hotspot": lambda edges, seed: rank_thermal_hotspot(edges),
    "flow": lambda edges, seed: rank_flow(edges),
    "active_mobility": lambda edges, seed: rank_active_mobility(edges),
    "optimized": lambda edges, seed: rank_optimized(edges),
    "equity": lambda edges, seed: rank_equity(edges),
}


def rank_edges(strategy: str, edges: pd.DataFrame, seed: int = 42) -> pd.Index:
    if strategy not in STRATEGY_FUNCS:
        raise ValueError(f"unknown strategy '{strategy}', expected one of {list(STRATEGY_FUNCS)}")
    return STRATEGY_FUNCS[strategy](edges, seed)


def allocate_budget(ranked_edge_ids: pd.Index, edges: pd.DataFrame, q: float) -> pd.Series:
    """Walk ranked_edge_ids in order, accumulating length_m, until cumulative
    length reaches q * total_length_m. Returns a boolean Series indexed like
    `edges` (True = selected for this intervention)."""
    if not (0.0 < q <= 1.0):
        raise ValueError("q must be in (0, 1]")
    length_by_id = edges.set_index("edge_id")["length_m"]
    total_length = length_by_id.sum()
    target = q * total_length

    ordered_lengths = length_by_id.reindex(ranked_edge_ids)
    cumulative = ordered_lengths.cumsum()
    selected_ids = set(ordered_lengths.index[cumulative <= target])
    # Always include at least one edge (so q>0 with a coarse network never
    # silently selects nothing).
    if not selected_ids and len(ranked_edge_ids) > 0:
        selected_ids = {ranked_edge_ids[0]}

    return edges["edge_id"].isin(selected_ids)
