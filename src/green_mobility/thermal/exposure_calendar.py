"""Wires the reproducible date calendar (thermal.date_selection) to real
per-edge-hour UTCI (thermal.era5_utci) and dose/benefit accounting
(thermal.dose_benefit), city-by-city and date-by-date.

Deliberately streaming, not batch: for each (city, date) this computes the
full per-edge-per-hour UTCI table, immediately joins it to that city's
walk+bike person-time and reduces it to ONE aggregate row (a
DoseBenefitResult + a λ-sweep), then discards the per-edge-hour table
before moving to the next (city, date). A naive "materialize everything
first" version would build a table with rows on the order of
n_cities × n_dates × n_edges × 24 hours -- multiple hundred million rows
for a 12-city, ~26-date calendar -- for no benefit, since the aggregate is
all downstream analysis needs.

Real per-edge-hour tables CAN optionally be persisted too (write_edge_hours=
True), but partitioned one file per (city, date) under
{city}/green_mobility/thermal/edge_hour_utci/{date}.parquet -- never one
combined cross-city table.
"""
from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

import pandas as pd

from green_mobility.config import CityConfig
from green_mobility.thermal.canopy_phenology import canopy_shade_multiplier
from green_mobility.thermal.dose_benefit import (
    DEFAULT_LAMBDA_SWEEP,
    DEFAULT_THETA_COLD_C,
    DEFAULT_THETA_HEAT_C,
    compute_dose_benefit,
    net_benefit_sweep,
)
from green_mobility.thermal.era5_utci import edge_hour_utci_from_era5
from green_mobility.thermal.shade import shade_fraction_from_canopy


def _era5_utci_year_path(era5_dir: Path, city_slug: str, year: int) -> Path:
    return era5_dir / f"utci_{city_slug}_{year}.nc"


def _load_edge_person_hours(walk_bike_edge_bins_path: Path) -> pd.DataFrame:
    """walk_bike_edge_bins_path: nomad_wrapper.walk_bike_routes's per-mode,
    per-15-min-bin table (columns: mode, edge_id, flow, person_seconds,
    travel_time_s, bin_start_s, bin_end_s). Combines walk+bike (both are
    active mobility -- the modes this whole study is about) and aggregates
    to HOURLY N[e,t] (matching UTCI's own hourly resolution): N = total
    person_seconds within that hour / 3600.
    """
    bins = pd.read_parquet(walk_bike_edge_bins_path)
    bins = bins[bins["mode"].isin(["walk", "bike"])]
    bins["hour"] = (bins["bin_start_s"] // 3600).astype(int) % 24
    hourly = bins.groupby(["edge_id", "hour"])["person_seconds"].sum().reset_index()
    hourly["n"] = hourly["person_seconds"] / 3600.0
    return hourly[["edge_id", "hour", "n"]]


def compute_city_date_dose_benefit(
    city: CityConfig,
    d: date_cls,
    *,
    year: int,
    era5_dir: Path = Path("data/climate_raw/era5"),
    canopy_type: str = "evergreen",
    write_edge_hours: bool = False,
) -> pd.DataFrame:
    """Returns (edge_hour, canopy_type): the per-edge-per-hour ambient/shaded
    UTCI table for this (city, date, canopy_type), plus the canopy_type
    passed through for convenience. Caller joins edge_hour to per-edge-hour
    walk+bike person-hours (_load_edge_person_hours) and reduces to a single
    summary row via dose_benefit_row -- that row is what's small and safe to
    concat across many (city, date) pairs into one summary table.

    Requires (raises a clear, actionable error naming the missing file if
    not yet built):
      - {city}/green_mobility/thermal/canopy_fraction.parquet (thermal.canopy)
      - data/climate_raw/era5/utci_{city}_{year}.nc (utci-year download)
      - {city}/green_mobility/exposure/<scenario>_walk_bike_edge_bins.parquet
        (nomad_wrapper.walk_bike_routes, itself requires prep-network +
        prep-demand + a NOMAD run for this city to exist)
    """
    canopy_path = city.green_mobility_dir / "thermal" / "canopy_fraction.parquet"
    if not canopy_path.exists():
        raise FileNotFoundError(
            f"{canopy_path} not found -- compute canopy fraction for {city.slug} first "
            "(thermal.canopy.edge_canopy_fraction against edges.parquet + a WorldCover raster)."
        )
    utci_path = _era5_utci_year_path(era5_dir, city.slug, year)
    if not utci_path.exists():
        raise FileNotFoundError(
            f"{utci_path} not found -- run: python python/download_data.py utci-year "
            f"--cities {city.slug} --year {year}"
        )

    canopy = pd.read_parquet(canopy_path)
    edge_shade = shade_fraction_from_canopy(canopy)
    multiplier = canopy_shade_multiplier(d, canopy_type)
    edge_shade = edge_shade.assign(shade_fraction=edge_shade["shade_fraction"] * multiplier)

    edge_hour = edge_hour_utci_from_era5(utci_path, city.lat, city.lon, d, edge_shade)
    edge_hour = edge_hour.rename(columns={"utci_c": "utci_shaded_c"})

    if write_edge_hours:
        out_dir = city.green_mobility_dir / "thermal" / "edge_hour_utci"
        out_dir.mkdir(parents=True, exist_ok=True)
        edge_hour.to_parquet(out_dir / f"{d.isoformat()}_{canopy_type}.parquet", index=False)

    return edge_hour, canopy_type  # caller joins to N[e,t] separately (see below)


def dose_benefit_row(
    city_slug: str, d: date_cls, canopy_type: str,
    edge_hour: pd.DataFrame, person_hours: pd.DataFrame,
    theta_heat_c: float = DEFAULT_THETA_HEAT_C, theta_cold_c: float = DEFAULT_THETA_COLD_C,
    lambdas: tuple[float, ...] = DEFAULT_LAMBDA_SWEEP,
) -> dict:
    """Joins one (city, date)'s edge_hour_utci to its N[e,t] (person-hours,
    active-mobility-only) and reduces to a single summary row. Edges with no
    active-mobility flow that hour contribute zero dose by construction (an
    inner join -- there is no exposure without anyone present)."""
    joined = edge_hour.merge(person_hours, on=["edge_id", "hour"], how="inner")
    result = compute_dose_benefit(
        joined, theta_heat_c=theta_heat_c, theta_cold_c=theta_cold_c,
        ambient_col="utci_ambient_c", shaded_col="utci_shaded_c", n_col="n",
    )
    sweep = net_benefit_sweep(result, lambdas)
    row = {
        "city": city_slug, "date": d.isoformat(), "canopy_type": canopy_type,
        "heat_dose_ambient": result.heat_dose_ambient, "heat_dose_shaded": result.heat_dose_shaded,
        "cold_dose_ambient": result.cold_dose_ambient, "cold_dose_shaded": result.cold_dose_shaded,
        "heat_benefit": result.heat_benefit, "cold_cost": result.cold_cost,
    }
    for lam, nb in zip(sweep["lambda"], sweep["net_benefit"]):
        row[f"net_benefit_lambda_{lam}"] = nb
    return row
