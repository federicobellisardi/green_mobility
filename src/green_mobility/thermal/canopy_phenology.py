"""Evergreen vs. deciduous canopy comparison for seasonal shade attenuation.

Three scenarios to compare, not one assumed default:
  - "evergreen": full canopy shade year-round (multiplier == 1.0 always;
    this is the implicit behaviour if this module isn't used at all).
  - "deciduous": shade attenuated by a seasonal leaf-cover fraction that
    ramps in around leaf-on and back out around leaf-off, reaching ~0 in
    winter dormancy.
  - "no_distinction": identical to "evergreen" numerically, but named
    explicitly so a caller states "I am not modelling species" rather than
    that being an unstated default.

IMPORTANT — leaf-on/leaf-off timing below is NOT a validated measurement
for these 12 cities. Real phenology literature exists for Mediterranean
urban trees (e.g. studies on urbanisation effects on Madrid tree phenology,
and Platanus flowering-model studies for Badajoz/Málaga), but extracting a
single verified day-of-year per city/species from those would require
reading the full papers, not a search-result snippet -- doing that here
would be over-claiming precision. The defaults are round-number
placeholders for SENSITIVITY ANALYSIS ONLY: override leaf_on_doy/
leaf_off_doy/ramp_days once real phenology data for the species actually
planted in each city has been sourced and read in full.
"""
from __future__ import annotations

from datetime import date as date_cls

VALID_CANOPY_TYPES = ("evergreen", "deciduous", "no_distinction")

DEFAULT_LEAF_ON_DOY = 100    # ~mid-April, round-number placeholder
DEFAULT_LEAF_OFF_DOY = 305   # ~early November, round-number placeholder
DEFAULT_RAMP_DAYS = 20


def deciduous_leaf_fraction(
    day_of_year: int,
    leaf_on_doy: int = DEFAULT_LEAF_ON_DOY,
    leaf_off_doy: int = DEFAULT_LEAF_OFF_DOY,
    ramp_days: int = DEFAULT_RAMP_DAYS,
) -> float:
    """Canopy leaf-cover fraction in [0, 1] for a deciduous tree on a given
    day of year: 0 during winter dormancy, linearly ramping to 1 over
    `ramp_days` centred on leaf_on_doy, holding at 1 through summer, then
    linearly ramping back to 0 over `ramp_days` centred on leaf_off_doy."""
    half = ramp_days / 2.0

    def ramp(center: float, rising: bool) -> float:
        t = (day_of_year - center) / half
        t = max(-1.0, min(1.0, t))
        frac = (t + 1.0) / 2.0
        return frac if rising else 1.0 - frac

    if day_of_year < leaf_on_doy - half:
        return 0.0
    if day_of_year < leaf_on_doy + half:
        return ramp(leaf_on_doy, rising=True)
    if day_of_year < leaf_off_doy - half:
        return 1.0
    if day_of_year < leaf_off_doy + half:
        return ramp(leaf_off_doy, rising=False)
    return 0.0


def canopy_shade_multiplier(
    d: date_cls,
    canopy_type: str,
    leaf_on_doy: int = DEFAULT_LEAF_ON_DOY,
    leaf_off_doy: int = DEFAULT_LEAF_OFF_DOY,
    ramp_days: int = DEFAULT_RAMP_DAYS,
) -> float:
    """Multiply this into shade_fraction before computing UTCI (e.g. via
    thermal.era5_utci.edge_hour_utci_from_era5) to get the seasonal variant
    -- kept as a separate, explicit multiplication rather than a new
    parameter on that function, so it's obvious at the call site which
    canopy assumption a given result depends on."""
    if canopy_type not in VALID_CANOPY_TYPES:
        raise ValueError(f"canopy_type must be one of {VALID_CANOPY_TYPES}, got {canopy_type!r}")
    if canopy_type in ("evergreen", "no_distinction"):
        return 1.0
    return deciduous_leaf_fraction(d.timetuple().tm_yday, leaf_on_doy, leaf_off_doy, ramp_days)
