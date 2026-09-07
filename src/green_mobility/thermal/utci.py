"""Universal Thermal Climate Index (UTCI) per street edge and hour.

UTCI itself is computed by `pythermalcomfort` (MIT-licensed,
https://pypi.org/project/pythermalcomfort/), which implements the official
Bröde et al. (2012) polynomial regression — we deliberately depend on that
verified implementation rather than transcribing the ~200-term polynomial by
hand, where a copy error would silently produce wrong thermal-comfort values.

Category thresholds are the standard published UTCI stress categories
(utci.org): <-40 extreme cold, -40..-27 very strong cold, -27..-13 strong
cold, -13..0 moderate cold, 0..9 slight cold, 9..26 no thermal stress,
26..32 moderate heat, 32..38 strong heat, 38..46 very strong heat, >46
extreme heat stress.

Required inputs (per city, per hour — see README "Thermal data sources"):
  - air temperature (°C), relative humidity (%), 10m wind speed (m/s):
    Copernicus ERA5-Land hourly reanalysis is the recommended open source
    (needs a free Copernicus Climate Data Store account/API key).
  - ambient (unshaded) mean radiant temperature: ideally a real MRT product
    (e.g. derived via SOLWEIG/UMEP from ERA5-Land + solar geometry). No such
    product is bundled here; `estimate_unshaded_tmrt` is a labelled, coarse
    fallback for pipeline smoke-testing only — see its docstring.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Peak-shade MRT reduction under full tree canopy — a commonly reported
# *range* in urban-microclimate field studies (e.g. Lee & Mayer 2018; Kántor
# et al. 2018 report full-canopy midday Tmrt reductions roughly in the
# 10-15°C band for temperate/Mediterranean summer conditions), not a
# universal constant. Treat this default as a calibratable assumption, not a
# measured fact for these specific cities — override with a locally
# validated value before drawing quantitative conclusions.
DEFAULT_MRT_REDUCTION_FULL_SHADE_C = 12.0

_CATEGORY_BINS = [-100, -40, -27, -13, 0, 9, 26, 32, 38, 46, 200]
_CATEGORY_LABELS = [
    "extreme_cold", "very_strong_cold", "strong_cold", "moderate_cold",
    "slight_cold", "no_stress", "moderate_heat", "strong_heat",
    "very_strong_heat", "extreme_heat",
]


def utci_from_weather(tdb_c, tr_c, v_ms, rh_pct):
    """Thin wrapper on pythermalcomfort.models.utci. Accepts scalars or
    numpy arrays (pythermalcomfort vectorises internally)."""
    try:
        from pythermalcomfort.models import utci
    except ImportError as exc:
        raise ImportError(
            "pythermalcomfort is required for UTCI computation "
            "(pip install pythermalcomfort). Not bundled by default since "
            "it's only needed by the thermal module."
        ) from exc
    result = utci(tdb=tdb_c, tr=tr_c, v=v_ms, rh=rh_pct)
    # pythermalcomfort returns a dataclass-like object with `.utci` for
    # vector input, or a plain float for scalar input depending on version.
    return getattr(result, "utci", result)


def utci_category(utci_c) -> np.ndarray:
    values = np.atleast_1d(np.asarray(utci_c, dtype=float))
    idx = np.digitize(values, _CATEGORY_BINS) - 1
    idx = np.clip(idx, 0, len(_CATEGORY_LABELS) - 1)
    return np.array(_CATEGORY_LABELS)[idx]


def apply_shade(
    tr_ambient_c, shade_fraction, mrt_reduction_full_shade_c: float = DEFAULT_MRT_REDUCTION_FULL_SHADE_C
):
    """Linear discount of ambient (unshaded) mean radiant temperature by
    shade fraction. See module docstring for the caveat on
    mrt_reduction_full_shade_c."""
    return np.asarray(tr_ambient_c) - np.asarray(shade_fraction) * mrt_reduction_full_shade_c


def estimate_unshaded_tmrt(ta_c, hour: int) -> np.ndarray:
    """Coarse, labelled fallback ONLY for smoke-testing the pipeline without
    a real MRT product: adds a triangular daytime solar-load offset peaking
    at +25°C over air temperature at solar noon (~13:00 local) and 0 at
    night, matching the rough shape (not a validated magnitude) reported for
    unshaded paved surfaces in Mediterranean summer conditions. Do NOT use
    this for any real conclusion about these cities — replace with an actual
    MRT product (see module docstring) first.
    """
    daylight = np.clip(1.0 - abs(hour - 13) / 7.0, 0.0, 1.0)
    return np.asarray(ta_c, dtype=float) + 25.0 * daylight


def edge_hour_utci(
    weather_by_hour: pd.DataFrame,
    edge_shade: pd.DataFrame,
    mrt_reduction_full_shade_c: float = DEFAULT_MRT_REDUCTION_FULL_SHADE_C,
) -> pd.DataFrame:
    """
    weather_by_hour: DataFrame[hour, ta_c, rh_pct, wind_ms, tr_ambient_c]
        (one row per hour 0..23, city-wide — see README for how to build this
        from ERA5-Land).
    edge_shade: DataFrame[edge_id, shade_fraction] (thermal/shade.py).

    Returns DataFrame[edge_id, hour, utci_c, utci_category].
    """
    required = {"hour", "ta_c", "rh_pct", "wind_ms", "tr_ambient_c"}
    missing = required - set(weather_by_hour.columns)
    if missing:
        raise ValueError(f"weather_by_hour missing columns: {sorted(missing)}")

    merged = edge_shade.merge(weather_by_hour, how="cross")
    merged["tr_c"] = apply_shade(
        merged["tr_ambient_c"], merged["shade_fraction"], mrt_reduction_full_shade_c
    )
    merged["utci_c"] = utci_from_weather(
        merged["ta_c"].to_numpy(),
        merged["tr_c"].to_numpy(),
        merged["wind_ms"].to_numpy(),
        merged["rh_pct"].to_numpy(),
    )
    merged["utci_category"] = utci_category(merged["utci_c"].to_numpy())
    return merged[["edge_id", "hour", "utci_c", "utci_category"]]
