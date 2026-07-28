"""Subprocess wrappers around NOMAD's own preprocessing scripts.

These call external/nomad/python/{download_data,preprocessing}/*.py exactly
as documented in external/nomad/README.md "Real-world case study: Palma de
Mallorca" — nothing here reimplements OSM clipping, graph simplification,
MITMA parsing, or OD generation; it only supplies --city/--data-dir/--out-dir
so the same NOMAD-owned code runs against our 4 cities' data.

Only `run_simplify_osm` needs an interpreter where NOMAD's compiled
`_nomad_core` module is importable (it calls `OsmLoader` directly); the rest
only need pandas/geopandas/requests and can run under this repo's own env.
Override the interpreter used for that one call with the GM_NOMAD_PYTHON
environment variable if this repo's env and NOMAD's build env differ.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from green_mobility.config import DATA_DIR, NOMAD_DIR, CityConfig

DOWNLOAD_MITMA_SCRIPT = NOMAD_DIR / "python" / "download_data" / "download_mitma.py"
SIMPLIFY_OSM_SCRIPT = NOMAD_DIR / "python" / "preprocessing" / "simplify_osm.py"

# NOMAD's own download_mitma.py only recognizes zone files packaged as a
# single .zip or .gpkg (select_zones_files(): `if not low.endswith((".zip",
# ".gpkg")): continue`). MITMA's real RSS feed (confirmed live, 2026-07)
# instead publishes the district zonification as loose shapefile-part files
# under zonificacion/zonificacion_distritos/ — never zipped — so NOMAD's own
# downloader silently finds zero candidates. This is a small, self-contained
# addition in our own wrapper (not a NOMAD change) that fetches those exact
# confirmed-live files directly; build_od.py itself only cares that a
# *.shp lands in data/od_raw/, not how it got there.
MITMA_ROOT = "https://movilidad-opendata.mitma.es/"
MITMA_ZONIFICACION_DISTRITOS_EXTS = ("shp", "dbf", "shx", "prj", "cpg", "qpj")
BUILD_OD_SCRIPT = NOMAD_DIR / "python" / "preprocessing" / "build_od.py"

OD_RAW_DIR = DATA_DIR / "od_raw"


class NomadScriptError(RuntimeError):
    def __init__(self, script: Path, result: subprocess.CompletedProcess):
        self.result = result
        super().__init__(
            f"{script.name} failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def _nomad_python() -> str:
    return os.environ.get("GM_NOMAD_PYTHON", sys.executable)


def _run(python_exe: str, script: Path, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [python_exe, str(script), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NomadScriptError(script, result)
    return result


def download_mitma_month(city: CityConfig, month: str) -> subprocess.CompletedProcess:
    OD_RAW_DIR.mkdir(parents=True, exist_ok=True)
    return _run(
        sys.executable,
        DOWNLOAD_MITMA_SCRIPT,
        ["--month", month, "--out-dir", str(OD_RAW_DIR)],
    )


def _download_mitma_zonificacion_distritos_shapefile() -> list[Path]:
    """Fallback for NOMAD's own --zones (see module-level comment): fetch the
    real, confirmed-live loose shapefile parts directly."""
    import requests

    OD_RAW_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for ext in MITMA_ZONIFICACION_DISTRITOS_EXTS:
        url = f"{MITMA_ROOT}zonificacion/zonificacion_distritos/zonificacion_distritos.{ext}"
        dest = OD_RAW_DIR / f"zonificacion_distritos.{ext}"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        downloaded.append(dest)
    return downloaded


def download_mitma_zones(force: bool = False) -> subprocess.CompletedProcess | None:
    """MITMA zonification is shared across all cities — download once."""
    already_have = list(OD_RAW_DIR.glob("*.shp")) + list(OD_RAW_DIR.glob("*.gpkg"))
    if already_have and not force:
        return None
    OD_RAW_DIR.mkdir(parents=True, exist_ok=True)
    result = _run(
        sys.executable, DOWNLOAD_MITMA_SCRIPT, ["--zones", "--out-dir", str(OD_RAW_DIR)]
    )
    if not (list(OD_RAW_DIR.glob("*.shp")) + list(OD_RAW_DIR.glob("*.gpkg"))):
        _download_mitma_zonificacion_distritos_shapefile()
    return result


def download_mitma(city: CityConfig) -> None:
    download_mitma_zones()
    for month in city.mitma_months:
        download_mitma_month(city, month)


def run_simplify_osm(city: CityConfig, force_clip: bool = False) -> subprocess.CompletedProcess:
    args = [
        "--city", city.name,
        "--pbf", str(city.raw_osm_path),
        "--data-dir", str(DATA_DIR),
    ]
    if force_clip:
        args.append("--force-clip")
    return _run(_nomad_python(), SIMPLIFY_OSM_SCRIPT, args)


def _import_build_od():
    """In-process import (not subprocess) so a Python callable (mode_choice_fn)
    can be threaded through -- build_od.py's own argparse CLI has no way to
    accept a callable. Module has no import-time side effects tied to a
    specific --data-dir (checked: only constants at module level), so a single
    cached import is safe to reuse across cities/calls."""
    build_od_dir = str(BUILD_OD_SCRIPT.parent)
    if build_od_dir not in sys.path:
        sys.path.insert(0, build_od_dir)
    import build_od
    return build_od


def run_build_od(city: CityConfig, scale: float = 1.0) -> None:
    from green_mobility.demand.mode_choice import CITY_TRANSIT_SHARE, ModeChoiceModel

    build_od = _import_build_od()
    argv = [
        "--city", city.name,
        "--data-dir", str(DATA_DIR),
        "--noise-sigma", str(city.demand_noise_sigma),
        "--occupancy-factor", str(city.demand_occupancy_factor),
        "--country", city.country,
        "--scale", str(scale),
        # city.mitma_months is this project's own source of truth for which
        # MITMA months belong to this city -- without --months, build_od.py's
        # glob would silently mix in any other month present under
        # data/od_raw/ (e.g. a July extract downloaded for another purpose).
        "--months", ",".join(city.mitma_months),
    ]
    # Real, city-specific transit share (Level 2 OD calibration) for the 8
    # cities where CITY_TRANSIT_SHARE has a sourced value -- overrides
    # build_od.py's flat national DISTANCE_MODE_FRACTIONS transit fraction.
    # .get() returns None for cities without one, preserving today's
    # flat-table behavior for them unchanged.
    build_od.main(argv, mode_choice_fn=ModeChoiceModel(city.slug).predict_proba,
                   transit_frac_override=CITY_TRANSIT_SHARE.get(city.slug))
