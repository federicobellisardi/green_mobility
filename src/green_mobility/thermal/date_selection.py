"""Reproducible date-selection pipeline for the 12-city thermal-exposure
study (see the project's Nature Cities scale-up plan): builds a calendar of
representative days from the full 2022 hourly UTCI series, instead of
picking dates by eye.

Two independent things this module does with the full-year series:
  1. Monthly representative days -- for each month, the day whose daily-max
     UTCI is closest to that month's median (a "medoid": an actual observed
     day, not an interpolated one). Two variants:
       - per-city medoid (Option A): most climatically representative for
         each city individually, but a different calendar date per city.
       - common medoid (Option B): one calendar date per month shared by
         all 12 cities, chosen to minimize
             Σ_c |UTCImax(c,d) − median_m(UTCImax_c)| / IQR_m(c)
         across cities c for candidate day d in month m -- directly
         comparable across cities (same weather-context date), at the cost
         of being less individually representative for any one city.
  2. Verification of candidate summer/heatwave dates against each city's OWN
     year-round UTCI percentile distribution -- a nationally-declared
     AEMET heatwave day is not automatically "extreme" in every city, and a
     day outside any declared heatwave is not automatically "normal" (a
     regional heat spike can be locally extreme without being severe enough
     to make the national declaration). See classify_city_day.

Official 2022 heatwave dates (source: AEMET's own "Informe climático de
verano de 2022", the agency's published seasonal summary -- not invented):
  - 12-18 June 2022 (2nd most premature on record)
  - 9-26 July 2022 (most intense ever recorded at time of writing, longest
    18-day span, affected a record 43 provinces)
  - 30 July - 15 August 2022 (17 days, third longest on record)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path

import numpy as np
import pandas as pd

from green_mobility.thermal.era5_utci import daily_utci_stats, load_ambient_utci_series
from green_mobility.thermal.utci import utci_category

# Source: AEMET, "Informe climático de verano de 2022" (aemet.es) -- the
# three heatwaves ("olas de calor") of summer 2022, by official start/end
# date (inclusive). Do NOT extend or shrink these without re-checking that
# report; they are a specific, citable, national declaration, not a rule
# derived from a temperature threshold in this codebase.
OFFICIAL_HEATWAVES_2022 = [
    (date_cls(2022, 6, 12), date_cls(2022, 6, 18)),
    (date_cls(2022, 7, 9), date_cls(2022, 7, 26)),
    (date_cls(2022, 7, 30), date_cls(2022, 8, 15)),
]


def is_official_heatwave(d: date_cls) -> bool:
    return any(start <= d <= end for start, end in OFFICIAL_HEATWAVES_2022)


def season_of(d: date_cls) -> str:
    """Meteorological (not astronomical) season -- standard convention
    (DJF/MAM/JJA/SON), consistent with how AEMET's own seasonal reports
    (e.g. the summer-2022 report cited above) define "summer" as
    June-July-August, not the solstice-to-equinox astronomical definition."""
    return {12: "DJF", 1: "DJF", 2: "DJF",
            3: "MAM", 4: "MAM", 5: "MAM",
            6: "JJA", 7: "JJA", 8: "JJA",
            9: "SON", 10: "SON", 11: "SON"}[d.month]


def load_year_daily_stats(nc_path: Path, lat: float, lon: float) -> pd.DataFrame:
    """DataFrame[date, utci_max_c, utci_min_c, utci_mean_c] for a full-year
    UTCI file (see python/download_data.py's utci-year subcommand)."""
    series = load_ambient_utci_series(nc_path, lat, lon)
    return daily_utci_stats(series)


def monthly_medoid_per_city(daily: pd.DataFrame) -> pd.DataFrame:
    """Option A. daily: DataFrame[date, utci_max_c] for ONE city, full year.
    Returns DataFrame[month, date, utci_max_c, abs_dev_from_median] -- the
    day in each month whose utci_max_c is closest to that month's median
    (ties broken by earliest date, for determinism)."""
    df = daily.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.month
    rows = []
    for month, group in df.groupby("month"):
        median = group["utci_max_c"].median()
        dev = (group["utci_max_c"] - median).abs()
        best_idx = dev.sort_values(kind="stable").index[0]
        rows.append({
            "month": month, "date": group.loc[best_idx, "date"],
            "utci_max_c": group.loc[best_idx, "utci_max_c"],
            "month_median_utci_max_c": median,
            "abs_dev_from_median": dev.loc[best_idx],
        })
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


def monthly_medoid_common(daily_by_city: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Option B. daily_by_city: {city_slug: DataFrame[date, utci_max_c]}
    (same full year for every city -- same calendar dates available).

    For each month, picks the ONE calendar date (common to all cities) that
    minimizes Σ_c |UTCImax(c,d) − median_m(UTCImax_c)| / IQR_m(c), i.e. the
    day that is, in aggregate and scale-normalized per city, closest to
    "typical" everywhere at once. IQR is used (not std) to normalize since
    it is more robust to the extreme tail asymmetry UTCI max shows in
    Mediterranean-summer cities (long hot tail, short cold tail).
    """
    slugs = sorted(daily_by_city)
    merged = None
    for slug in slugs:
        d = daily_by_city[slug][["date", "utci_max_c"]].rename(columns={"utci_max_c": slug})
        merged = d if merged is None else merged.merge(d, on="date", how="inner")
    if merged is None or merged.empty:
        raise ValueError("no overlapping dates across cities")
    merged["month"] = pd.to_datetime(merged["date"]).dt.month

    rows = []
    for month, group in merged.groupby("month"):
        total_distance = pd.Series(0.0, index=group.index)
        per_city_metrics = {}
        for slug in slugs:
            median = group[slug].median()
            q1, q3 = group[slug].quantile([0.25, 0.75])
            iqr = q3 - q1
            if iqr == 0:
                iqr = group[slug].std() or 1.0  # degenerate month fallback, avoid div-by-zero
            total_distance = total_distance + (group[slug] - median).abs() / iqr
            per_city_metrics[slug] = {"median": median, "iqr": iqr}
        best_idx = total_distance.sort_values(kind="stable").index[0]
        row = {
            "month": month, "date": group.loc[best_idx, "date"],
            "total_normalized_distance": total_distance.loc[best_idx],
        }
        for slug in slugs:
            row[f"utci_max_c_{slug}"] = group.loc[best_idx, slug]
        rows.append(row)
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


@dataclass(frozen=True)
class CityDayClassification:
    city: str
    date: date_cls
    season: str
    official_heatwave: bool
    utci_max_c: float
    utci_min_c: float
    local_max_percentile: float   # utci_max_c's percentile within THIS city's full-year utci_max distribution
    local_min_percentile: float   # utci_min_c's percentile within THIS city's full-year utci_min distribution
    moderate_heat: bool           # local_max_percentile in [75, 90)
    extreme_heat: bool            # local_max_percentile >= 90
    extreme_heatwave: bool        # official_heatwave AND extreme_heat
    cold_stress_category: str     # utci_category() of utci_min_c (official utci.org bins)
    heat_stress_category: str     # utci_category() of utci_max_c


def classify_city_day(city: str, d: date_cls, year_stats: pd.DataFrame) -> CityDayClassification:
    """year_stats: this city's full DataFrame[date, utci_max_c, utci_min_c, ...]
    for the whole year (output of load_year_daily_stats), used both to look
    up d's own values and as the reference distribution for its percentile."""
    row = year_stats[year_stats["date"] == d]
    if row.empty:
        raise ValueError(f"{city}: {d} not found in year_stats (check the UTCI download covers it)")
    utci_max = float(row["utci_max_c"].iloc[0])
    utci_min = float(row["utci_min_c"].iloc[0])

    max_pct = float((year_stats["utci_max_c"] <= utci_max).mean() * 100)
    min_pct = float((year_stats["utci_min_c"] <= utci_min).mean() * 100)

    official = is_official_heatwave(d)
    moderate_heat = 75 <= max_pct < 90
    extreme_heat = max_pct >= 90
    extreme_heatwave = official and extreme_heat

    return CityDayClassification(
        city=city, date=d, season=season_of(d), official_heatwave=official,
        utci_max_c=utci_max, utci_min_c=utci_min,
        local_max_percentile=max_pct, local_min_percentile=min_pct,
        moderate_heat=moderate_heat, extreme_heat=extreme_heat,
        extreme_heatwave=extreme_heatwave,
        cold_stress_category=str(utci_category(utci_min)[0]),
        heat_stress_category=str(utci_category(utci_max)[0]),
    )


def classify_all(cities: list[str], dates: list[date_cls], year_stats_by_city: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = [
        classify_city_day(city, d, year_stats_by_city[city]).__dict__
        for city in cities for d in dates
        if d in set(year_stats_by_city[city]["date"])
    ]
    return pd.DataFrame(rows)


def dedupe_calendar(monthly_dates: list[date_cls], extreme_dates: list[date_cls]) -> list[date_cls]:
    """Union of both sets, de-duplicated, sorted -- a date already selected
    as a monthly medoid is not re-added as a separate extreme-event date."""
    return sorted(set(monthly_dates) | set(extreme_dates))


# Candidate dates supplied by the user for verification against AEMET +
# local UTCI percentiles -- NOT assumed correct just because they sit
# outside/inside the official windows (see classify_city_day). Real
# verification result (computed against the actual 2022 UTCI series, see
# the project's date-selection report): 4 of the 5 "summer normal"
# candidates are locally extreme_heat (percentile >= 90) in at least one of
# the 12 cities despite being outside every OFFICIAL_HEATWAVES_2022 window
# (2022-07-02 in Valladolid: 94.5th pct; 2022-07-05 in Barcelona: 90.7th;
# 2022-08-20 in Sevilla: 92.3rd; 2022-08-27 in A Coruña: 95.3rd) -- so
# "normal" must be read as "city-day", never as a blanket label for the
# calendar date across all 12 cities.
SUMMER_NORMAL_CANDIDATES_2022 = [
    date_cls(2022, 6, 5), date_cls(2022, 7, 2), date_cls(2022, 7, 5),
    date_cls(2022, 8, 20), date_cls(2022, 8, 27),
]
HEATWAVE_JULY_CANDIDATES_2022 = [
    date_cls(2022, 7, 10), date_cls(2022, 7, 14), date_cls(2022, 7, 18),
    date_cls(2022, 7, 22), date_cls(2022, 7, 25),
]
HEATWAVE_JULY_AUGUST_CANDIDATES_2022 = [
    date_cls(2022, 7, 31), date_cls(2022, 8, 3), date_cls(2022, 8, 6),
    date_cls(2022, 8, 10), date_cls(2022, 8, 14),
]


def build_calendar(
    year_stats_by_city: dict[str, pd.DataFrame],
    *,
    include_monthly: bool = True,
    medoid_mode: str = "common",
    include_heatwaves: bool = True,
    manual_dates: list[date_cls] | None = None,
) -> tuple[list[date_cls], pd.DataFrame]:
    """Orchestrates the whole selection: monthly medoids (per medoid_mode)
    + verified heatwave/summer-normal candidates + manual overrides,
    deduplicated. Returns (final_dates, classification_df) where
    classification_df is one row per (city, date) with the full
    CityDayClassification fields -- the "come procedere" evidence table.

    medoid_mode: "common" (Option B, recommended -- one date per month
    shared by all cities) or "per_city" (Option A -- used as the robustness
    check, produces a different date per city per month).
    """
    if medoid_mode not in {"common", "per_city"}:
        raise ValueError(f"medoid_mode must be 'common' or 'per_city', got {medoid_mode!r}")

    cities = sorted(year_stats_by_city)
    monthly_dates: list[date_cls] = []
    if include_monthly:
        if medoid_mode == "common":
            daily_by_city = {c: year_stats_by_city[c][["date", "utci_max_c"]] for c in cities}
            option_b = monthly_medoid_common(daily_by_city)
            monthly_dates = [pd.Timestamp(d).date() for d in option_b["date"]]
        else:
            for city in cities:
                medoids = monthly_medoid_per_city(year_stats_by_city[city][["date", "utci_max_c"]])
                monthly_dates.extend(pd.Timestamp(d).date() for d in medoids["date"])

    extreme_dates: list[date_cls] = []
    if include_heatwaves:
        extreme_dates = (
            SUMMER_NORMAL_CANDIDATES_2022
            + HEATWAVE_JULY_CANDIDATES_2022
            + HEATWAVE_JULY_AUGUST_CANDIDATES_2022
        )

    final_dates = dedupe_calendar(monthly_dates, extreme_dates + list(manual_dates or []))
    classification = classify_all(cities, final_dates, year_stats_by_city)
    return final_dates, classification
