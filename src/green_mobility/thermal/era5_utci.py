"""Per-edge, per-hour UTCI for a city using REAL downloaded data:
  - Copernicus CDS derived-utci-historical-timeseries (ambient UTCI, already
    computed by ECMWF's own official methodology — not reconstructed here)
  - ESA WorldCover canopy fraction per edge (thermal.canopy)

This supersedes thermal.utci.edge_hour_utci's Tmrt-reconstruction path for
any city/date where a real UTCI product has actually been downloaded: we use
the real ambient value directly instead of re-deriving UTCI ourselves from
raw ERA5-Land fields (avoids compounding an independent, error-prone
computation on top of data that already IS the answer).

Known data-format gotcha (see python/download_data.py:_unwrap_if_zipped):
CDS's derived-utci-historical-timeseries dataset returns a ZIP archive even
when saved with a .nc extension and download_format=unarchived requested —
confirmed by inspection (`file` reports "Zip archive data"). Files downloaded
before that fix are auto-unwrapped on first read here too, defensively.

Shade-gating by daylight: applying a canopy-shade UTCI reduction uniformly
across all 24 hours would incorrectly cool night-time UTCI, when there is no
sun to block in the first place. Gated here by actual solar elevation (a
standard, well-established astronomical formula — not ERA5's `ssrd` field,
which is an ECMWF-accumulated quantity with a known reset-at-forecast-cycle
ambiguity at the 00 UTC timestep that would need separate verification to
use correctly; using the closed-form solar-position equation instead avoids
that risk entirely).
"""
from __future__ import annotations

import math
import zipfile
from datetime import date as date_cls
from pathlib import Path

import numpy as np
import pandas as pd

KELVIN_OFFSET = 273.15

# UTCI reduction under full tree canopy at solar noon — a commonly reported
# *range* in urban-microclimate studies (roughly 5-10C for UTCI specifically,
# a narrower band than the ~10-15C often reported for Tmrt alone, since UTCI's
# sensitivity to Tmrt is damped relative to a 1:1 relationship). Treat as a
# calibratable assumption, not a measured fact for these specific cities.
DEFAULT_UTCI_REDUCTION_FULL_SHADE_C = 6.0


def _open_utci_dataset(nc_path: Path):
    """Open a CDS UTCI netCDF, transparently unwrapping the masked-ZIP
    behaviour of derived-utci-historical-timeseries (see module docstring)
    whether the file holds a single day (24 timesteps) or a full year
    (8760 timesteps, see download_utci_year in python/download_data.py)."""
    import xarray as xr

    if zipfile.is_zipfile(nc_path):
        with zipfile.ZipFile(nc_path) as zf:
            members = [n for n in zf.namelist() if n.endswith(".nc")]
            if not members:
                raise ValueError(f"{nc_path} is a zip with no .nc member: {zf.namelist()}")
            data = zf.read(members[0])
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".nc") as tmp:
            tmp.write(data)
            tmp.flush()
            return xr.open_dataset(tmp.name).load()
    return xr.open_dataset(nc_path)


def load_ambient_utci(nc_path: Path, lat: float, lon: float, on_date: date_cls | None = None) -> pd.DataFrame:
    """Real CDS UTCI at the grid cell nearest (lat, lon), one row per hour.

    Works against both a single-day file (24 timesteps -- `on_date` not
    needed, every file has exactly one day) and a full-year file (8760
    timesteps, see load_ambient_utci_series/download_utci_year) by passing
    `on_date` to select just that day's 24 hours out of the year.

    Returns DataFrame[hour, utci_ambient_c]."""
    ds = _open_utci_dataset(nc_path)
    point = ds.sel(latitude=lat, longitude=lon, method="nearest")
    timestamps = pd.to_datetime(point["valid_time"].values)
    utci_c = point["utci"].values - KELVIN_OFFSET
    frame = pd.DataFrame({"date": timestamps.date, "hour": timestamps.hour, "utci_ambient_c": utci_c})
    if on_date is not None:
        frame = frame[frame["date"] == on_date]
        if frame.empty:
            raise ValueError(f"{nc_path} has no data for {on_date}")
    elif frame["date"].nunique() > 1:
        raise ValueError(
            f"{nc_path} spans {frame['date'].nunique()} distinct days -- pass on_date= "
            "to select one of them explicitly."
        )
    return frame[["hour", "utci_ambient_c"]].sort_values("hour").reset_index(drop=True)


def load_ambient_utci_series(nc_path: Path, lat: float, lon: float) -> pd.DataFrame:
    """Full multi-day series at the nearest grid cell to (lat, lon), e.g. for
    a whole-year file from download_utci_year. Returns
    DataFrame[date, hour, utci_ambient_c], one row per hour in the file."""
    ds = _open_utci_dataset(nc_path)
    point = ds.sel(latitude=lat, longitude=lon, method="nearest")
    timestamps = pd.to_datetime(point["valid_time"].values)
    utci_c = point["utci"].values - KELVIN_OFFSET
    return pd.DataFrame({
        "date": timestamps.date, "hour": timestamps.hour, "utci_ambient_c": utci_c,
    }).sort_values(["date", "hour"]).reset_index(drop=True)


def daily_utci_stats(series: pd.DataFrame) -> pd.DataFrame:
    """series: output of load_ambient_utci_series (date, hour, utci_ambient_c).
    Returns DataFrame[date, utci_max_c, utci_min_c, utci_mean_c] -- one row
    per calendar day, only including days with a full 24 hourly readings
    (a partial day, e.g. from a truncated download, would silently bias its
    max/min otherwise)."""
    counts = series.groupby("date")["hour"].nunique()
    complete_dates = counts[counts == 24].index
    incomplete = sorted(set(series["date"]) - set(complete_dates))
    if incomplete:
        raise ValueError(
            f"{len(incomplete)} date(s) have fewer than 24 hourly readings "
            f"(first few: {incomplete[:5]}) -- refusing to compute daily "
            "stats from a partial day; check the source file/download."
        )
    grouped = series.groupby("date")["utci_ambient_c"]
    return grouped.agg(utci_max_c="max", utci_min_c="min", utci_mean_c="mean").reset_index()


def solar_elevation_deg(lat_deg: float, lon_deg: float, d: date_cls, hour_utc: float) -> float:
    """Standard (NOAA-style) approximate solar elevation angle [degrees]
    above the horizon, for a UTC hour (fractional). Positive = sun above
    horizon (daylight); negative = below (night). Uses the standard
    day-of-year solar-declination approximation and the hour-angle/elevation
    spherical-astronomy equation (not a fitted or invented formula).
    """
    day_of_year = d.timetuple().tm_yday
    declination_rad = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))
    # Equation of time [minutes] (standard approximation)
    b = math.radians(360.0 / 364.0 * (day_of_year - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_time_hours = hour_utc + lon_deg / 15.0 + eot / 60.0
    hour_angle_rad = math.radians(15.0 * (solar_time_hours - 12.0))
    lat_rad = math.radians(lat_deg)
    elevation_rad = math.asin(
        math.sin(lat_rad) * math.sin(declination_rad)
        + math.cos(lat_rad) * math.cos(declination_rad) * math.cos(hour_angle_rad)
    )
    return math.degrees(elevation_rad)


def solar_azimuth_deg(lat_deg: float, lon_deg: float, d: date_cls, hour_utc: float) -> float:
    """Standard (NOAA-style) solar azimuth [degrees, compass bearing:
    0=North, 90=East, 180=South, 270=West] -- the direction FROM a ground
    point TOWARDS the sun, for thermal.building_shadow's ray-casting (which
    marches from a point toward this azimuth to look for a blocking
    building). Shares the same declination/hour-angle/equation-of-time
    approximation as solar_elevation_deg (not re-derived independently, so
    the two stay mutually consistent for the same lat/lon/date/hour)."""
    day_of_year = d.timetuple().tm_yday
    declination_rad = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))
    b = math.radians(360.0 / 364.0 * (day_of_year - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_time_hours = hour_utc + lon_deg / 15.0 + eot / 60.0
    hour_angle_rad = math.radians(15.0 * (solar_time_hours - 12.0))
    lat_rad = math.radians(lat_deg)

    elevation_rad = math.asin(
        math.sin(lat_rad) * math.sin(declination_rad)
        + math.cos(lat_rad) * math.cos(declination_rad) * math.cos(hour_angle_rad)
    )
    cos_az = (math.sin(declination_rad) - math.sin(lat_rad) * math.sin(elevation_rad)) / (
        math.cos(lat_rad) * math.cos(elevation_rad)
    )
    cos_az = min(1.0, max(-1.0, cos_az))  # clamp float noise at the +-1 boundary
    azimuth_rad = math.acos(cos_az)
    azimuth_deg = math.degrees(azimuth_rad)
    if math.sin(hour_angle_rad) > 0:
        azimuth_deg = 360.0 - azimuth_deg
    return azimuth_deg


def daylight_fraction_by_hour(lat_deg: float, lon_deg: float, d: date_cls) -> pd.DataFrame:
    """DataFrame[hour, daylight_fraction] for hour=0..23 (UTC), daylight_fraction
    in [0, 1]: 0 at/below horizon, scaling with sin(elevation) up to a
    reference elevation (45 deg) so the shade benefit ramps in through the
    morning and out through the evening rather than switching on/off
    instantly at the horizon."""
    rows = []
    for hour in range(24):
        elev = solar_elevation_deg(lat_deg, lon_deg, d, hour + 0.5)  # bin midpoint
        frac = max(0.0, math.sin(math.radians(elev))) / math.sin(math.radians(45.0))
        rows.append({"hour": hour, "daylight_fraction": min(1.0, frac)})
    return pd.DataFrame(rows)


def edge_hour_utci_from_era5(
    utci_nc_path: Path,
    lat: float,
    lon: float,
    date: date_cls,
    edge_shade: pd.DataFrame,
    utci_reduction_full_shade_c: float = DEFAULT_UTCI_REDUCTION_FULL_SHADE_C,
) -> pd.DataFrame:
    """Real per-edge-per-hour UTCI: ambient (real CDS product) minus a
    canopy-shade reduction gated by actual solar elevation.

    edge_shade: DataFrame[edge_id, shade_fraction] (thermal.shade).
    Returns DataFrame[edge_id, hour, utci_ambient_c, utci_c].
    """
    ambient = load_ambient_utci(utci_nc_path, lat, lon, on_date=date)
    daylight = daylight_fraction_by_hour(lat, lon, date)
    weather = ambient.merge(daylight, on="hour")

    merged = edge_shade.merge(weather, how="cross")
    merged["utci_c"] = merged["utci_ambient_c"] - (
        merged["shade_fraction"] * merged["daylight_fraction"] * utci_reduction_full_shade_c
    )
    return merged[["edge_id", "hour", "utci_ambient_c", "utci_c"]]
