"""Download each city's regional OSM extract from Geofabrik.

NOMAD ships its own download_osm.py, but it is hardcoded to a single region
(Islas Baleares, for Palma) — not reusable across our 4 cities' different
autonomous communities. This is a ~20-line generic replacement for *that one
script only*; the actual network build (clipping to the FUA, graph
simplification) still goes through NOMAD's own simplify_osm.py unchanged
(see nomad_wrapper/build.py) — no simulation-engine logic is duplicated here.
"""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from green_mobility.config import CityConfig


def download_region_osm(city: CityConfig, force: bool = False) -> Path:
    dest = city.raw_osm_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(city.geofabrik_url) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    tmp.replace(dest)
    return dest
