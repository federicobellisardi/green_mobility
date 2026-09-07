"""Assembles the combined per-edge table gm intervene needs
({city}/green_mobility/exposure/{scenario}_edges.parquet, columns: edge_id,
length_m, flow_total, flow_active, exposure_score, cold_exposure_score,
vulnerability_index, utci_peak_c) from already-existing per-mode flow, UTCI
and vulnerability
artifacts. Ties together nomad_wrapper.walk_bike_stats-style flow
aggregation, thermal.exposure_calendar's canopy-only UTCI pathway, and
vulnerability.index's edge_vulnerability output -- none of which have ever
been joined into this final shape before.

Calendar dates: the same MAIN_TEXT_DATES calendar used by this week's
car-traffic sweep (scripts/launch_ltm_gatefix_calendar_sweep.sh), split into
the weekday_full/weekend_full groups OD demand itself already uses.
"""
from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

import pandas as pd

from green_mobility.config import CityConfig
from green_mobility.exposure.combine import compute_exposure
from green_mobility.thermal.exposure_calendar import compute_city_date_dose_benefit

WEEKDAY_DATES = [date_cls(2022, 1, 15), date_cls(2022, 4, 10), date_cls(2022, 6, 5)]
WEEKEND_DATES = [date_cls(2022, 7, 20), date_cls(2022, 10, 28)]
SCENARIO_DATE_GROUPS = {"weekday_full": WEEKDAY_DATES, "weekend_full": WEEKEND_DATES}


def hourly_mode_flow(walk_bike_edge_bins_path: Path) -> pd.DataFrame:
    """Returns DataFrame[edge_id, mode, hour, count] from the 15-min-bin
    walk+bike flow table (nomad_wrapper.walk_bike_routes output)."""
    bins = pd.read_parquet(walk_bike_edge_bins_path)
    bins = bins[bins["mode"].isin(["walk", "bike"])].copy()
    bins["hour"] = (bins["bin_start_s"] // 3600).astype(int)
    return (
        bins.groupby(["edge_id", "mode", "hour"], as_index=False)["flow"]
        .sum()
        .rename(columns={"flow": "count"})
    )


def car_hourly_flow(car_edge_bins_path: Path) -> pd.DataFrame:
    """Returns DataFrame[edge_id, mode="car", hour, count] from an LTM-gatefix
    car-run's edge-bins table, converting NOMAD's estimated delivered
    throughput rate (flow_veh_h, capacity-capped -- not flow_veh_h_uncapped)
    into a per-bin vehicle count comparable to walk/bike's count column."""
    car = pd.read_parquet(car_edge_bins_path, columns=["edge_id", "bin_start_s", "bin_end_s", "flow_veh_h"])
    car["hour"] = (car["bin_start_s"] // 3600).astype(int)
    car["count"] = car["flow_veh_h"] * (car["bin_end_s"] - car["bin_start_s"]) / 3600.0
    car["mode"] = "car"
    return car.groupby(["edge_id", "mode", "hour"], as_index=False)["count"].sum()


def combined_edge_utci(
    city: CityConfig, dates: list[date_cls], *, year: int, era5_dir: Path, canopy_type: str,
) -> pd.DataFrame:
    """Returns DataFrame[edge_id, hour, date, utci_c] across every date in
    `dates`, calling thermal.exposure_calendar.compute_city_date_dose_benefit
    once per date (persists each date's edge_hour_utci parquet as a side
    effect -- see that function's own docstring)."""
    frames = []
    for d in dates:
        edge_hour, _ = compute_city_date_dose_benefit(
            city, d, year=year, era5_dir=era5_dir, canopy_type=canopy_type, write_edge_hours=True,
        )
        edge_hour = edge_hour.rename(columns={"utci_shaded_c": "utci_c"})
        edge_hour["date"] = d.isoformat()
        frames.append(edge_hour[["edge_id", "hour", "date", "utci_c"]])
    return pd.concat(frames, ignore_index=True)


def build_scenario_edges_table(
    city: CityConfig,
    scenario_key: str,
    *,
    year: int = 2022,
    era5_dir: Path = Path("data/climate_raw/era5"),
    canopy_type: str = "evergreen",
    car_edge_bins_path: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Assembles the combined edges table for one (city, scenario). Returns
    (edges_df, stats) where stats records counts useful for the caller's
    manifest and sanity checks (n_edges, n_edges_excluded_zero_flow_or_no_canopy,
    n_sections_dates, with_car_flow)."""
    dates = SCENARIO_DATE_GROUPS[scenario_key]

    walk_bike_path = city.green_mobility_dir / "exposure" / f"{scenario_key}_walk_bike_edge_bins.parquet"
    flows = hourly_mode_flow(walk_bike_path)
    if car_edge_bins_path is not None:
        flows = pd.concat([flows, car_hourly_flow(car_edge_bins_path)], ignore_index=True)

    utci = combined_edge_utci(city, dates, year=year, era5_dir=era5_dir, canopy_type=canopy_type)

    exposure_by_date = []
    for d in dates:
        utci_d = utci[utci["date"] == d.isoformat()]
        exposure_by_date.append(compute_exposure(flows, utci_d))
    flow_nunique = pd.concat(
        [e.set_index("edge_id")["flow_total"] for e in exposure_by_date], axis=1
    ).nunique(axis=1).max()
    if flow_nunique != 1:
        raise AssertionError(
            f"flow_total is not date-independent within {scenario_key} (expected constant "
            f"across {len(dates)} dates, got up to {flow_nunique} distinct values per edge) -- "
            "the assumption that OD demand only varies by weekday/weekend, not by exact date, "
            "does not hold here"
        )

    exposure_score = (
        pd.concat(exposure_by_date, ignore_index=True)
        .groupby("edge_id", as_index=False)[["exposure_score", "cold_exposure_score"]].mean()
    )
    flow_cols = exposure_by_date[0][["edge_id", "flow_total", "flow_active"]]
    exposure_reduced = flow_cols.merge(exposure_score, on="edge_id")

    utci_peak = utci.groupby("edge_id", as_index=False)["utci_c"].max().rename(columns={"utci_c": "utci_peak_c"})

    # Inner join: excludes edges with zero flow in ANY mode for this scenario (the
    # dominant real cause -- e.g. Palma weekday_full drops ~31.7k/186.4k edges this
    # way, since compute_exposure's flow_total is built from flows.groupby(edge_id),
    # which only contains edges that appear at all in the walk/bike/car bins), plus
    # the small number of edges canopy_fraction.parquet has no coverage for (~4 for
    # Palma). A street nobody was simulated to use can't get an exposure_score.
    edges = pd.read_parquet(city.edges_parquet, columns=["edge_id", "length_m"])
    n_edges_total = len(edges)
    edges = edges.merge(exposure_reduced, on="edge_id", how="inner")
    edges = edges.merge(utci_peak, on="edge_id", how="inner")

    vuln_path = city.green_mobility_dir / "vulnerability" / "edge_vulnerability.parquet"
    vulnerability = pd.read_parquet(vuln_path)
    edges = edges.merge(vulnerability, on="edge_id", how="left")

    stats = {
        "n_edges": int(len(edges)),
        "n_edges_excluded_zero_flow_or_no_canopy": int(n_edges_total - len(edges)),
        "dates": [d.isoformat() for d in dates],
        "with_car_flow": car_edge_bins_path is not None,
    }
    return edges, stats
