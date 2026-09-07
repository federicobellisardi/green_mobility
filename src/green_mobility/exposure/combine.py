"""Combine active-mobility flow with thermal exposure into a per-edge score.

Defined metric (ours, not an external standard — documented so it's citable
as a methodological choice): for each edge e,

    exposure_score(e) = sum_over_hours[ active_flow(e, h) * max(0, utci(e, h) - comfort_threshold_c) ]

i.e. person-trips × degrees of UTCI above the "no thermal stress" upper
bound (26°C — the standard UTCI category boundary, see thermal/utci.py),
summed across the day. This is a proxy for cumulative person-hours of heat
stress exceedance carried by people walking/biking on that edge, not a
validated epidemiological exposure measure — treat comparisons between
edges/strategies as relative, not absolute.

Symmetric cold-side sibling, same construction, mirrored around the
"slight cold" UTCI category boundary (9°C, thermal.dose_benefit's
DEFAULT_THETA_COLD_C):

    cold_exposure_score(e) = sum_over_hours[ active_flow(e, h) * max(0, cold_threshold_c - utci(e, h)) ]

Both are computed on TODAY's real (already-shaded) UTCI, not an
ambient-vs-shaded delta — see thermal.dose_benefit.edge_heat_cold_dose's
docstring for why that distinction matters for a "where to plant next"
ranking.
"""
from __future__ import annotations

import pandas as pd

from green_mobility.thermal.dose_benefit import DEFAULT_THETA_COLD_C, edge_heat_cold_dose

DEFAULT_COMFORT_THRESHOLD_C = 26.0  # UTCI "no thermal stress" upper bound (utci.org)


def compute_exposure(
    flows: pd.DataFrame,
    utci: pd.DataFrame,
    active_modes: tuple[str, ...] = ("walk", "bike"),
    comfort_threshold_c: float = DEFAULT_COMFORT_THRESHOLD_C,
    cold_threshold_c: float = DEFAULT_THETA_COLD_C,
) -> pd.DataFrame:
    """
    flows: DataFrame[edge_id, hour, mode, count] (nomad_wrapper.flows output).
    utci:  DataFrame[edge_id, hour, utci_c, ...] (thermal.utci.edge_hour_utci).

    Returns DataFrame[edge_id, flow_active, flow_total, exposure_score, cold_exposure_score].
    """
    for col in ("edge_id", "hour", "mode", "count"):
        if col not in flows.columns:
            raise ValueError(f"flows missing column '{col}'")
    for col in ("edge_id", "hour", "utci_c"):
        if col not in utci.columns:
            raise ValueError(f"utci missing column '{col}'")

    active = flows[flows["mode"].isin(active_modes)]
    merged = active.merge(utci[["edge_id", "hour", "utci_c"]], on=["edge_id", "hour"], how="left")

    dose = edge_heat_cold_dose(
        merged, edge_col="edge_id", utci_col="utci_c", n_col="count",
        theta_heat_c=comfort_threshold_c, theta_cold_c=cold_threshold_c, dt_hours=1.0,
    ).rename(columns={"heat_dose": "exposure_score", "cold_dose": "cold_exposure_score"})

    flow_active = active.groupby("edge_id", as_index=False)["count"].sum().rename(
        columns={"count": "flow_active"}
    )
    per_edge = flow_active.merge(dose, on="edge_id", how="left")
    flow_total = flows.groupby("edge_id", as_index=False)["count"].sum().rename(
        columns={"count": "flow_total"}
    )
    result = flow_total.merge(per_edge, on="edge_id", how="left")
    result["flow_active"] = result["flow_active"].fillna(0.0)
    result["exposure_score"] = result["exposure_score"].fillna(0.0)
    result["cold_exposure_score"] = result["cold_exposure_score"].fillna(0.0)
    return result
