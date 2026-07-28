#!/usr/bin/env python3
"""Level 2 OD calibration: re-derive CITY_SHIFT given a REAL, city-specific
transit share, instead of the flat national DISTANCE_MODE_FRACTIONS value
CITY_SHIFT was apparently calibrated without accounting for.

Root cause (found this session, see the plan file / git history for the
full trail): external/nomad/python/preprocessing/build_od.py's transit
fraction is a flat, national, distance-band-only constant (~27-31% for
every city regardless of real local public-transport infrastructure).
CITY_SHIFT (src/green_mobility/demand/mode_choice.py) only calibrates the
car:walk:bike split among the non-transit remainder -- confirmed directly
that even Madrid's own "OMM calibrato" shift, run through the real
pipeline, does NOT reproduce the real car-share target (predicted 29.5%
vs. real 39.0%) BECAUSE the flat transit assumption baked into the
combined arithmetic doesn't match Madrid's real transit share (24.3%).

This script, for the 8 cities where CITY_TRANSIT_SHARE has a real, sourced
value (mode_choice.py, same OMM PDF/page CITY_SHIFT's own comments already
cite): loads each city's REAL MITMA distance-band trip distribution (same
data build_od.py itself uses, not a synthetic assumption), then numerically
solves for a new scalar shift (walk=bike, same convention as the existing
"OMM calibrato (scalare)" cities) such that
    P(car | non-transit, shift) * (1 - CITY_TRANSIT_SHARE[city])
reproduces the real OMM car-share target for that city.

Does NOT touch mode_choice.py -- prints/writes a before/after table for
human review. Applying the new shifts is a separate, deliberate edit once
this table has been checked.

Usage:
  python scripts/diagnostics/calibrate_transit_share_level2.py [--city SLUG]
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
NOMAD_BUILD_OD_DIR = REPO_ROOT / "external" / "nomad" / "python" / "preprocessing"
sys.path.insert(0, str(NOMAD_BUILD_OD_DIR))

from green_mobility.config import load_all_cities  # noqa: E402
from green_mobility.demand import mode_choice as mc  # noqa: E402

import build_od  # noqa: E402  (external/nomad, feature/logit-mode-choice branch)

# Real OMM car-share target, same source/row as CITY_TRANSIT_SHARE (see
# mode_choice.py comments for the exact PDF/page citation). Kept here (not
# re-derived from CITY_SHIFT, which is the thing being corrected) so this
# script's target is independent of whatever is currently in CITY_SHIFT.
CITY_CAR_SHARE_TARGET: dict[str, float] = {
    "madrid": 0.3900,
    "barcelona": 0.1967,
    "valencia": 0.4130,
    "bilbao": 0.3150,
    "palma_de_mallorca": 0.5280,
    "zaragoza": 0.4270,
    "valladolid": 0.3000,
    "a_coruna": 0.4921,
}


@dataclass
class CityDistances:
    """One row per real MITMA OD pair (post FUA-clip), weekday average."""
    dist_km: np.ndarray   # real per-row distance, same logic as build_od_rows()
    weight: np.ndarray    # trip count (mean_count), used to weight the average


def load_city_distances(city) -> CityDistances:
    """Replicates build_od.py main()'s data-loading up to od_fua (zone-level,
    FUA-clipped MITMA OD with distance/km columns) -- skips the node-level
    spatial join (not needed for a distance-weighted mode-share estimate).
    Then computes each row's dist_km exactly as build_od_rows() does."""
    data_dir = REPO_ROOT / "data"
    fua_file = data_dir / "fua" / "boundaries.gpkg"
    od_raw = data_dir / "od_raw"

    fua_row = build_od.find_fua(city.name, fua_file)
    fua_poly = fua_row.geometry

    zone_cands = list(od_raw.glob("*.gpkg")) + list(od_raw.glob("*.shp"))
    if not zone_cands:
        raise SystemExit(f"[{city.slug}] nessun file zone MITMA in {od_raw}")
    fua_zones = build_od.load_mitma_zones(zone_cands[0], fua_poly)
    fua_zone_ids = set(fua_zones["id"].astype(str))

    month_dirs = [od_raw / m for m in city.mitma_months]
    missing = [d for d in month_dirs if not d.is_dir()]
    if missing:
        raise SystemExit(f"[{city.slug}] cartelle mese non trovate: {missing}")
    viajes_files = sorted(
        [f for d in month_dirs for f in d.glob("*iajes*.csv.gz")] +
        [f for d in month_dirs for f in d.glob("*iajes*.csv")]
    )
    if not viajes_files:
        raise SystemExit(f"[{city.slug}] nessun file viajes per i mesi {city.mitma_months}")

    cols = build_od.detect_columns(viajes_files[0])

    file_years = set()
    import re as _re
    for fp in viajes_files:
        mm = _re.search(r"(\d{8})", fp.name)
        if mm:
            file_years.add(int(mm.group(1)[:4]))
    holidays_set = build_od.build_holidays_set(file_years, city.country)

    od_fua = build_od.stream_viajes(viajes_files, cols, fua_zone_ids,
                                     weekend=False, holidays_set=holidays_set)
    if od_fua is None:
        raise SystemExit(f"[{city.slug}] nessun dato weekday da stream_viajes")

    col_dist, col_km, col_trips = cols.get("distance"), cols.get("km"), cols["trips"]
    trips = od_fua[col_trips].to_numpy(dtype=float)

    if col_km and col_dist:
        km_vals = od_fua[col_km].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            real_dist = np.where((km_vals > 0) & (trips > 0), km_vals / trips, np.nan)
        bands = od_fua[col_dist].astype(str).to_numpy()
        fallback = np.array([build_od.DISTANCE_BAND_FALLBACK_KM.get(b, np.nan) for b in bands])
        dist_km = np.where(np.isnan(real_dist), fallback, real_dist)
    elif col_dist:
        bands = od_fua[col_dist].astype(str).to_numpy()
        dist_km = np.array([build_od.DISTANCE_BAND_FALLBACK_KM.get(b, np.nan) for b in bands])
    else:
        raise SystemExit(f"[{city.slug}] nessuna colonna distanza/banda nei dati MITMA")

    valid = ~np.isnan(dist_km) & (dist_km > 0) & (trips > 0)
    return CityDistances(dist_km=dist_km[valid], weight=trips[valid])


def predicted_car_share(shift: float, cd: CityDistances, transit_frac: float) -> float:
    """Weighted-average P(car | non-transit) at this shift, scaled by
    (1 - transit_frac) -- same combined arithmetic build_od_rows() applies
    (car:walk:bike rescaled to fill the non-transit remainder)."""
    ld = np.log(cd.dist_km)
    u_walk = mc.BASE_COEFS["walk"]["const"] + mc.BASE_COEFS["walk"]["log_dist"] * ld + shift
    u_bike = mc.BASE_COEFS["bike"]["const"] + mc.BASE_COEFS["bike"]["log_dist"] * ld + shift
    u_car = np.zeros_like(ld)
    m = np.maximum(np.maximum(u_walk, u_bike), u_car)
    e_car, e_walk, e_bike = np.exp(u_car - m), np.exp(u_walk - m), np.exp(u_bike - m)
    p_car = e_car / (e_car + e_walk + e_bike)
    p_car_weighted = float(np.average(p_car, weights=cd.weight))
    return p_car_weighted * (1.0 - transit_frac)


def solve_shift(cd: CityDistances, transit_frac: float, target_car_share: float) -> float:
    f = lambda s: predicted_car_share(s, cd, transit_frac) - target_car_share
    # car share is monotonically decreasing in shift (higher shift -> more
    # walk/bike utility -> less car) -- verified: f(-10) > 0 > f(15) for
    # every real distance distribution this project uses.
    lo, hi = -10.0, 15.0
    if f(lo) < 0 or f(hi) > 0:
        raise RuntimeError(
            f"target {target_car_share:.4f} not bracketed in shift range [{lo},{hi}]: "
            f"f(lo)={f(lo):.4f} f(hi)={f(hi):.4f} -- widen the bracket, don't force a result"
        )
    return brentq(f, lo, hi, xtol=1e-6)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--city", choices=sorted(mc.CITY_TRANSIT_SHARE), default=None,
                   help="solo questa città (default: tutte le 8 con CITY_TRANSIT_SHARE reale)")
    args = p.parse_args(argv)

    cities_cfg = load_all_cities()
    targets = [args.city] if args.city else sorted(mc.CITY_TRANSIT_SHARE)

    rows = []
    for slug in targets:
        if slug not in cities_cfg:
            print(f"[{slug}] non trovata in load_all_cities() -- salto")
            continue
        city = cities_cfg[slug]
        transit_frac = mc.CITY_TRANSIT_SHARE[slug]
        target_car = CITY_CAR_SHARE_TARGET[slug]
        old_shift = mc.CITY_SHIFT[slug]["walk"]  # walk==bike for all 8 "OMM calibrato" cities

        print(f"[{slug}] caricamento distribuzione distanze reale MITMA...")
        cd = load_city_distances(city)
        print(f"[{slug}] {len(cd.dist_km):,} coppie OD valide, "
              f"distanza media pesata={np.average(cd.dist_km, weights=cd.weight):.2f}km")

        old_predicted = predicted_car_share(old_shift, cd, transit_frac)
        new_shift = solve_shift(cd, transit_frac, target_car)
        new_predicted = predicted_car_share(new_shift, cd, transit_frac)

        rows.append({
            "city": slug,
            "transit_share_real": transit_frac,
            "car_share_target": target_car,
            "old_shift": old_shift,
            "old_predicted_car_share": old_predicted,
            "new_shift": new_shift,
            "new_predicted_car_share": new_predicted,
            "residual": new_predicted - target_car,
        })
        print(f"[{slug}] shift {old_shift:.4f} -> {new_shift:.4f}  "
              f"predicted car share {old_predicted:.4f} -> {new_predicted:.4f}  "
              f"(target {target_car:.4f})")

    df = pd.DataFrame(rows)
    out_dir = REPO_ROOT / "reports" / "diagnostics" / "od_calibration_level2"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "transit_share_recalibration.csv", index=False)
    print(f"\n[scritto] {out_dir / 'transit_share_recalibration.csv'}")
    print("\n" + df.to_string(index=False))
    print("\nQuesto script NON modifica mode_choice.py -- rivedi la tabella "
          "prima di aggiornare CITY_SHIFT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
