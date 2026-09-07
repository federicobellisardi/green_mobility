"""Functional Urban Area (FUA) boundary handling.

NOMAD's own preprocessing (external/nomad/python/preprocessing/simplify_osm.py
and build_od.py, function find_fua()) requires data/fua/boundaries.gpkg with a
layer named "FUAs" and a "fuaname" column, and does not ship it. We do not
guess a download URL for it here (it is not something this assistant can
verify is current/correct) — the user must obtain it once from the JRC/OECD
"Functional Urban Areas" dataset (GHSL / OECD Metropolitan Areas programme,
the standard open source for FUA polygons in the EU) and place it at
data/fua/boundaries.gpkg. This module only validates it's usable before the
expensive network/demand steps run, and fails with actionable suggestions
(mirroring NOMAD's own find_fua() error path) rather than a bare traceback.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

from green_mobility.config import DATA_DIR, CityConfig

BOUNDARIES_PATH = DATA_DIR / "fua" / "boundaries.gpkg"
BOUNDARIES_LAYER = "FUAs"
FUA_NAME_COLUMN = "fuaname"


class BoundariesError(RuntimeError):
    pass


@dataclass(frozen=True)
class FuaMatch:
    city_slug: str
    query_name: str
    matched_fuaname: str | None
    suggestions: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.matched_fuaname is not None


def _missing_file_error() -> BoundariesError:
    return BoundariesError(
        f"{BOUNDARIES_PATH} not found.\n\n"
        "This repo does not ship or download a Functional Urban Area boundary "
        "file automatically. Obtain the JRC/OECD 'Functional Urban Areas' "
        "dataset (GHSL / OECD Metropolitan Areas programme) yourself, and "
        "save/convert it to a GeoPackage at that exact path with:\n"
        f"  - a layer named '{BOUNDARIES_LAYER}'\n"
        f"  - a '{FUA_NAME_COLUMN}' column identifying each FUA by name\n"
        "This is the same file external/nomad's own simplify_osm.py / "
        "build_od.py (find_fua()) already expect — no NOMAD change needed."
    )


def check_boundaries_file() -> Path:
    if not BOUNDARIES_PATH.exists():
        raise _missing_file_error()
    return BOUNDARIES_PATH


def match_city(city: CityConfig, fua_names: list[str]) -> FuaMatch:
    lo = city.name.lower().strip()
    lower_names = {n.lower(): n for n in fua_names}
    if lo in lower_names:
        return FuaMatch(city.slug, city.name, lower_names[lo], ())
    contains = [orig for low, orig in lower_names.items() if lo in low]
    if contains:
        return FuaMatch(city.slug, city.name, contains[0], ())
    suggestions = tuple(get_close_matches(lo, list(lower_names.keys()), n=5, cutoff=0.4))
    return FuaMatch(city.slug, city.name, None, suggestions)


def validate_cities(cities: list[CityConfig]) -> list[FuaMatch]:
    """Raises BoundariesError (with per-city suggestions) if any configured
    city name can't be matched in boundaries.gpkg; otherwise returns the
    successful matches for logging/manifest purposes."""
    import geopandas as gpd

    path = check_boundaries_file()
    fuas = gpd.read_file(path, layer=BOUNDARIES_LAYER)
    if FUA_NAME_COLUMN not in fuas.columns:
        raise BoundariesError(
            f"{path} layer '{BOUNDARIES_LAYER}' has no '{FUA_NAME_COLUMN}' column "
            f"(found: {list(fuas.columns)})"
        )
    fua_names = fuas[FUA_NAME_COLUMN].astype(str).tolist()

    matches = [match_city(c, fua_names) for c in cities]
    failed = [m for m in matches if not m.ok]
    if failed:
        lines = [
            f"  - '{m.query_name}' ({m.city_slug}): no match. Suggestions: {list(m.suggestions)}"
            for m in failed
        ]
        raise BoundariesError(
            f"{len(failed)} configured city name(s) not found in {path} "
            f"layer '{BOUNDARIES_LAYER}' column '{FUA_NAME_COLUMN}':\n" + "\n".join(lines)
        )
    return matches
