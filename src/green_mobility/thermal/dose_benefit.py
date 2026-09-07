"""Heat/cold thermal-dose accounting and shade benefit/cost, per edge-hour.

Definitions (θ_heat, θ_cold are UTCI thresholds in °C):

  heat_dose = Σ_{e,t} N[e,t] × max(UTCI[e,t] − θ_heat, 0) × Δt
  cold_dose = Σ_{e,t} N[e,t] × max(θ_cold − UTCI[e,t], 0) × Δt

  heat_benefit = heat_dose(ambient) − heat_dose(shaded)   -- shade's cooling payoff
  cold_cost    = cold_dose(shaded) − cold_dose(ambient)   -- shade's cold-stress penalty
  net_benefit(λ) = heat_benefit − λ × cold_cost

Default thresholds (θ_heat=26°C, θ_cold=9°C) reuse the SAME official
UTCI stress-category boundaries already defined in thermal.utci
(utci.org: 9°C = onset of "slight cold" stress, 26°C = onset of "moderate
heat" stress) -- not new invented numbers, and both are ordinary function
parameters so a different population/activity threshold can be substituted
without editing this module.

Per the project brief: never assume a single λ. net_benefit_sweep reports
a range of λ values; heat_benefit and cold_cost are also always available
un-combined (two separate objectives), for anyone who wants to reason about
each on its own rather than through one scalar trade-off.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DEFAULT_THETA_HEAT_C = 26.0  # utci.py's "moderate_heat" category lower bound
DEFAULT_THETA_COLD_C = 9.0   # utci.py's "slight_cold" category upper bound
DEFAULT_LAMBDA_SWEEP = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)


def heat_dose(
    df: pd.DataFrame, theta_heat_c: float = DEFAULT_THETA_HEAT_C,
    utci_col: str = "utci_c", n_col: str = "n", dt_hours: float = 1.0,
) -> float:
    excess = (df[utci_col] - theta_heat_c).clip(lower=0)
    return float((df[n_col] * excess * dt_hours).sum())


def cold_dose(
    df: pd.DataFrame, theta_cold_c: float = DEFAULT_THETA_COLD_C,
    utci_col: str = "utci_c", n_col: str = "n", dt_hours: float = 1.0,
) -> float:
    deficit = (theta_cold_c - df[utci_col]).clip(lower=0)
    return float((df[n_col] * deficit * dt_hours).sum())


@dataclass(frozen=True)
class DoseBenefitResult:
    heat_dose_ambient: float
    heat_dose_shaded: float
    cold_dose_ambient: float
    cold_dose_shaded: float

    @property
    def heat_benefit(self) -> float:
        return self.heat_dose_ambient - self.heat_dose_shaded

    @property
    def cold_cost(self) -> float:
        return self.cold_dose_shaded - self.cold_dose_ambient

    def net_benefit(self, lam: float) -> float:
        return self.heat_benefit - lam * self.cold_cost


def compute_dose_benefit(
    df: pd.DataFrame,
    *,
    theta_heat_c: float = DEFAULT_THETA_HEAT_C,
    theta_cold_c: float = DEFAULT_THETA_COLD_C,
    ambient_col: str = "utci_ambient_c",
    shaded_col: str = "utci_c",
    n_col: str = "n",
    dt_hours: float = 1.0,
) -> DoseBenefitResult:
    """df: one row per (edge, hour) with columns [n_col, ambient_col, shaded_col]
    (e.g. thermal.era5_utci.edge_hour_utci_from_era5's output, joined to
    per-edge-hour walk+bike person-counts from nomad_wrapper.walk_bike_routes)."""
    required = {ambient_col, shaded_col, n_col}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"df missing columns: {sorted(missing)}")
    return DoseBenefitResult(
        heat_dose_ambient=heat_dose(df, theta_heat_c, ambient_col, n_col, dt_hours),
        heat_dose_shaded=heat_dose(df, theta_heat_c, shaded_col, n_col, dt_hours),
        cold_dose_ambient=cold_dose(df, theta_cold_c, ambient_col, n_col, dt_hours),
        cold_dose_shaded=cold_dose(df, theta_cold_c, shaded_col, n_col, dt_hours),
    )


def edge_heat_cold_dose(
    df: pd.DataFrame,
    edge_col: str = "edge_id",
    utci_col: str = "utci_c",
    n_col: str = "n",
    theta_heat_c: float = DEFAULT_THETA_HEAT_C,
    theta_cold_c: float = DEFAULT_THETA_COLD_C,
    dt_hours: float = 1.0,
) -> pd.DataFrame:
    """Per-edge heat_dose/cold_dose on ONE utci column (today's real,
    already-shaded scenario) -- same arithmetic as heat_dose()/cold_dose()
    above, grouped by edge instead of reduced over the whole frame.

    Deliberately NOT built on ambient-vs-shaded DoseBenefitResult:
    heat_benefit/cold_cost there measure value already delivered by
    EXISTING canopy, which is exactly zero by construction on edges with
    little/no canopy today -- precisely the edges a "where to plant next"
    ranking most needs to surface. This function instead measures the
    burden present right now, the same way exposure.combine.exposure_score
    already does for heat alone -- a directly comparable cold-side twin.

    Returns DataFrame[edge_col, heat_dose, cold_dose]."""
    heat = (df[utci_col] - theta_heat_c).clip(lower=0) * df[n_col] * dt_hours
    cold = (theta_cold_c - df[utci_col]).clip(lower=0) * df[n_col] * dt_hours
    return (
        df.assign(_heat=heat, _cold=cold)
        .groupby(edge_col, as_index=False)[["_heat", "_cold"]]
        .sum()
        .rename(columns={"_heat": "heat_dose", "_cold": "cold_dose"})
    )


def net_benefit_sweep(result: DoseBenefitResult, lambdas: tuple[float, ...] = DEFAULT_LAMBDA_SWEEP) -> pd.DataFrame:
    """Reports net_benefit across a RANGE of λ, plus the two un-combined
    objectives, instead of committing to one λ. Never call this the answer
    -- λ encodes a value judgement (how much a unit of added cold stress is
    "worth" relative to a unit of avoided heat stress) that this codebase
    does not have grounds to pick on the study's behalf."""
    return pd.DataFrame({
        "lambda": lambdas,
        "heat_benefit": [result.heat_benefit] * len(lambdas),
        "cold_cost": [result.cold_cost] * len(lambdas),
        "net_benefit": [result.net_benefit(lam) for lam in lambdas],
    })
